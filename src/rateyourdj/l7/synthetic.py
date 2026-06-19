from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l5 import REWARD_BY_FEEDBACK_TYPE
from rateyourdj.l6 import AgentTrajectory, JsonTrajectoryStore

from .models import SyntheticGenerationResult


QUERY_TEMPLATES = [
    "推荐 {top_k} 首{genre}，多样一点",
    "来 {top_k} 首适合现在听的{genre}",
    "想听 {genre}，推荐 {top_k} 首不同歌手的歌",
    "给我 {top_k} 首和收藏相似的{genre}",
    "推荐 {top_k} 首{genre}，不要重复歌手",
]

FEEDBACK_WEIGHTS = [
    ("play", 0.18),
    ("play_complete", 0.18),
    ("like", 0.18),
    ("favorite", 0.08),
    ("playlist_add", 0.04),
    ("replay", 0.07),
    ("skip", 0.16),
    ("quick_skip", 0.07),
    ("dislike", 0.04),
]


@dataclass(frozen=True, slots=True)
class _Song:
    song_id: str
    title: str
    artist: str
    genres: tuple[str, ...]


class SyntheticTrajectoryGenerator:
    def __init__(
        self,
        song_dir: str | Path = "data/song_profiles",
    ) -> None:
        self.song_store = JsonSongStore(song_dir)

    def generate(
        self,
        output_dir: str | Path,
        *,
        count: int = 500,
        users: int = 25,
        seed: int = 20260615,
        feedback_rate: float = 0.7,
    ) -> SyntheticGenerationResult:
        if not 1 <= count <= 100_000:
            raise ValueError("count must be between 1 and 100000")
        if not 1 <= users <= count:
            raise ValueError("users must be between 1 and count")
        if not 0 <= feedback_rate <= 1:
            raise ValueError("feedback_rate must be between 0 and 1")
        destination = Path(output_dir)
        if any(destination.glob("*/*.json")):
            raise FileExistsError(
                f"synthetic output already contains trajectories: {destination}"
            )

        songs = self._load_songs()
        if not songs:
            raise ValueError("song profile directory contains no usable songs")
        genres = sorted(
            {genre for song in songs for genre in song.genres}
        )
        if not genres:
            genres = ["music"]

        rng = random.Random(seed)
        store = JsonTrajectoryStore(destination)
        feedback_event_count = 0
        session_ids: set[str] = set()
        turns_by_user: dict[str, int] = {}
        sessions_by_user: dict[str, str] = {}
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

        for index in range(count):
            user_index = index % users
            user_id = f"synthetic-user-{user_index + 1:04d}"
            turn_index = turns_by_user.get(user_id, 0) + 1
            if turn_index == 1 or rng.random() < 0.22:
                session_id = self._identifier(seed, "session", index)
                turn_index = 1
                sessions_by_user[user_id] = session_id
            else:
                session_id = sessions_by_user[user_id]
            turns_by_user[user_id] = turn_index
            session_ids.add(session_id)

            genre = rng.choice(genres)
            top_k = rng.choices([5, 10, 20], weights=[0.55, 0.35, 0.10])[0]
            max_per_artist = rng.choices([1, 2], weights=[0.62, 0.38])[0]
            query = rng.choice(QUERY_TEMPLATES).format(
                top_k=top_k,
                genre=genre.replace("_", " "),
            )
            preferred = [song for song in songs if genre in song.genres]
            pool = preferred if len(preferred) >= top_k else songs
            goal_satisfied = rng.random() < 0.84
            result_count = (
                top_k
                if goal_satisfied
                else rng.randint(max(1, top_k // 3), max(1, top_k - 1))
            )
            recommendations = self._recommendations(
                rng,
                pool,
                result_count,
                max_per_artist=max_per_artist,
            )
            trajectory_id = self._identifier(seed, "trajectory", index)
            feedback_events = self._feedback_events(
                rng,
                recommendations,
                trajectory_id,
                created_at,
                feedback_rate,
            )
            feedback_event_count += len(feedback_events)
            agent_mode = rng.choices(
                ["rules", "model"],
                weights=[0.62, 0.38],
            )[0]
            fallback_reason = (
                "synthetic provider timeout"
                if agent_mode == "rules" and rng.random() < 0.08
                else None
            )
            tool_calls = self._tool_calls(
                user_id,
                top_k,
                max_per_artist,
                recommendations,
                partial=not goal_satisfied,
            )
            trajectory = AgentTrajectory(
                trajectory_id=trajectory_id,
                session_id=session_id,
                turn_index=turn_index,
                user_id=user_id,
                query=query,
                parsed_request={
                    "query": query,
                    "top_k": top_k,
                    "max_per_artist": max_per_artist,
                    "min_retrieval_score": 0.0,
                    "preference_terms": [genre],
                    "exclude_terms": [],
                    "intent": "recommend",
                    "exclude_seen": False,
                },
                plan=[
                    {
                        "tool": "L1.inspect_user_profile",
                        "reason": "inspect synthetic user profile",
                    },
                    {
                        "tool": "L4.rank_candidates",
                        "reason": "rank synthetic recommendation candidates",
                    },
                ],
                tool_calls=tool_calls,
                recommendations=recommendations,
                response_text=(
                    f"合成样本：为用户生成 {len(recommendations)} 首"
                    f"{genre.replace('_', ' ')}推荐。"
                ),
                feedback_events=feedback_events,
                stop_reason=(
                    "goal_satisfied"
                    if goal_satisfied
                    else "insufficient_candidates"
                ),
                agent_mode=agent_mode,
                provider="synthetic" if agent_mode == "model" else None,
                fallback_reason=fallback_reason,
                agent_decisions=[],
                created_at=(
                    created_at + timedelta(minutes=index * 17)
                ).isoformat(),
            )
            store.save(trajectory)

        return SyntheticGenerationResult(
            output_dir=str(destination),
            trajectory_count=count,
            user_count=users,
            session_count=len(session_ids),
            feedback_event_count=feedback_event_count,
            seed=seed,
        )

    def _load_songs(self) -> list[_Song]:
        songs: list[_Song] = []
        for path in sorted(self.song_store.root.glob("*.json")):
            profile = SongProfile.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            title = profile.metadata.get("title")
            artist = profile.metadata.get("artist")
            if not isinstance(title, str) or not isinstance(artist, str):
                continue
            songs.append(
                _Song(
                    song_id=profile.song_id,
                    title=title,
                    artist=artist,
                    genres=tuple(profile.genres),
                )
            )
        return songs

    @staticmethod
    def _recommendations(
        rng: random.Random,
        pool: list[_Song],
        count: int,
        *,
        max_per_artist: int,
    ) -> list[dict[str, Any]]:
        candidates = list(pool)
        rng.shuffle(candidates)
        selected: list[_Song] = []
        artist_counts: dict[str, int] = {}
        for song in candidates:
            if artist_counts.get(song.artist, 0) >= max_per_artist:
                continue
            selected.append(song)
            artist_counts[song.artist] = artist_counts.get(song.artist, 0) + 1
            if len(selected) >= count:
                break
        for song in candidates:
            if len(selected) >= count:
                break
            if song not in selected:
                selected.append(song)
        return [
            {
                "song_id": song.song_id,
                "title": song.title,
                "artist": song.artist,
                "rank": rank,
                "final_score": round(max(0.05, 0.92 - rank * 0.045), 6),
                "ranking_reasons": [
                    "retrieved from collection-level song similarity",
                    "matches the collection genre profile",
                ],
                "score_breakdown": {
                    "retrieval": round(max(0.01, 0.5 - rank * 0.02), 6),
                    "genre_preference": 0.15,
                    "quality": 0.1,
                },
            }
            for rank, song in enumerate(selected, start=1)
        ]

    @staticmethod
    def _feedback_events(
        rng: random.Random,
        recommendations: list[dict[str, Any]],
        trajectory_id: str,
        created_at: datetime,
        feedback_rate: float,
    ) -> list[dict[str, Any]]:
        if not recommendations or rng.random() >= feedback_rate:
            return []
        event_count = rng.randint(1, min(4, len(recommendations)))
        selected = rng.sample(recommendations, event_count)
        feedback_events = []
        types = [item[0] for item in FEEDBACK_WEIGHTS]
        weights = [item[1] for item in FEEDBACK_WEIGHTS]
        for offset, song in enumerate(selected, start=1):
            feedback_type = rng.choices(types, weights=weights)[0]
            feedback_events.append(
                {
                    "feedback_type": feedback_type,
                    "song_id": song["song_id"],
                    "timestamp": (
                        created_at + timedelta(seconds=offset * 30)
                    ).isoformat(),
                    "reward_score": REWARD_BY_FEEDBACK_TYPE[feedback_type],
                    "recommendation_context": {
                        "trajectory_id": trajectory_id,
                        "rank": song["rank"],
                        "final_score": song["final_score"],
                        "source": "synthetic",
                    },
                }
            )
        return feedback_events

    @staticmethod
    def _tool_calls(
        user_id: str,
        top_k: int,
        max_per_artist: int,
        recommendations: list[dict[str, Any]],
        *,
        partial: bool,
    ) -> list[dict[str, Any]]:
        ranking_status = "partial" if partial else "ok"
        return [
            {
                "step": 1,
                "tool": "L1.inspect_user_profile",
                "arguments": {"user_id": user_id},
                "observation": {
                    "tool": "L1.inspect_user_profile",
                    "status": "ok",
                    "data": {"user_id": user_id, "collection_count": 12},
                    "diagnostics": [],
                    "retryable": False,
                    "suggested_actions": [],
                },
                "decision": "continue to synthetic ranking",
            },
            {
                "step": 2,
                "tool": "L4.rank_candidates",
                "arguments": {
                    "user_id": user_id,
                    "top_k": top_k,
                    "candidate_pool_size": top_k * 5,
                    "max_per_artist": max_per_artist,
                    "min_retrieval_score": 0.0,
                },
                "observation": {
                    "tool": "L4.rank_candidates",
                    "status": ranking_status,
                    "data": {"ranked_songs": recommendations},
                    "diagnostics": (
                        ["synthetic candidate pool was intentionally short"]
                        if partial
                        else []
                    ),
                    "retryable": partial,
                    "suggested_actions": [],
                },
                "decision": (
                    "synthetic goal satisfied"
                    if not partial
                    else "synthetic candidates exhausted"
                ),
            },
        ]

    @staticmethod
    def _identifier(seed: int, kind: str, index: int) -> str:
        return f"synthetic-{kind}-{uuid5(NAMESPACE_URL, f'{seed}:{kind}:{index}')}"
