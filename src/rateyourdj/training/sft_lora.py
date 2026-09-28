"""LoRA SFT on sft-sample/v2 chat/tool-call data (stage 4).

  stats   tokenize a dataset with the model's chat template: lengths, assistant share
  train   bf16 LoRA fine-tuning; loss only on assistant turns with weight != 0

Masking: every assistant turn's text is located by rendering the conversation prefix
with the tokenizer's own chat template (``messages[:i]`` + generation prompt vs.
``messages[:i+1]``); tokens inside turns with ``weight: 0`` or outside assistant turns
get label -100. The assistant turn includes its end-of-turn token, so the model learns
to stop. Samples longer than ``max_len`` are dropped, never truncated (truncation would
cut off the final submission).

Memory: logits are computed only at supervised positions (a 12k-token sample would
otherwise need a 12k x 151k fp32 logits tensor).

Heavy dependencies (torch / transformers / peft) are imported lazily:
    pip install -e ".[training]"
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


# ---------------------------------------------------------------- data -> tokens
def load_samples(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _clean(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop training-only keys before the chat template sees the messages."""
    return [{k: v for k, v in m.items() if k != "weight"} for m in messages]


def _render(tokenizer: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
            add_generation_prompt: bool) -> str:
    return tokenizer.apply_chat_template(_clean(messages), tools=tools, tokenize=False,
                                         add_generation_prompt=add_generation_prompt)


def assistant_char_spans(tokenizer: Any, sample: dict[str, Any]) -> tuple[str, list[tuple[int, int, float]]]:
    """Full rendered text and (start, end, weight) character spans of every assistant turn."""
    messages, tools = sample["messages"], sample.get("tools")
    full = _render(tokenizer, messages, tools, False)
    spans = []
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        before = _render(tokenizer, messages[:i], tools, True)
        through = _render(tokenizer, messages[: i + 1], tools, False)
        if not through.startswith(before) or not full.startswith(through):
            raise ValueError("chat template is not prefix-consistent; cannot locate assistant turn "
                             f"{i} of {sample.get('sample_id')}")
        spans.append((len(before), len(through), float(m.get("weight", 1.0))))
    return full, spans


def tokenize_sample(tokenizer: Any, sample: dict[str, Any], max_len: int | None = None) -> dict[str, Any] | None:
    """input_ids + labels (-100 outside weighted assistant turns). None when longer than max_len."""
    text, spans = assistant_char_spans(tokenizer, sample)
    enc = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = enc["input_ids"], enc["offset_mapping"]
    if max_len is not None and len(ids) > max_len:
        return None
    labels = [-100] * len(ids)
    for t, (a, b) in enumerate(offsets):
        for start, end, weight in spans:
            if weight > 0 and start <= a and b <= end and b > a:
                labels[t] = ids[t]
                break
    return {"input_ids": ids, "labels": labels, "n_tokens": len(ids),
            "n_label_tokens": sum(x != -100 for x in labels)}


def token_stats(tokenizer: Any, samples: list[dict[str, Any]]) -> dict[str, Any]:
    lengths, label_counts = [], []
    for s in samples:
        tok = tokenize_sample(tokenizer, s)
        lengths.append(tok["n_tokens"])
        label_counts.append(tok["n_label_tokens"])
    lengths_sorted = sorted(lengths)

    def pct(q: float) -> int:
        return lengths_sorted[min(len(lengths_sorted) - 1, int(q * len(lengths_sorted)))]
    return {"n": len(samples), "tokens": {"p50": pct(0.5), "p90": pct(0.9), "p95": pct(0.95), "p99": pct(0.99),
                                          "max": lengths_sorted[-1], "mean": round(sum(lengths) / len(lengths))},
            "supervised_tokens": {"mean": round(sum(label_counts) / len(label_counts)),
                                  "share": round(sum(label_counts) / sum(lengths), 4)},
            "total_tokens": sum(lengths)}


