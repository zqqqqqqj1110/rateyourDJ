"""LEGACY (pre-stage 5) GRPO entry point - kept only so old imports fail loudly.

The stage 5 GRPO lives in grpo_data.py / grpo_reward.py / grpo_train.py / grpo_eval.py.

GRPO (Group-Relative Policy Optimization) on agent trajectories.

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


def run_grpo(config: GRPOConfig) -> dict[str, Any]:
    """Removed in stage 5: the old trainer rewarded a completion only when its text exactly matched
    a recorded response (a lookup table), so no freshly sampled completion could ever be rewarded.
    Use the programmatic reward (training/grpo_reward.py) and trainer (training/grpo_train.py)."""
    raise RuntimeError("legacy lookup-table GRPO was removed; use "
                       "python -m rateyourdj.training.grpo_train (see stage-5.md)")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
