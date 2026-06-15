"""L4 preference-aware recommendation ranking and diversification."""

from .models import (
    BASE_SCORE_WEIGHTS,
    DIVERSITY_PENALTY_WEIGHT,
    DIVERSITY_SIMILARITY_WEIGHTS,
    FEEDBACK_ADJUSTMENT_WEIGHT,
    RankedSong,
    RankingResult,
    ranking_schema,
)
from .scoring import (
    artist_preference_score,
    candidate_tags,
    diversity_similarity,
    quality_score,
    ranking_reasons,
    score_candidate,
)
from .service import RecommendationRankingService
from .tools import rank_candidates, rank_candidates_tool

__all__ = [
    "BASE_SCORE_WEIGHTS",
    "DIVERSITY_PENALTY_WEIGHT",
    "DIVERSITY_SIMILARITY_WEIGHTS",
    "FEEDBACK_ADJUSTMENT_WEIGHT",
    "RankedSong",
    "RankingResult",
    "RecommendationRankingService",
    "artist_preference_score",
    "candidate_tags",
    "diversity_similarity",
    "quality_score",
    "rank_candidates",
    "rank_candidates_tool",
    "ranking_reasons",
    "ranking_schema",
    "score_candidate",
]
