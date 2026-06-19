from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_BASE_SCORE_WEIGHTS = {
    "retrieval": 0.50,
    "artist_preference": 0.08,
    "genre_preference": 0.14,
    "tag_preference": 0.18,
    "quality": 0.10,
}

DEFAULT_DIVERSITY_PENALTY_WEIGHT = 0.15
DEFAULT_FEEDBACK_ADJUSTMENT_WEIGHT = 0.15

DEFAULT_DIVERSITY_SIMILARITY_WEIGHTS = {
    "artist": 0.20,
    "genres": 0.40,
    "tags": 0.40,
}

BASE_SCORE_WEIGHTS = dict(DEFAULT_BASE_SCORE_WEIGHTS)
DIVERSITY_PENALTY_WEIGHT = DEFAULT_DIVERSITY_PENALTY_WEIGHT
FEEDBACK_ADJUSTMENT_WEIGHT = DEFAULT_FEEDBACK_ADJUSTMENT_WEIGHT
DIVERSITY_SIMILARITY_WEIGHTS = dict(DEFAULT_DIVERSITY_SIMILARITY_WEIGHTS)


@dataclass(frozen=True, slots=True)
class RankingWeights:
    base_score_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_BASE_SCORE_WEIGHTS)
    )
    diversity_penalty_weight: float = DEFAULT_DIVERSITY_PENALTY_WEIGHT
    feedback_adjustment_weight: float = DEFAULT_FEEDBACK_ADJUSTMENT_WEIGHT
    diversity_similarity_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_DIVERSITY_SIMILARITY_WEIGHTS)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_score_weights": dict(self.base_score_weights),
            "diversity_penalty_weight": self.diversity_penalty_weight,
            "feedback_adjustment_weight": self.feedback_adjustment_weight,
            "diversity_similarity_weights": dict(
                self.diversity_similarity_weights
            ),
        }


@dataclass(slots=True)
class RankedSong:
    rank: int
    song_id: str
    title: str | None
    artist: str | None
    final_score: float
    base_score: float
    score_breakdown: dict[str, float]
    diversity_penalty: float
    ranking_reasons: list[str]
    best_seed_song_id: str
    retrieval_sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "song_id": self.song_id,
            "title": self.title,
            "artist": self.artist,
            "final_score": self.final_score,
            "base_score": self.base_score,
            "score_breakdown": dict(self.score_breakdown),
            "diversity_penalty": self.diversity_penalty,
            "ranking_reasons": list(self.ranking_reasons),
            "best_seed_song_id": self.best_seed_song_id,
            "retrieval_sources": list(self.retrieval_sources),
        }


@dataclass(slots=True)
class RankingResult:
    user_id: str
    seed_song_ids: list[str]
    missing_seed_song_ids: list[str]
    missing_candidate_song_ids: list[str]
    ranked_songs: list[RankedSong]

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "seed_song_ids": list(self.seed_song_ids),
            "missing_seed_song_ids": list(self.missing_seed_song_ids),
            "missing_candidate_song_ids": list(
                self.missing_candidate_song_ids
            ),
            "ranked_songs": [song.to_dict() for song in self.ranked_songs],
        }


def ranking_schema() -> dict[str, Any]:
    return {
        "rank": "integer starting at 1",
        "song_id": "L2 song_id",
        "title": "string | null",
        "artist": "string | null",
        "final_score": "base_score - diversity_penalty, clamped to [0, 1]",
        "base_score": "sum of score_breakdown",
        "score_breakdown": {
            name: f"weighted contribution (weight={weight})"
            for name, weight in BASE_SCORE_WEIGHTS.items()
        }
        | {
            "feedback_adjustment": (
                "signed L5 feedback contribution "
                f"(weight={FEEDBACK_ADJUSTMENT_WEIGHT})"
            )
        },
        "diversity_penalty": (
            "similarity to already selected songs * "
            f"{DIVERSITY_PENALTY_WEIGHT}"
        ),
        "ranking_reasons": ["human-readable scoring reason"],
        "best_seed_song_id": "L3 best matching collection song",
        "retrieval_sources": ["L3 retrieval source"],
    }
