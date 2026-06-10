from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .merger import SongDataMerger, SourceInput
from .models import SongProfile, validate_song_patch
from .store import JsonSongStore


class SongProfileService:
    """Owns L2 dictionary validation, merge semantics and persistence."""

    def __init__(self, store: JsonSongStore) -> None:
        self.store = store

    def get_song_profile(self, song_id: str) -> SongProfile:
        return self.store.load_or_create(song_id)

    def validate_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        return validate_song_patch(patch)

    def import_song_patch(
        self, song_id: str, patch: dict[str, Any]
    ) -> SongProfile:
        profile = self.get_song_profile(song_id)
        normalized = validate_song_patch(patch)

        profile.external_ids.update(normalized.get("external_ids", {}))
        profile.metadata.update(normalized.get("metadata", {}))
        for field_name, values in normalized.get("source_tags", {}).items():
            profile.source_tags[field_name] = values
        profile.genres.update(normalized.get("genres", {}))
        for field_name, sources in normalized.get("data_source", {}).items():
            profile.data_source[field_name] = sources
        if "confidence_score" in normalized:
            profile.confidence_score = normalized["confidence_score"]

        profile.version += 1
        profile.updated_at = datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat()
        self.store.save(profile)
        return profile

    def merge_and_save_sources(
        self,
        song_id: str,
        *,
        spotify: SourceInput = None,
        musicbrainz: SourceInput = None,
        lastfm: SourceInput = None,
    ) -> SongProfile:
        profile = SongDataMerger().merge(
            song_id,
            spotify=spotify,
            musicbrainz=musicbrainz,
            lastfm=lastfm,
        )
        self.store.save(profile)
        return profile
