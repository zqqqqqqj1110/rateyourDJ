from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from .models import AgentTrajectory
from rateyourdj.l5.models import COLLECTION_FEEDBACK_TYPES

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class TrajectoryNotFoundError(KeyError):
    pass


class JsonTrajectoryStore:
    def __init__(self, root: str | Path = "data/trajectories") -> None:
        self.root = Path(root)

    def _user_dir(self, user_id: str) -> Path:
        _validate_identifier(user_id, "user_id")
        return self.root / user_id

    def _path_for(self, user_id: str, trajectory_id: str) -> Path:
        _validate_identifier(trajectory_id, "trajectory_id")
        return self._user_dir(user_id) / f"{trajectory_id}.json"

    def exists(self, user_id: str, trajectory_id: str) -> bool:
        return self._path_for(user_id, trajectory_id).exists()

    def save(self, trajectory: AgentTrajectory) -> Path:
        with self._locked(trajectory.user_id, trajectory.trajectory_id):
            return self._save_unlocked(trajectory)

    def _save_unlocked(self, trajectory: AgentTrajectory) -> Path:
        destination = self._path_for(
            trajectory.user_id,
            trajectory.trajectory_id,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{trajectory.trajectory_id}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(
                    trajectory.to_dict(),
                    file,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_name, destination)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return destination

    def load(self, user_id: str, trajectory_id: str) -> AgentTrajectory:
        return self._load_unlocked(user_id, trajectory_id)

    def _load_unlocked(
        self,
        user_id: str,
        trajectory_id: str,
    ) -> AgentTrajectory:
        path = self._path_for(user_id, trajectory_id)
        if not path.exists():
            raise TrajectoryNotFoundError(trajectory_id)
        with path.open("r", encoding="utf-8") as file:
            return AgentTrajectory.from_dict(json.load(file))

    def append_feedback(
        self,
        user_id: str,
        trajectory_id: str,
        feedback: dict[str, Any],
    ) -> None:
        with self._locked(user_id, trajectory_id):
            trajectory = self._load_unlocked(user_id, trajectory_id)
            normalized_feedback = dict(feedback)
            feedback_context = _feedback_context_entry(
                trajectory,
                normalized_feedback,
            )
            collection_write = _collection_write_entry(
                trajectory,
                normalized_feedback,
                feedback_context,
            )
            updated = replace(
                trajectory,
                feedback_events=[
                    *trajectory.feedback_events,
                    normalized_feedback,
                ],
                feedback_contexts=[
                    *trajectory.feedback_contexts,
                    feedback_context,
                ],
                collection_writes=(
                    [
                        *trajectory.collection_writes,
                        collection_write,
                    ]
                    if collection_write is not None
                    else list(trajectory.collection_writes)
                ),
            )
            self._save_unlocked(updated)

    def list_for_user(self, user_id: str) -> list[AgentTrajectory]:
        user_dir = self._user_dir(user_id)
        if not user_dir.exists():
            return []
        trajectories = [
            self.load(user_id, path.stem)
            for path in sorted(user_dir.glob("*.json"))
        ]
        return sorted(
            trajectories,
            key=lambda item: (item.created_at, item.trajectory_id),
            reverse=True,
        )

    @contextmanager
    def _locked(
        self,
        user_id: str,
        trajectory_id: str,
    ) -> Iterator[None]:
        path = self._path_for(user_id, trajectory_id).absolute()
        key = str(path)
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
        with thread_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.parent / f".{trajectory_id}.lock"
            with lock_path.open("a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _validate_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+",
        value,
    ):
        raise ValueError(
            f"{name} may contain only letters, numbers, '.', '_' and '-'"
        )


def _feedback_context_entry(
    trajectory: AgentTrajectory,
    feedback: dict[str, Any],
) -> dict[str, Any]:
    recommendation_context = dict(
        feedback.get("recommendation_context", {})
        if isinstance(feedback.get("recommendation_context"), dict)
        else {}
    )
    return {
        "trajectory_id": trajectory.trajectory_id,
        "session_id": trajectory.session_id,
        "turn_index": trajectory.turn_index,
        "user_id": trajectory.user_id,
        "query": trajectory.query,
        "feedback_type": str(feedback.get("feedback_type", "")),
        "song_id": str(feedback.get("song_id", "")),
        "timestamp": feedback.get("timestamp"),
        "reward_score": feedback.get("reward_score"),
        "stop_reason": trajectory.stop_reason,
        "agent_mode": trajectory.agent_mode,
        "provider": trajectory.provider,
        "recommendation_context": recommendation_context,
        "recommendation_rank": recommendation_context.get("rank"),
        "recommended_final_score": recommendation_context.get("final_score"),
        "feedback_source": recommendation_context.get("source"),
        "playback_position_ms": recommendation_context.get(
            "playback_position_ms"
        ),
        "playback_duration_ms": recommendation_context.get(
            "playback_duration_ms"
        ),
        "track": (
            dict(recommendation_context["track"])
            if isinstance(recommendation_context.get("track"), dict)
            else None
        ),
    }


def _collection_write_entry(
    trajectory: AgentTrajectory,
    feedback: dict[str, Any],
    feedback_context: dict[str, Any],
) -> dict[str, Any] | None:
    feedback_type = str(feedback.get("feedback_type", ""))
    if feedback_type not in COLLECTION_FEEDBACK_TYPES:
        return None
    return {
        "action": "add_track",
        "status": "applied",
        "scope": "collection",
        "source": "feedback",
        "feedback_type": feedback_type,
        "song_id": str(feedback.get("song_id", "")),
        "timestamp": feedback.get("timestamp"),
        "trajectory_id": trajectory.trajectory_id,
        "session_id": trajectory.session_id,
        "turn_index": trajectory.turn_index,
        "recommendation_rank": feedback_context.get("recommendation_rank"),
        "track": feedback_context.get("track"),
    }
