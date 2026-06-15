from __future__ import annotations

from pathlib import Path
from typing import Any

from rateyourdj.agent_tools import ToolObservation

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


def inspect_song_profile(
    song_id: str,
    data_dir: str | Path = DEFAULT_SONG_DIR,
) -> ToolObservation:
    profile = _service(data_dir).store.load(song_id)
    missing_metadata = [
        field_name
        for field_name in ("title", "artist", "album", "release_year", "duration_ms")
        if profile.metadata.get(field_name) in (None, "")
    ]
    diagnostics: list[str] = []
    if missing_metadata:
        diagnostics.append(
            "missing metadata fields: " + ", ".join(missing_metadata)
        )
    if not profile.source_tags["lastfm_track_tags"]:
        diagnostics.append("Last.fm track tags are empty")
    if not profile.genres:
        diagnostics.append("normalized genres are empty")
    return ToolObservation(
        tool="L2.inspect_song_profile",
        status="partial" if diagnostics else "ok",
        data=profile.to_dict(),
        diagnostics=diagnostics,
        retryable=bool(diagnostics),
        suggested_actions=(
            [
                {
                    "tool": "L2.merge_sources",
                    "reason": "refresh missing metadata, tags or genres",
                }
            ]
            if diagnostics
            else []
        ),
    )


__all__ = [
    "get_song_profile",
    "get_song_schema",
    "import_song_dictionary",
    "inspect_song_profile",
    "merge_and_store_song",
    "validate_song_dictionary",
]
