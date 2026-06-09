from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import UserProfile, validate_profile_patch
from .store import JsonProfileStore


class UserProfileService:
    """Owns L1 dictionary validation, merge semantics and persistence."""

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

        for section_name in (
            "long_term_preference",
            "negative_preference",
        ):
            for field_name, values in normalized.get(section_name, {}).items():
                getattr(profile, section_name)[field_name].update(values)

        for field_name, value in normalized.get(
            "short_term_intent", {}
        ).items():
            profile.short_term_intent[field_name] = value

        profile.feedback_memory.extend(
            normalized.get("feedback_memory", [])
        )
        profile.version += 1
        profile.updated_at = datetime.now(timezone.utc).isoformat()
        self.store.save(profile)
        return profile

    def replace_profile_sections(
        self, user_id: str, profile_data: dict[str, Any]
    ) -> UserProfile:
        """Replace all four L1 sections while preserving profile metadata."""
        normalized = validate_profile_patch(profile_data)
        required = {
            "long_term_preference",
            "short_term_intent",
            "negative_preference",
            "feedback_memory",
        }
        missing = sorted(required - set(normalized))
        if missing:
            raise ValueError(
                "replacement is missing sections: " + ", ".join(missing)
            )

        profile = self.get_user_profile(user_id)
        replacement = UserProfile.empty(user_id)
        replacement.long_term_preference.update(
            normalized["long_term_preference"]
        )
        replacement.short_term_intent.update(
            normalized["short_term_intent"]
        )
        replacement.negative_preference.update(
            normalized["negative_preference"]
        )
        replacement.feedback_memory = normalized["feedback_memory"]
        replacement.version = profile.version + 1
        replacement.updated_at = datetime.now(timezone.utc).isoformat()
        self.store.save(replacement)
        return replacement
