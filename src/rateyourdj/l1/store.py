from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .models import UserProfile


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
        path = self._path_for(user_id)
        if not path.exists():
            raise ProfileNotFoundError(user_id)
        with path.open("r", encoding="utf-8") as file:
            return UserProfile.from_dict(json.load(file))

    def load_or_create(self, user_id: str) -> UserProfile:
        if self.exists(user_id):
            return self.load(user_id)
        profile = UserProfile(user_id=user_id)
        self.save(profile)
        return profile

    def save(self, profile: UserProfile) -> Path:
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
