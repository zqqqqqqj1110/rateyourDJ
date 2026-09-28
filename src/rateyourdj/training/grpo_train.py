"""GRPO on the selection step, starting from the stage 4 SFT model (stage 5).

    merge   fold the SFT LoRA adapter into the base weights -> the GRPO starting point and the
            KL reference (with a fresh LoRA on top, "adapter disabled" = the SFT model)
    train   TRL GRPOTrainer: per prompt, sample ``num_generations`` completions (vLLM, colocated),
            score them with reward-v1 (training/grpo_reward.py) and update the new LoRA

Prompts are rendered with the model's own chat template (system + tools + user + the executed
retrieval), so the policy writes exactly what the ReAct loop expects next: the
``<tool_call>{"name": "submit_recommendations", ...}</tool_call>`` turn. ``eval_only`` never
reaches the trainer (training_view). Every scored completion is appended to
``<output>/rewards.jsonl`` (reward, components, validity, length) for monitoring.

Heavy dependencies are imported lazily:  pip install "trl>=0.23,<0.26" (see scripts/gpu_grpo.sh).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .grpo_data import load_jsonl, training_view
from .grpo_reward import REWARD_VERSION, score


@dataclass
class GRPORunConfig:
    data_dir: str = "data/training/grpo/grpo-v1"
    sft_model: str = "/root/autodl-tmp/models/rateyourdj-sft-v1"   # merged SFT model
    output_dir: str = "runs/grpo-v1"
    limit_train: int | None = None
    num_generations: int = 8
    temperature: float = 1.0
    max_completion_length: int = 1400
    max_prompt_length: int = 12288
    learning_rate: float = 5e-6
    beta: float = 0.02
    epochs: float = 1.0
    per_device_batch: int = 2
    grad_accum: int = 4
    lora_r: int = 16
    lora_alpha: int = 32
    save_steps: int = 50
    use_vllm: bool = True
    vllm_gpu_memory_utilization: float = 0.35
    seed: int = 20260928


def render_prompts(tokenizer: Any, samples: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for s in samples:
        view = training_view(s)
        text = tokenizer.apply_chat_template(view["prompt"], tools=view["tools"], tokenize=False,
                                             add_generation_prompt=True)
        rows.append({"prompt": text, "sample_id": s["sample_id"]})
    return rows


def merge_adapter(base: str, adapter: str, out: str) -> None:  # pragma: no cover - needs weights
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16)
    model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
    model.save_pretrained(out, safe_serialization=True)
    AutoTokenizer.from_pretrained(adapter).save_pretrained(out)
    print(f"merged {adapter} into {base} -> {out}")


def run(cfg: GRPORunConfig) -> dict[str, Any]:  # pragma: no cover - needs a GPU
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoTokenizer, TrainerCallback
    from trl import GRPOConfig, GRPOTrainer

    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    samples = load_jsonl(Path(cfg.data_dir) / "train.jsonl", cfg.limit_train)
    by_id = {s["sample_id"]: training_view(s) for s in samples}
    tokenizer = AutoTokenizer.from_pretrained(cfg.sft_model)
    dataset = Dataset.from_list(render_prompts(tokenizer, samples))
    log_path = out / "rewards.jsonl"
    state = {"step": 0}

    def rateyourdj_reward(prompts, completions, sample_id, **kwargs):
        rewards = []
        with log_path.open("a", encoding="utf-8") as log:
            for text, sid in zip(completions, sample_id):
                text = text if isinstance(text, str) else (text[0].get("content") or "")
                r = score(text, by_id[sid])
                rewards.append(float(r["reward"]))
                log.write(json.dumps({"step": state["step"], "sample_id": sid, "reward": r["reward"],
                                      "valid": r["valid"], "error": r.get("error"),
                                      "components": r.get("components"), "tails": r.get("tails"),
                                      "chars": len(text)}, ensure_ascii=False) + "\n")
        return rewards

    history: list[dict[str, Any]] = []

    class Log(TrainerCallback):
        def on_log(self, args, st, control, logs=None, **kw):
            state["step"] = st.global_step
            if logs:
                history.append({"step": st.global_step, **logs})

    extra: dict[str, Any] = {}
    if cfg.use_vllm:
        extra = {"use_vllm": True, "vllm_mode": "colocate",
                 "vllm_gpu_memory_utilization": cfg.vllm_gpu_memory_utilization,
                 "vllm_enable_sleep_mode": True}
    args = GRPOConfig(
        output_dir=str(out / "checkpoints"), num_train_epochs=cfg.epochs, learning_rate=cfg.learning_rate,
        per_device_train_batch_size=cfg.per_device_batch, gradient_accumulation_steps=cfg.grad_accum,
        num_generations=cfg.num_generations, temperature=cfg.temperature,
        max_prompt_length=cfg.max_prompt_length, max_completion_length=cfg.max_completion_length,
        beta=cfg.beta, bf16=True, gradient_checkpointing=True, logging_steps=1, save_steps=cfg.save_steps,
        save_total_limit=None, report_to=[], seed=cfg.seed, log_completions=False,
        model_init_kwargs={"torch_dtype": "bfloat16"}, **extra)
    trainer = GRPOTrainer(
        model=cfg.sft_model, reward_funcs=[rateyourdj_reward], args=args, train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=LoraConfig(r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=0.0, task_type="CAUSAL_LM",
                               target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj",
                                               "down_proj"]),
        callbacks=[Log()])
    started = time.time()
    trainer.train()
    trainer.save_model(str(out / "adapter"))
    tokenizer.save_pretrained(str(out / "adapter"))
    (out / "log_history.json").write_text(json.dumps(history, indent=1) + "\n", "utf-8")
    return {"train_prompts": len(samples), "steps": trainer.state.global_step,
            "minutes": round((time.time() - started) / 60, 1),
            "peak_gpu_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1),
            "reward_version": REWARD_VERSION}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m rateyourdj.training.grpo_train")
    sub = parser.add_subparsers(dest="command", required=True)
    mg = sub.add_parser("merge")
    mg.add_argument("--base", required=True)
    mg.add_argument("--adapter", required=True)
    mg.add_argument("--out", required=True)
    tr = sub.add_parser("train")
    for name, value in asdict(GRPORunConfig()).items():
        flag = "--" + name.replace("_", "-")
        if isinstance(value, bool):
            tr.add_argument(flag, type=lambda x: x.lower() in ("1", "true", "yes"), default=value)
        elif value is None:
            tr.add_argument(flag, type=int, default=None)
        else:
            tr.add_argument(flag, type=type(value), default=value)
    args = parser.parse_args(argv)
    if args.command == "merge":
        merge_adapter(args.base, args.adapter, args.out)
        return 0
    cfg = GRPORunConfig(**{k: v for k, v in vars(args).items() if k != "command"})
    from rateyourdj.experiment import build_run_manifest, write_run_manifest
    result = run(cfg)
    write_run_manifest(Path(cfg.output_dir), build_run_manifest(
        Path(cfg.output_dir).name, kind="grpo", config=asdict(cfg), seed=cfg.seed,
        data_files=[str(Path(cfg.data_dir) / f) for f in ("train.jsonl", "manifest.json")],
        model={"start": cfg.sft_model, "adapter": str(Path(cfg.output_dir) / "adapter")}, metrics=result))
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
