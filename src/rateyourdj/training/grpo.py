"""GRPO (Group-Relative Policy Optimization) on agent trajectories.

GRPO improves a policy using the *relative* reward of multiple responses to the
same prompt, where reward comes from real user feedback (play/like/skip/save…).
This module wires the ``{prompt, responses, rewards}`` groups produced by
``dataset.build_grpo_file`` into ``trl``'s ``GRPOTrainer``.

As with SFT, heavy deps (torch, transformers, trl, peft, datasets) are imported
lazily so the data-prep path stays dependency-free. Install with::

    pip install "rateyourdj[training]"

Reward design: each trajectory already has a scalar reward in [-1, 1] derived
from production feedback (L5). We reuse a *precomputed* reward table keyed by the
response text, so the GRPO reward is exactly the feedback the live system
recorded — the policy is pushed toward responses real users rewarded. (When you
later sample fresh responses from the policy online, swap this table-backed
reward for a live scorer.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


_MISSING_DEPS_HINT = (
    "GRPO training requires the optional training dependencies. Install them "
    'with:  pip install "rateyourdj[training]"  (torch, transformers, trl, '
    "peft, datasets). The data-prep step (build-grpo) runs without them."
)


@dataclass(slots=True)
class GRPOConfig:
    train_file: str
    output_dir: str = "data/training/grpo-model"
    base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    epochs: float = 1.0
    learning_rate: float = 1e-5
    per_device_batch_size: int = 1
    gradient_accumulation_steps: int = 8
    num_generations: int = 4
    max_prompt_length: int = 1024
    max_completion_length: int = 512
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
            "num_generations": self.num_generations,
            "max_prompt_length": self.max_prompt_length,
            "max_completion_length": self.max_completion_length,
            "seed": self.seed,
        }


def _require_training_deps() -> dict[str, Any]:
    try:
        import torch  # noqa: F401
        from datasets import Dataset
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import GRPOConfig as TRLGRPOConfig
        from trl import GRPOTrainer
    except ImportError as error:  # pragma: no cover - depends on optional deps
        raise RuntimeError(_MISSING_DEPS_HINT) from error
    return {
        "Dataset": Dataset,
        "AutoModelForCausalLM": AutoModelForCausalLM,
        "AutoTokenizer": AutoTokenizer,
        "TRLGRPOConfig": TRLGRPOConfig,
        "GRPOTrainer": GRPOTrainer,
    }


def build_reward_table(groups: list[dict[str, Any]]) -> dict[str, float]:
    """Map response text -> recorded reward, from GRPO group samples."""
    table: dict[str, float] = {}
    for group in groups:
        responses = group.get("responses", [])
        rewards = group.get("rewards", [])
        for response, reward in zip(responses, rewards):
            if isinstance(response, str) and _is_number(reward):
                # Keep the strongest signal if a response repeats.
                table[response] = max(table.get(response, float(reward)), float(reward))
    return table


def run_grpo(config: GRPOConfig) -> dict[str, Any]:
    """Run a GRPO training loop. Requires the [training] extras + a GPU."""
    if not Path(config.train_file).exists():
        raise FileNotFoundError(
            f"GRPO train file not found: {config.train_file}. "
            "Build it first with: rateyourdj-train build-grpo"
        )
    from .dataset import load_jsonl

    groups = load_jsonl(config.train_file)
    if not groups:
        raise ValueError(
            "GRPO train file has no groups; collect more feedback-bearing "
            "trajectories or lower --min-group-size when building."
        )
    deps = _require_training_deps()

    reward_table = build_reward_table(groups)
    prompts = [{"prompt": group["prompt"]} for group in groups if group.get("prompt")]
    dataset = deps["Dataset"].from_list(prompts)

    tokenizer = deps["AutoTokenizer"].from_pretrained(config.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = deps["AutoModelForCausalLM"].from_pretrained(config.base_model)

    def reward_fn(prompts, completions, **_kwargs):
        # Table-backed reward: reuse the production feedback reward when the
        # generated completion matches a recorded response; neutral otherwise.
        return [float(reward_table.get(completion, 0.0)) for completion in completions]

    training_args = deps["TRLGRPOConfig"](
        output_dir=config.output_dir,
        num_train_epochs=config.epochs,
        learning_rate=config.learning_rate,
        per_device_train_batch_size=config.per_device_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        num_generations=config.num_generations,
        max_prompt_length=config.max_prompt_length,
        max_completion_length=config.max_completion_length,
        seed=config.seed,
        logging_steps=10,
        save_strategy="epoch",
    )
    trainer = deps["GRPOTrainer"](
        model=model,
        args=training_args,
        train_dataset=dataset,
        reward_funcs=reward_fn,
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(config.output_dir)
    tokenizer.save_pretrained(config.output_dir)
    return {
        "status": "completed",
        "output_dir": config.output_dir,
        "prompt_groups": len(prompts),
        "config": config.to_dict(),
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
