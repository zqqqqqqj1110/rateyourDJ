"""Model training for the rateyourDJ agent.

Two stages, both fed by the L7 trajectory dataset:

* SFT  — imitate the agent's ReAct behavior (``sft.py``)
* GRPO — optimize the policy with feedback-derived rewards (``grpo.py``)

The data-preparation layer (``dataset.py``) is standard-library only and always
importable. The training loops use optional heavy dependencies (torch /
transformers / trl / peft / datasets) imported lazily; install them with
``pip install "rateyourdj[training]"``.
"""

from .dataset import (
    SampleBuildResult,
    build_grpo_file,
    build_grpo_samples,
    build_sft_file,
    build_sft_samples,
    load_jsonl,
    write_jsonl,
)

__all__ = [
    "SampleBuildResult",
    "build_grpo_file",
    "build_grpo_samples",
    "build_sft_file",
    "build_sft_samples",
    "load_jsonl",
    "write_jsonl",
]
