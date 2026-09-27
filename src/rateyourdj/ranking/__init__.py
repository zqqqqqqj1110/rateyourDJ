"""Stage 3: deterministic ranking inside a candidate set + selection validator."""

from .ranker import STRATEGIES, WEIGHTS_VERSION, rank_candidates, slot_plan
from .validator import validate_selection

__all__ = ["STRATEGIES", "WEIGHTS_VERSION", "rank_candidates", "slot_plan", "validate_selection"]