# ---------------------------------------------------------------- training
@dataclass
class LoRAConfig:
    data_dir: str = "data/training/sft/sft-v1-smoke"
    output_dir: str = "runs/sft-v1-smoke"
    base_model: str = DEFAULT_MODEL
    max_len: int = 12288
    epochs: float = 2.0
    learning_rate: float = 1e-4
    batch_size: int = 1
    grad_accum: int = 8
    warmup_ratio: float = 0.05
    lora_r: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.05
    target_modules: list[str] = field(default_factory=lambda: list(LORA_TARGETS))
    eval_steps: int = 25
    seed: int = 20260927
    gradient_checkpointing: bool = True
    limit_train: int | None = None
    limit_val: int | None = 200


def _deps() -> dict[str, Any]:
    try:
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer, TrainerCallback,
                                  TrainingArguments)
    except ImportError as error:  # pragma: no cover - optional deps
        raise RuntimeError('需要训练依赖：pip install -e ".[training]"（torch、transformers、peft）') from error
    return {"torch": torch, "LoraConfig": LoraConfig, "get_peft_model": get_peft_model,
            "AutoModelForCausalLM": AutoModelForCausalLM, "AutoTokenizer": AutoTokenizer,
            "Trainer": Trainer, "TrainerCallback": TrainerCallback, "TrainingArguments": TrainingArguments}


def _tokenized(tokenizer: Any, path: Path, max_len: int, limit: int | None = None) -> tuple[list[dict], int]:
    samples = load_samples(path)[: limit or None]
    out, dropped = [], 0
    for s in samples:
        tok = tokenize_sample(tokenizer, s, max_len)
        if tok is None or tok["n_label_tokens"] == 0:
            dropped += 1
            continue
        out.append({"input_ids": tok["input_ids"], "labels": tok["labels"]})
    return out, dropped


