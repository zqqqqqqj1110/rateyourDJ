from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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

        profile.metadata.update(normalized.get("metadata", {}))
        for field_name, values in normalized.get(
            "aligned_features", {}
        ).items():
            profile.aligned_features[field_name].update(values)
        profile.avoid_tags.update(normalized.get("avoid_tags", {}))
        profile.semantic_tags.update(normalized.get("semantic_tags", {}))

        for section_name in ("source_tags", "data_source"):
            target = getattr(profile, section_name)
            for source, values in normalized.get(section_name, {}).items():
                target[source] = values

        for field_name in (
            "confidence_score",
            "embedding_text",
            "embedding",
        ):
            if field_name in normalized:
                setattr(profile, field_name, normalized[field_name])

        profile.version += 1
        profile.updated_at = datetime.now(timezone.utc).isoformat()
        self.store.save(profile)
        return profile
