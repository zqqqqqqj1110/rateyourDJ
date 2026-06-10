from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rateyourdj.l1 import FEEDBACK_TYPES


REWARD_BY_FEEDBACK_TYPE = {
    "play": 0.1,
    "play_complete": 0.4,
    "skip": -0.4,
    "quick_skip": -0.8,
    "favorite": 0.8,
    "like": 0.6,
    "dislike": -1.0,
    "playlist_add": 1.0,
    "replay": 0.5,
}

COLLECTION_FEEDBACK_TYPES = {"favorite", "playlist_add"}


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    feedback_type: str
    song_id: str
    timestamp: str
    reward_score: float
    recommendation_context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_type": self.feedback_type,
            "song_id": self.song_id,
            "timestamp": self.timestamp,
            "reward_score": self.reward_score,
            "recommendation_context": dict(self.recommendation_context),
        }


@dataclass(frozen=True, slots=True)
class FeedbackSummary:
    user_id: str
    total_events: int
    positive_events: int
    negative_events: int
    neutral_events: int
    average_reward: float
    feedback_type_counts: dict[str, int]
    missing_song_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "total_events": self.total_events,
            "positive_events": self.positive_events,
            "negative_events": self.negative_events,
            "neutral_events": self.neutral_events,
            "average_reward": self.average_reward,
            "feedback_type_counts": dict(self.feedback_type_counts),
            "missing_song_ids": list(self.missing_song_ids),
        }


def feedback_schema() -> dict[str, Any]:
    return {
        "feedback_type": {
            name: REWARD_BY_FEEDBACK_TYPE[name] for name in FEEDBACK_TYPES
        },
        "song_id": "existing L2 song_id",
        "timestamp": "ISO-8601 string; generated when omitted",
        "reward_score": "number between -1 and 1; derived from feedback_type",
        "recommendation_context": "optional object",
    }
