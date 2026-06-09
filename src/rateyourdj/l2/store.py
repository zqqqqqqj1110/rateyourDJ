from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .models import SongProfile


class SongNotFoundError(KeyError):
    pass


class JsonSongStore:
    def __init__(self, root: str | Path = "data/song_profiles") -> None:
        self.root = Path(root)

    def _path_for(self, song_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", song_id):
            raise ValueError(
                "song_id may contain only letters, numbers, '.', '_' and '-'"
            )
        return self.root / f"{song_id}.json"

    def exists(self, song_id: str) -> bool:
        return self._path_for(song_id).exists()

    def load(self, song_id: str) -> SongProfile:
        path = self._path_for(song_id)
        if not path.exists():
            raise SongNotFoundError(song_id)
        with path.open("r", encoding="utf-8") as file:
            return SongProfile.from_dict(json.load(file))

    def load_or_create(self, song_id: str) -> SongProfile:
        if self.exists(song_id):
            return self.load(song_id)
        profile = SongProfile.empty(song_id)
        self.save(profile)
        return profile

    def save(self, profile: SongProfile) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path_for(profile.song_id)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root, prefix=f".{profile.song_id}.", suffix=".tmp"
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
