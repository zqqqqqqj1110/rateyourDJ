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
            updated = replace(
                trajectory,
                feedback_events=[
                    *trajectory.feedback_events,
                    dict(feedback),
                ],
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
