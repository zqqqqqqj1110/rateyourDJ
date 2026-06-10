from __future__ import annotations

from pathlib import Path
from typing import Any

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore

from .models import FeedbackRecord, FeedbackSummary
from .service import FeedbackService


def _service(
    profile_dir: str | Path,
    song_dir: str | Path,
) -> FeedbackService:
    return FeedbackService(
        JsonProfileStore(profile_dir),
        JsonSongStore(song_dir),
    )


def collect_feedback(
    user_id: str,
    song_id: str,
    feedback_type: str,
    *,
    timestamp: str | None = None,
    reward_score: float | None = None,
    recommendation_context: dict[str, Any] | None = None,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> FeedbackRecord:
    return _service(profile_dir, song_dir).record(
        user_id,
        song_id,
        feedback_type,
        timestamp=timestamp,
        reward_score=reward_score,
        recommendation_context=recommendation_context,
    )


def get_feedback_summary(
    user_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> FeedbackSummary:
    return _service(profile_dir, song_dir).summary(user_id)


def get_feedback_score(
    user_id: str,
    song_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> float:
    return _service(profile_dir, song_dir).score_song(user_id, song_id)
