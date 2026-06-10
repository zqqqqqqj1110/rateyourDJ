from __future__ import annotations

from collections.abc import Mapping

from rateyourdj.l1 import UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l3 import weighted_jaccard


FEEDBACK_SIMILARITY_WEIGHTS = {
    "artist": 0.25,
    "genres": 0.35,
    "tags": 0.40,
}
MIN_FEEDBACK_SIMILARITY = 0.30


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").strip().casefold().split())


def _candidate_tags(song: SongProfile) -> dict[str, float]:
    tags: dict[str, float] = {}
    for source_tags in song.source_tags.values():
        for label, score in source_tags.items():
            key = _normalized_text(label)
            if key:
                tags[key] = max(tags.get(key, 0.0), float(score))
    return tags


def feedback_similarity(left: SongProfile, right: SongProfile) -> float:
    artist_match = float(
        bool(_normalized_text(left.metadata["artist"]))
        and _normalized_text(left.metadata["artist"])
        == _normalized_text(right.metadata["artist"])
    )
    raw_scores = {
        "artist": artist_match,
        "genres": weighted_jaccard(left.genres, right.genres),
        "tags": weighted_jaccard(_candidate_tags(left), _candidate_tags(right)),
    }
    return sum(
        raw_scores[name] * weight
        for name, weight in FEEDBACK_SIMILARITY_WEIGHTS.items()
    )


class FeedbackSignalModel:
    """Build a reusable feedback model for scoring one recommendation pool."""

    def __init__(self, profile: UserProfile, song_store: JsonSongStore) -> None:
        self.direct_rewards: dict[str, float] = {}
        self.feedback_songs: list[tuple[SongProfile, float]] = []

        for record in profile.feedback_memory:
            song_id = record.get("song_id")
            reward = _reward(record)
            if not isinstance(song_id, str) or not song_id or reward == 0:
                continue
            self.direct_rewards[song_id] = reward
            if song_store.exists(song_id):
                self.feedback_songs.append((song_store.load(song_id), reward))

    def score(self, song: SongProfile) -> float:
        if song.song_id in self.direct_rewards:
            return self.direct_rewards[song.song_id]

        weighted_reward = 0.0
        for feedback_song, reward in self.feedback_songs:
            similarity = feedback_similarity(feedback_song, song)
            if similarity < MIN_FEEDBACK_SIMILARITY:
                continue
            weighted_reward += reward * similarity
        return round(
            max(-1.0, min(weighted_reward, 1.0)),
            6,
        )


def _reward(record: Mapping[str, object]) -> float:
    value = record.get("reward_score", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(-1.0, min(float(value), 1.0))
