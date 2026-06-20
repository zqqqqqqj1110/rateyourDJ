"""Supervised fine-tuning (SFT) of a base LLM on agent trajectories.

This trains a model to imitate the rateyourDJ agent's ReAct behavior using the
``{prompt, completion}`` samples produced by ``dataset.build_sft_file``. It uses
LoRA (parameter-efficient fine-tuning) via ``peft`` and ``trl``'s ``SFTTrainer``.

Heavy dependencies (torch, transformers, trl, peft, datasets) are imported
lazily inside ``run_sft`` so that importing this module — and the data-prep code
next to it — never requires a GPU stack. Install them with::

    pip install "rateyourdj[training]"

The defaults target a small base model so the loop is runnable on a single
modest GPU; override via the CLI for larger runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_MISSING_DEPS_HINT = (
    "SFT training requires the optional training dependencies. Install them "
    'with:  pip install "rateyourdj[training]"  (torch, transformers, trl, '
    "peft, datasets). The data-prep step (build-sft) runs without them."
)


@dataclass(slots=True)
class SFTConfig:
    train_file: str
    output_dir: str = "data/training/sft-model"
    base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    epochs: float = 1.0
    learning_rate: float = 2e-4
    per_device_batch_size: int = 1
    gradient_accumulation_steps: int = 8
    max_seq_length: int = 2048
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    seed: int = 20260615

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_file": self.train_file,
            "output_dir": self.output_dir,
            "base_model": self.base_model,
            "epochs": self.epochs,
            "learning_rate": self.learning_rate,
            "per_device_batch_size": self.per_device_batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "max_seq_length": self.max_seq_length,
            "lora_r": self.lora_r,
            "lora_alpha": self.lora_alpha,
            "lora_dropout": self.lora_dropout,
            "seed": self.seed,
        }


def _require_training_deps() -> dict[str, Any]:
    """Import heavy training deps lazily; raise a clear error if missing."""
    try:
        import torch  # noqa: F401
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig as TRLSFTConfig
        from trl import SFTTrainer
    except ImportError as error:  # pragma: no cover - depends on optional deps
        raise RuntimeError(_MISSING_DEPS_HINT) from error
    return {
        "load_dataset": load_dataset,
        "LoraConfig": LoraConfig,
        "AutoModelForCausalLM": AutoModelForCausalLM,
        "AutoTokenizer": AutoTokenizer,
        "TRLSFTConfig": TRLSFTConfig,
        "SFTTrainer": SFTTrainer,
    }


def run_sft(config: SFTConfig) -> dict[str, Any]:
    """Run a LoRA SFT training loop. Requires the [training] extras + a GPU."""
    if not Path(config.train_file).exists():
        raise FileNotFoundError(
            f"SFT train file not found: {config.train_file}. "
            "Build it first with: rateyourdj-train build-sft"
        )
    deps = _require_training_deps()

    dataset = deps["load_dataset"](
        "json", data_files=config.train_file, split="train"
    )

    def _format(example: dict[str, Any]) -> dict[str, str]:
        # trl can train on a single text field; join prompt + completion.
        return {
            "text": f"{example['prompt']}\n\n{example['completion']}"
        }

    dataset = dataset.map(_format)

    tokenizer = deps["AutoTokenizer"].from_pretrained(config.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = deps["AutoModelForCausalLM"].from_pretrained(config.base_model)

    lora = deps["LoraConfig"](
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        task_type="CAUSAL_LM",
    )
    training_args = deps["TRLSFTConfig"](
        output_dir=config.output_dir,
        num_train_epochs=config.epochs,
        learning_rate=config.learning_rate,
        per_device_train_batch_size=config.per_device_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        max_seq_length=config.max_seq_length,
        seed=config.seed,
        logging_steps=10,
        save_strategy="epoch",
    )
    trainer = deps["SFTTrainer"](
        model=model,
        args=training_args,
        train_dataset=dataset,
        peft_config=lora,
        dataset_text_field="text",
        tokenizer=tokenizer,
    )
    trainer.train()
    trainer.save_model(config.output_dir)
    tokenizer.save_pretrained(config.output_dir)
    return {
        "status": "completed",
        "output_dir": config.output_dir,
        "train_samples": len(dataset),
        "config": config.to_dict(),
    }
