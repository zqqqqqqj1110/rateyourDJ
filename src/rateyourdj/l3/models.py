from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SCORE_WEIGHTS = {
    "track_tags": 0.55,
    "genres": 0.25,
    "artist_tags": 0.15,
    "release_year": 0.05,
}

COLLECTION_SCORE_WEIGHTS = {
    "best_seed": 0.7,
    "top_seed_average": 0.3,
}

TOP_SEED_COUNT = 5


@dataclass(slots=True)
class RetrievalCandidate:
    candidate_song_id: str
    best_seed_song_id: str
    matched_seed_song_ids: list[str]
    best_seed_score: float
    top_seed_average_score: float
    similarity_score: float
    score_breakdown: dict[str, float]
    retrieval_sources: list[str] = field(
        default_factory=lambda: ["local_candidate_library"]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_song_id": self.candidate_song_id,
            "best_seed_song_id": self.best_seed_song_id,
            "matched_seed_song_ids": list(self.matched_seed_song_ids),
            "best_seed_score": self.best_seed_score,
            "top_seed_average_score": self.top_seed_average_score,
            "similarity_score": self.similarity_score,
            "score_breakdown": dict(self.score_breakdown),
            "retrieval_sources": list(self.retrieval_sources),
        }


@dataclass(slots=True)
class RetrievalResult:
    user_id: str
    seed_song_ids: list[str]
    missing_seed_song_ids: list[str]
    candidates: list[RetrievalCandidate]

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "seed_song_ids": list(self.seed_song_ids),
            "missing_seed_song_ids": list(self.missing_seed_song_ids),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def retrieval_schema() -> dict[str, Any]:
    return {
        "candidate_song_id": "string",
        "best_seed_song_id": "string with the highest pairwise similarity",
        "matched_seed_song_ids": ["string with similarity_score > 0"],
        "best_seed_score": "highest pairwise similarity",
        "top_seed_average_score": f"average of the top {TOP_SEED_COUNT} seed scores",
        "similarity_score": (
            f"{COLLECTION_SCORE_WEIGHTS['best_seed']} * best_seed_score + "
            f"{COLLECTION_SCORE_WEIGHTS['top_seed_average']} * "
            "top_seed_average_score"
        ),
        "score_breakdown": {
            field_name: (
                f"collection-level weighted contribution (feature weight={weight})"
            )
            for field_name, weight in SCORE_WEIGHTS.items()
        },
        "retrieval_sources": ["local_candidate_library | lastfm_similar_tracks"],
    }
