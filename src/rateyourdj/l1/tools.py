from __future__ import annotations

from pathlib import Path
from typing import Any

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


__all__ = [
    "get_profile_schema",
    "get_user_profile",
    "import_profile_dictionary",
    "validate_profile_dictionary",
]
