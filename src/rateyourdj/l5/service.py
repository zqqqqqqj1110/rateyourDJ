from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from rateyourdj.l1 import FEEDBACK_TYPES, JsonProfileStore, UserProfileService
from rateyourdj.l2 import JsonSongStore, SongNotFoundError
from rateyourdj.collectors.album import rebuild_user_profile

from .models import (
    COLLECTION_FEEDBACK_TYPES,
    REWARD_BY_FEEDBACK_TYPE,
    FeedbackRecord,
    FeedbackSummary,
)
from .scoring import FeedbackSignalModel


class FeedbackService:
    def __init__(
        self,
        profile_store: JsonProfileStore,
        song_store: JsonSongStore,
    ) -> None:
        self.profile_store = profile_store
        self.song_store = song_store
        self.profile_service = UserProfileService(profile_store)

    def record(
        self,
        user_id: str,
        song_id: str,
        feedback_type: str,
        *,
        timestamp: str | None = None,
        reward_score: float | None = None,
        recommendation_context: dict[str, Any] | None = None,
    ) -> FeedbackRecord:
        if feedback_type not in FEEDBACK_TYPES:
            raise ValueError(
                "feedback_type must be one of " + ", ".join(FEEDBACK_TYPES)
            )
        if not self.song_store.exists(song_id):
            raise SongNotFoundError(song_id)
        reward = (
            REWARD_BY_FEEDBACK_TYPE[feedback_type]
            if reward_score is None
            else _validate_reward(reward_score)
        )
        event_time = timestamp or datetime.now(timezone.utc).isoformat()
        _validate_timestamp(event_time)
        context = {} if recommendation_context is None else recommendation_context
        if not isinstance(context, dict):
            raise ValueError("recommendation_context must be an object")

        record = FeedbackRecord(
            feedback_type=feedback_type,
            song_id=song_id,
            timestamp=event_time,
            reward_score=reward,
            recommendation_context=context,
        )
        profile = self.profile_service.import_profile_patch(
            user_id,
            {"feedback_memory": [record.to_dict()]},
        )
        if feedback_type in COLLECTION_FEEDBACK_TYPES:
            collection_song_ids = list(profile.collection_song_ids)
            if song_id not in collection_song_ids:
                collection_song_ids.append(song_id)
            rebuild_user_profile(
                user_id,
                song_ids=collection_song_ids,
                song_data_dir=self.song_store.root,
                user_data_dir=self.profile_store.root,
            )
        return record

    def summary(self, user_id: str) -> FeedbackSummary:
        profile = self.profile_store.load(user_id)
        rewards = [
            _validate_stored_reward(record.get("reward_score"))
            for record in profile.feedback_memory
        ]
        missing = sorted(
            {
                str(record.get("song_id"))
                for record in profile.feedback_memory
                if record.get("song_id")
                and not self.song_store.exists(str(record["song_id"]))
            }
        )
        counts = Counter(
            str(record.get("feedback_type"))
            for record in profile.feedback_memory
            if record.get("feedback_type")
        )
        return FeedbackSummary(
            user_id=user_id,
            total_events=len(rewards),
            positive_events=sum(reward > 0 for reward in rewards),
            negative_events=sum(reward < 0 for reward in rewards),
            neutral_events=sum(reward == 0 for reward in rewards),
            average_reward=(
                round(sum(rewards) / len(rewards), 6) if rewards else 0.0
            ),
            feedback_type_counts=dict(sorted(counts.items())),
            missing_song_ids=missing,
        )

    def score_song(self, user_id: str, song_id: str) -> float:
        profile = self.profile_store.load(user_id)
        if not self.song_store.exists(song_id):
            raise SongNotFoundError(song_id)
        return FeedbackSignalModel(profile, self.song_store).score(
            self.song_store.load(song_id)
        )


def _validate_reward(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("reward_score must be numeric")
    reward = float(value)
    if not -1 <= reward <= 1:
        raise ValueError("reward_score must be between -1 and 1")
    return reward


def _validate_stored_reward(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(-1.0, min(float(value), 1.0))


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be an ISO-8601 string") from error
