from __future__ import annotations

from pathlib import Path
from typing import Any

from .merger import SourceInput
from .models import SongProfile, song_schema, validate_song_patch
from .service import SongProfileService
from .store import JsonSongStore


DEFAULT_SONG_DIR = Path("data/song_profiles")


def _service(data_dir: str | Path) -> SongProfileService:
    return SongProfileService(JsonSongStore(data_dir))


def get_song_schema() -> dict[str, Any]:
    return song_schema()


def get_song_profile(
    song_id: str, data_dir: str | Path = DEFAULT_SONG_DIR
) -> SongProfile:
    return _service(data_dir).get_song_profile(song_id)


def validate_song_dictionary(song_patch: dict[str, Any]) -> dict[str, Any]:
    return validate_song_patch(song_patch)


def import_song_dictionary(
    song_id: str,
    song_patch: dict[str, Any],
    data_dir: str | Path = DEFAULT_SONG_DIR,
) -> SongProfile:
    return _service(data_dir).import_song_patch(song_id, song_patch)


def merge_and_store_song(
    song_id: str,
    *,
    spotify: SourceInput = None,
    musicbrainz: SourceInput = None,
    lastfm: SourceInput = None,
    data_dir: str | Path = DEFAULT_SONG_DIR,
) -> SongProfile:
    return _service(data_dir).merge_and_save_sources(
        song_id,
        spotify=spotify,
        musicbrainz=musicbrainz,
        lastfm=lastfm,
    )


__all__ = [
    "get_song_profile",
    "get_song_schema",
    "import_song_dictionary",
    "merge_and_store_song",
    "validate_song_dictionary",
]
