"""Local web interface for rateyourDJ recommendations and feedback."""

from pathlib import Path
from typing import Any


def create_app(
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> Any:
    from .app import create_app as app_factory

    return app_factory(profile_dir=profile_dir, song_dir=song_dir)


__all__ = ["create_app"]
