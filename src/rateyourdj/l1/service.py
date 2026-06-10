from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import PREFERENCE_FIELDS, UserProfile, validate_profile_patch
from .store import JsonProfileStore


class UserProfileService:
    """Owns L1 collection-profile validation, merging and persistence."""

    def __init__(self, store: JsonProfileStore) -> None:
        self.store = store

    def get_user_profile(self, user_id: str) -> UserProfile:
        return self.store.load_or_create(user_id)

    def validate_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        return validate_profile_patch(patch)

    def import_profile_patch(
        self, user_id: str, patch: dict[str, Any]
    ) -> UserProfile:
        profile = self.get_user_profile(user_id)
        normalized = validate_profile_patch(patch)

        for song_id in normalized.get("collection_song_ids", []):
            if song_id not in profile.collection_song_ids:
                profile.collection_song_ids.append(song_id)

        for field_name in PREFERENCE_FIELDS:
            getattr(profile, field_name).update(normalized.get(field_name, {}))

        profile.feedback_memory.extend(
            normalized.get("feedback_memory", [])
        )
        profile.version += 1
        profile.updated_at = datetime.now(timezone.utc).isoformat()
        self.store.save(profile)
        return profile

    def replace_profile_data(
        self, user_id: str, profile_data: dict[str, Any]
    ) -> UserProfile:
        """Replace all L1 data fields while preserving profile identity."""
        normalized = validate_profile_patch(profile_data)
        missing = sorted(set(self._required_patch_fields()) - set(normalized))
        if missing:
            raise ValueError(
                "replacement is missing fields: " + ", ".join(missing)
            )

        existing = self.get_user_profile(user_id)
        replacement = UserProfile(
            user_id=user_id,
            collection_song_ids=normalized["collection_song_ids"],
            artist_preferences=normalized["artist_preferences"],
            genre_preferences=normalized["genre_preferences"],
            tag_preferences=normalized["tag_preferences"],
            feedback_memory=normalized["feedback_memory"],
            version=existing.version + 1,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.save(replacement)
        return replacement

    @staticmethod
    def _required_patch_fields() -> tuple[str, ...]:
        return (
            "collection_song_ids",
            *PREFERENCE_FIELDS,
            "feedback_memory",
        )
