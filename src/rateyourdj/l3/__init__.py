"""L3 local candidate retrieval and collection filtering."""

from .models import (
    COLLECTION_SCORE_WEIGHTS,
    SCORE_WEIGHTS,
    TOP_SEED_COUNT,
    RetrievalCandidate,
    RetrievalResult,
    retrieval_schema,
)
from .service import CandidateRetrievalService
from .similarity import (
    release_year_similarity,
    score_song_pair,
    weighted_jaccard,
)
from .tools import retrieve_candidates

__all__ = [
    "SCORE_WEIGHTS",
    "COLLECTION_SCORE_WEIGHTS",
    "TOP_SEED_COUNT",
    "CandidateRetrievalService",
    "RetrievalCandidate",
    "RetrievalResult",
    "release_year_similarity",
    "retrieval_schema",
    "retrieve_candidates",
    "score_song_pair",
    "weighted_jaccard",
]
