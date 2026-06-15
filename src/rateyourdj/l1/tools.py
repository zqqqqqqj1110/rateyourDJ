from __future__ import annotations

from pathlib import Path
from typing import Any

from rateyourdj.agent_tools import ToolObservation

from .models import UserProfile, profile_schema, validate_profile_patch
from .service import UserProfileService
from .store import JsonProfileStore


DEFAULT_PROFILE_DIR = Path("data/user_profiles")


def _service(data_dir: str | Path) -> UserProfileService:
    return UserProfileService(JsonProfileStore(data_dir))


def get_profile_schema() -> dict[str, Any]:
    return profile_schema()


def get_user_profile(
    user_id: str, data_dir: str | Path = DEFAULT_PROFILE_DIR
) -> UserProfile:
    return _service(data_dir).get_user_profile(user_id)


def validate_profile_dictionary(profile_patch: dict[str, Any]) -> dict[str, Any]:
    return validate_profile_patch(profile_patch)


def import_profile_dictionary(
    user_id: str,
    profile_patch: dict[str, Any],
    data_dir: str | Path = DEFAULT_PROFILE_DIR,
) -> UserProfile:
    return _service(data_dir).import_profile_patch(user_id, profile_patch)


def inspect_user_profile(
    user_id: str,
    data_dir: str | Path = DEFAULT_PROFILE_DIR,
) -> ToolObservation:
    profile = _service(data_dir).get_user_profile(user_id)
    diagnostics: list[str] = []
    suggested_actions: list[dict[str, Any]] = []
    if not profile.collection_song_ids:
        diagnostics.append("collection is empty")
        suggested_actions.append(
            {
                "tool": "L2.collect_or_import",
                "reason": "recommendation requires collection seed songs",
            }
        )
    if not any(
        (
            profile.artist_preferences,
            profile.genre_preferences,
            profile.tag_preferences,
        )
    ):
        diagnostics.append("collection preferences are empty")
        suggested_actions.append(
            {
                "tool": "L1.rebuild_profile",
                "reason": "rebuild preferences from current L2 collection songs",
            }
        )
    return ToolObservation(
        tool="L1.inspect_user_profile",
        status="empty" if not profile.collection_song_ids else "ok",
        data={
            "user_id": profile.user_id,
            "collection_song_ids": list(profile.collection_song_ids),
            "collection_count": len(profile.collection_song_ids),
            "artist_preferences": dict(profile.artist_preferences),
            "genre_preferences": dict(profile.genre_preferences),
            "tag_preferences": dict(profile.tag_preferences),
            "feedback_count": len(profile.feedback_memory),
            "version": profile.version,
            "updated_at": profile.updated_at,
        },
        diagnostics=diagnostics,
        retryable=bool(diagnostics),
        suggested_actions=suggested_actions,
    )


__all__ = [
    "get_profile_schema",
    "get_user_profile",
    "import_profile_dictionary",
    "inspect_user_profile",
    "validate_profile_dictionary",
]
