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
        normalized = validate_profile_patch(patch)

        def update(profile: UserProfile) -> UserProfile:
            for song_id in normalized.get("collection_song_ids", []):
                if song_id not in profile.collection_song_ids:
                    profile.collection_song_ids.append(song_id)

            for field_name in PREFERENCE_FIELDS:
                getattr(profile, field_name).update(
                    normalized.get(field_name, {})
                )

            profile.feedback_memory.extend(
                normalized.get("feedback_memory", [])
            )
            profile.version += 1
            profile.updated_at = datetime.now(timezone.utc).isoformat()
            return profile

        return self.store.update(user_id, update)

    def replace_profile_data(
        self,
        user_id: str,
        profile_data: dict[str, Any],
        *,
        preserve_feedback: bool = False,
    ) -> UserProfile:
        """Replace all L1 data fields while preserving profile identity."""
        normalized = validate_profile_patch(profile_data)
        missing = sorted(set(self._required_patch_fields()) - set(normalized))
        if missing:
            raise ValueError(
                "replacement is missing fields: " + ", ".join(missing)
            )

        def replace(existing: UserProfile) -> UserProfile:
            return UserProfile(
                user_id=user_id,
                collection_song_ids=normalized["collection_song_ids"],
                artist_preferences=normalized["artist_preferences"],
                genre_preferences=normalized["genre_preferences"],
                tag_preferences=normalized["tag_preferences"],
                feedback_memory=(
                    list(existing.feedback_memory)
                    if preserve_feedback
                    else normalized["feedback_memory"]
                ),
                # conversation_affinity is learned from chat, not from the
                # collection, so a collection rebuild must preserve it.
                conversation_affinity=dict(existing.conversation_affinity),
                version=existing.version + 1,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )

        return self.store.update(user_id, replace)

    @staticmethod
    def _required_patch_fields() -> tuple[str, ...]:
        return (
            "collection_song_ids",
            *PREFERENCE_FIELDS,
            "feedback_memory",
        )

    def learn_from_conversation(
        self,
        user_id: str,
        terms: list[str],
        *,
        increment: float = 0.34,
        decay: float = 0.9,
        max_terms: int = 64,
    ) -> UserProfile:
        """Nudge long-term conversation affinity from chat preference terms.

        This is a *light* learning signal: terms the user mentions in chat
        gain weight even without an explicit like/save, while all other terms
        decay slightly so stale interests fade. Weights use a decayed EMA and
        are clamped to [0, 1]. Stored separately from collection-derived
        preferences so a collection rebuild never overwrites it.
        """
        cleaned = []
        seen: set[str] = set()
        for term in terms:
            label = str(term or "").strip()
            key = label.casefold()
            if label and key not in seen:
                seen.add(key)
                cleaned.append(label)
        if not cleaned:
            return self.store.load_or_create(user_id)

        boosted = {term.casefold() for term in cleaned}

        def update(profile: UserProfile) -> UserProfile:
            affinity = dict(profile.conversation_affinity)
            # Decay existing interests so the profile tracks recent taste.
            for label in list(affinity):
                affinity[label] = round(affinity[label] * decay, 6)
            # Boost mentioned terms (decayed EMA toward 1.0).
            for label in cleaned:
                current = affinity.get(label, 0.0)
                affinity[label] = round(
                    min(1.0, current * decay + increment), 6
                )
            # Drop negligible weights, then cap the dictionary size.
            affinity = {
                label: weight
                for label, weight in affinity.items()
                if weight >= 0.05 or label.casefold() in boosted
            }
            if len(affinity) > max_terms:
                affinity = dict(
                    sorted(
                        affinity.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )[:max_terms]
                )
            profile.conversation_affinity = affinity
            profile.version += 1
            profile.updated_at = datetime.now(timezone.utc).isoformat()
            return profile

        return self.store.update(user_id, update)