def run_lora_sft(cfg: LoRAConfig) -> dict[str, Any]:  # pragma: no cover - needs a GPU
    d = _deps()
    torch = d["torch"]
    torch.manual_seed(cfg.seed)
    data_dir, out_dir = Path(cfg.data_dir), Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = d["AutoTokenizer"].from_pretrained(cfg.base_model)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    train, dropped_train = _tokenized(tokenizer, data_dir / "train.jsonl", cfg.max_len, cfg.limit_train)
    val, dropped_val = _tokenized(tokenizer, data_dir / "val.jsonl", cfg.max_len, cfg.limit_val)
    print(f"train {len(train)} (dropped {dropped_train} > {cfg.max_len} tokens), val {len(val)} (dropped {dropped_val})")

    model = d["AutoModelForCausalLM"].from_pretrained(cfg.base_model, torch_dtype=torch.bfloat16,
                                                      attn_implementation="sdpa")
    if cfg.gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    model.config.use_cache = False
    model = d["get_peft_model"](model, d["LoraConfig"](
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=cfg.target_modules, task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    pad = tokenizer.pad_token_id

    def collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
        n = max(len(b["input_ids"]) for b in batch)
        ids = torch.full((len(batch), n), pad, dtype=torch.long)
        labels = torch.full((len(batch), n), -100, dtype=torch.long)
        mask = torch.zeros((len(batch), n), dtype=torch.long)
        for i, b in enumerate(batch):
            k = len(b["input_ids"])
            ids[i, :k] = torch.tensor(b["input_ids"])
            labels[i, :k] = torch.tensor(b["labels"])
            mask[i, :k] = 1
        return {"input_ids": ids, "labels": labels, "attention_mask": mask}

    class SupervisedOnlyLoss(d["Trainer"]):
        """Cross-entropy computed from the hidden states at supervised positions only."""

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            labels = inputs.pop("labels")
            inner = model.get_base_model()
            hidden = inner.model(input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"]
                                 ).last_hidden_state
            target = labels[:, 1:]
            keep = target != -100
            h = hidden[:, :-1][keep]
            logits = inner.lm_head(h).float()
            loss = torch.nn.functional.cross_entropy(logits, target[keep], reduction="sum")
            denom = num_items_in_batch if num_items_in_batch is not None else keep.sum()
            loss = loss / max(1, int(denom))
            return (loss, {"loss": loss}) if return_outputs else loss

    history: list[dict[str, Any]] = []

    class Log(d["TrainerCallback"]):
        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs:
                history.append({"step": state.global_step, **logs})

    args = d["TrainingArguments"](
        output_dir=str(out_dir / "checkpoints"), num_train_epochs=cfg.epochs, learning_rate=cfg.learning_rate,
        per_device_train_batch_size=cfg.batch_size, per_device_eval_batch_size=1,
        gradient_accumulation_steps=cfg.grad_accum, warmup_ratio=cfg.warmup_ratio, lr_scheduler_type="cosine",
        bf16=True, logging_steps=5, eval_strategy="steps", eval_steps=cfg.eval_steps,
        save_strategy="steps", save_steps=cfg.eval_steps, save_total_limit=2, load_best_model_at_end=True,
        metric_for_best_model="eval_loss", greater_is_better=False, report_to=[], seed=cfg.seed,
        remove_unused_columns=False, dataloader_num_workers=2, group_by_length=False,
        label_names=["labels"], prediction_loss_only=True)
    trainer = SupervisedOnlyLoss(model=model, args=args, train_dataset=train, eval_dataset=val,
                                 data_collator=collate, callbacks=[Log()])
    # our loss is already normalised by the supervised tokens of the whole accumulated batch
    trainer.model_accepts_loss_kwargs = True
    started = time.time()
    trainer.train()
    final_eval = trainer.evaluate()
    adapter_dir = out_dir / "adapter"
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    (out_dir / "log_history.json").write_text(json.dumps(history, indent=1) + "\n", "utf-8")
    result = {"train_samples": len(train), "val_samples": len(val), "dropped": {"train": dropped_train,
              "val": dropped_val}, "final_eval_loss": final_eval.get("eval_loss"),
              "best_checkpoint": trainer.state.best_model_checkpoint, "minutes": round((time.time() - started) / 60, 1),
              "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1) if torch.cuda.is_available() else None}
    return result


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m rateyourdj.training.sft_lora")
    sub = parser.add_subparsers(dest="command", required=True)
    st = sub.add_parser("stats", help="token lengths with the model's chat template (CPU only)")
    st.add_argument("--data", default="data/training/sft/sft-v1-smoke")
    st.add_argument("--model", default=DEFAULT_MODEL)
    tr = sub.add_parser("train", help="LoRA SFT (GPU)")
    defaults = LoRAConfig()
    for name, value in asdict(defaults).items():
        if name == "target_modules":
            continue
        flag = "--" + name.replace("_", "-")
        if isinstance(value, bool):
            tr.add_argument(flag, type=lambda x: x.lower() in ("1", "true", "yes"), default=value)
        elif value is None:
            tr.add_argument(flag, type=int, default=None)
        else:
            tr.add_argument(flag, type=type(value), default=value)
    args = parser.parse_args(argv)

    if args.command == "stats":
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        report = {}
        for split in ("train", "val", "test"):
            path = Path(args.data) / f"{split}.jsonl"
            if path.is_file():
                report[split] = token_stats(tokenizer, load_samples(path))
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    cfg = LoRAConfig(**{k: v for k, v in vars(args).items() if k != "command"})
    from rateyourdj.experiment import build_run_manifest, write_run_manifest
    result = run_lora_sft(cfg)
    out = Path(cfg.output_dir)
    write_run_manifest(out, build_run_manifest(
        out.name, kind="sft_lora", config=asdict(cfg), seed=cfg.seed,
        data_files=[str(Path(cfg.data_dir) / f) for f in ("train.jsonl", "val.jsonl", "split_manifest.json")],
        model={"base": cfg.base_model, "adapter": str(out / "adapter")}, metrics=result))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
