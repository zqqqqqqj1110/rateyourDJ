from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import UserProfile

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class ProfileNotFoundError(KeyError):
    pass


class JsonProfileStore:
    def __init__(self, root: str | Path = "data/user_profiles") -> None:
        self.root = Path(root)

    def _path_for(self, user_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", user_id):
            raise ValueError(
                "user_id may contain only letters, numbers, '.', '_' and '-'"
            )
        return self.root / f"{user_id}.json"

    def exists(self, user_id: str) -> bool:
        return self._path_for(user_id).exists()

    def load(self, user_id: str) -> UserProfile:
        return self._load_unlocked(user_id)

    def _load_unlocked(self, user_id: str) -> UserProfile:
        path = self._path_for(user_id)
        if not path.exists():
            raise ProfileNotFoundError(user_id)
        with path.open("r", encoding="utf-8") as file:
            return UserProfile.from_dict(json.load(file))

    def load_or_create(self, user_id: str) -> UserProfile:
        with self._locked(user_id):
            if self.exists(user_id):
                path = self._path_for(user_id)
                with path.open("r", encoding="utf-8") as file:
                    stored_payload = json.load(file)
                profile = UserProfile.from_dict(stored_payload)
                if stored_payload != profile.to_dict():
                    self._save_unlocked(profile)
                return profile
            profile = UserProfile(user_id=user_id)
            self._save_unlocked(profile)
            return profile

    def save(self, profile: UserProfile) -> Path:
        with self._locked(profile.user_id):
            return self._save_unlocked(profile)

    def update(
        self,
        user_id: str,
        updater: Callable[[UserProfile], UserProfile],
    ) -> UserProfile:
        """Apply one read-modify-write transaction to a user profile."""
        with self._locked(user_id):
            profile = (
                self._load_unlocked(user_id)
                if self.exists(user_id)
                else UserProfile(user_id=user_id)
            )
            updated = updater(profile)
            if updated.user_id != user_id:
                raise ValueError("profile updater cannot change user_id")
            self._save_unlocked(updated)
            return updated

    def _save_unlocked(self, profile: UserProfile) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path_for(profile.user_id)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root, prefix=f".{profile.user_id}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(
                    profile.to_dict(),
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

    @contextmanager
    def _locked(self, user_id: str) -> Iterator[None]:
        profile_path = self._path_for(user_id).absolute()
        lock_key = str(profile_path)
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(
                lock_key,
                threading.RLock(),
            )

        with thread_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            lock_path = self.root / f".{user_id}.lock"
            with lock_path.open("a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
