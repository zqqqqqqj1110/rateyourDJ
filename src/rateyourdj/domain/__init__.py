"""Domain-level capabilities shared by the agent layer.

Explanation generation (human-readable reasons with evidence). The generative
discovery path (LLM nominates songs, providers ground them) was removed in
stage 3; recommendations now come only from the catalog via rateyourdj.rag /
rateyourdj.agent.
"""

from .explanations import (
    Evidence,
    ExplanationGenerator,
    Reason,
    TrackExplanation,
)

__all__ = [
    "Evidence",
    "ExplanationGenerator",
    "Reason",
    "TrackExplanation",
]
