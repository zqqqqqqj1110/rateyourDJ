from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


PREFERENCE_FIELDS = (
    "artist_preferences",
    "genre_preferences",
    "tag_preferences",
)

FEEDBACK_TYPES = (
    "play",
    "play_complete",
    "skip",
    "quick_skip",
    "favorite",
    "like",
    "dislike",
    "playlist_add",
    "replay",
)

FEEDBACK_RECORD_FIELDS = (
    "feedback_type",
    "song_id",
    "timestamp",
    "reward_score",
    "recommendation_context",
)

REQUIRED_FEEDBACK_RECORD_FIELDS = {
    "feedback_type",
    "song_id",
    "timestamp",
    "reward_score",
}

TOP_LEVEL_IMPORT_FIELDS = (
    "collection_song_ids",
    *PREFERENCE_FIELDS,
    "feedback_memory",
)

LEGACY_PROFILE_FIELDS = {
    "user_id",
    "long_term_preference",
    "short_term_intent",
    "negative_preference",
    "feedback_memory",
    "version",
    "updated_at",
}


class ProfileValidationError(ValueError):
    """Raised when an incoming user dictionary violates the L1 schema."""


def empty_profile_dict(user_id: str) -> dict[str, Any]:
    """Return the complete, serializable L1 collection profile."""
    return {
        "user_id": user_id,
        "collection_song_ids": [],
        "artist_preferences": {},
        "genre_preferences": {},
        "tag_preferences": {},
        "conversation_affinity": {},
        "feedback_memory": [],
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def profile_schema() -> dict[str, Any]:
    """Return a machine-readable description of dictionaries accepted by L1."""
    return {
        "collection_song_ids": ["string"],
        "artist_preferences": {"<artist>": "number between 0 and 1"},
        "genre_preferences": {"<genre>": "number between 0 and 1"},
        "tag_preferences": {"<tag>": "number between 0 and 1"},
        "feedback_memory": [
            {
                "feedback_type": list(FEEDBACK_TYPES),
                "song_id": "string",
                "timestamp": "ISO 8601 string",
                "reward_score": "number supplied by the feedback module",
                "recommendation_context": "object supplied by later modules",
            }
        ],
    }


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError(f"{path} must be an object")
    return value


def _reject_unknown_keys(
    value: dict[str, Any], allowed: tuple[str, ...] | set[str], path: str
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ProfileValidationError(
            f"{path} contains unknown fields: {', '.join(unknown)}"
        )


def _validate_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise ProfileValidationError(f"{path} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ProfileValidationError(
                f"{path}[{index}] must be a non-empty string"
            )
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return result


def _validate_preferences(value: Any, path: str) -> dict[str, float]:
    preferences = _require_mapping(value, path)
    validated: dict[str, float] = {}
    for label, weight in preferences.items():
        if not isinstance(label, str) or not label.strip():
            raise ProfileValidationError(
                f"{path} keys must be non-empty strings"
            )
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ProfileValidationError(f"{path}.{label} must be a number")
        numeric_weight = float(weight)
        if not 0 <= numeric_weight <= 1:
            raise ProfileValidationError(
                f"{path}.{label} must be between 0 and 1"
            )
        validated[label.strip()] = numeric_weight
    return validated


def _validate_feedback_record(value: Any, path: str) -> dict[str, Any]:
    record = _require_mapping(value, path)
    _reject_unknown_keys(record, FEEDBACK_RECORD_FIELDS, path)
    missing = sorted(REQUIRED_FEEDBACK_RECORD_FIELDS - set(record))
    if missing:
        raise ProfileValidationError(
            f"{path} is missing fields: {', '.join(missing)}"
        )
    validated = deepcopy(record)

    for field_name in ("song_id", "timestamp"):
        if (
            not isinstance(record[field_name], str)
            or not record[field_name].strip()
        ):
            raise ProfileValidationError(
                f"{path}.{field_name} must be a non-empty string"
            )

    if record["feedback_type"] not in FEEDBACK_TYPES:
        raise ProfileValidationError(
            f"{path}.feedback_type must be one of "
            + ", ".join(FEEDBACK_TYPES)
        )

    if (
        isinstance(record["reward_score"], bool)
        or not isinstance(record["reward_score"], (int, float))
    ):
        raise ProfileValidationError(f"{path}.reward_score must be a number")

    if "recommendation_context" in record and not isinstance(
        record["recommendation_context"], dict
    ):
        raise ProfileValidationError(
            f"{path}.recommendation_context must be an object"
        )
    return validated


def validate_profile_patch(patch: Any) -> dict[str, Any]:
    """Validate a partial dictionary supplied by collection/feedback modules."""
    value = _require_mapping(patch, "profile patch")
    _reject_unknown_keys(value, TOP_LEVEL_IMPORT_FIELDS, "profile patch")
    validated: dict[str, Any] = {}

    if "collection_song_ids" in value:
        validated["collection_song_ids"] = _validate_string_list(
            value["collection_song_ids"], "collection_song_ids"
        )

    for field_name in PREFERENCE_FIELDS:
        if field_name in value:
            validated[field_name] = _validate_preferences(
                value[field_name], field_name
            )

    if "feedback_memory" in value:
        records = value["feedback_memory"]
        if not isinstance(records, list):
            raise ProfileValidationError("feedback_memory must be an array")
        validated["feedback_memory"] = [
            _validate_feedback_record(record, f"feedback_memory[{index}]")
            for index, record in enumerate(records)
        ]
    return validated


def migrate_legacy_profile(data: Any) -> dict[str, Any] | None:
    """Convert the project's previous L1 structure to the collection schema."""
    value = _require_mapping(data, "profile")
    if not {
        "long_term_preference",
        "short_term_intent",
        "negative_preference",
    }.intersection(value):
        return None
    _reject_unknown_keys(value, LEGACY_PROFILE_FIELDS, "legacy profile")

    migrated = empty_profile_dict(value.get("user_id", ""))
    migrated["feedback_memory"] = validate_profile_patch(
        {"feedback_memory": value.get("feedback_memory", [])}
    )["feedback_memory"]

    version = value.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ProfileValidationError(
            "legacy profile.version must be a positive integer"
        )
    migrated["version"] = version + 1

    updated_at = value.get("updated_at")
    if updated_at is not None and not isinstance(updated_at, str):
        raise ProfileValidationError(
            "legacy profile.updated_at must be a string"
        )
    return migrated


@dataclass(slots=True)
class UserProfile:
    user_id: str
    collection_song_ids: list[str] = field(default_factory=list)
    artist_preferences: dict[str, float] = field(default_factory=dict)
    genre_preferences: dict[str, float] = field(default_factory=dict)
    tag_preferences: dict[str, float] = field(default_factory=dict)
    feedback_memory: list[dict[str, Any]] = field(default_factory=list)
    conversation_affinity: dict[str, float] = field(default_factory=dict)
    version: int = 1
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "collection_song_ids": list(self.collection_song_ids),
            "artist_preferences": deepcopy(self.artist_preferences),
            "genre_preferences": deepcopy(self.genre_preferences),
            "tag_preferences": deepcopy(self.tag_preferences),
            "conversation_affinity": deepcopy(self.conversation_affinity),
            "feedback_memory": deepcopy(self.feedback_memory),
            "version": self.version,
            "updated_at": self.updated_at,
        }

    @classmethod
    def empty(cls, user_id: str) -> UserProfile:
        return cls.from_dict(empty_profile_dict(user_id))

    @classmethod
    def from_dict(cls, data: Any) -> UserProfile:
        value = _require_mapping(data, "profile")
        migrated = migrate_legacy_profile(value)
        if migrated is not None:
            value = migrated
        required = {
            "user_id",
            *TOP_LEVEL_IMPORT_FIELDS,
            "version",
            "updated_at",
        }
        # conversation_affinity is an optional, newer field: tolerate its
        # presence (new profiles) and its absence (profiles written before it
        # existed) without failing the strict unknown-field check.
        optional = {"conversation_affinity"}
        missing = sorted(required - set(value))
        unknown = sorted(set(value) - required - optional)
        if missing:
            raise ProfileValidationError(
                f"profile is missing fields: {', '.join(missing)}"
            )
        if unknown:
            raise ProfileValidationError(
                f"profile contains unknown fields: {', '.join(unknown)}"
            )
        if not isinstance(value["user_id"], str) or not value["user_id"].strip():
            raise ProfileValidationError(
                "profile.user_id must be a non-empty string"
            )
        if (
            isinstance(value["version"], bool)
            or not isinstance(value["version"], int)
            or value["version"] < 1
        ):
            raise ProfileValidationError(
                "profile.version must be a positive integer"
            )
        if not isinstance(value["updated_at"], str):
            raise ProfileValidationError("profile.updated_at must be a string")

        patch = validate_profile_patch(
            {key: value[key] for key in TOP_LEVEL_IMPORT_FIELDS}
        )
        affinity = _validate_preferences(
            value.get("conversation_affinity", {}),
            "conversation_affinity",
        )
        return cls(
            user_id=value["user_id"].strip(),
            collection_song_ids=patch["collection_song_ids"],
            artist_preferences=patch["artist_preferences"],
            genre_preferences=patch["genre_preferences"],
            tag_preferences=patch["tag_preferences"],
            feedback_memory=patch["feedback_memory"],
            conversation_affinity=affinity,
            version=value["version"],
            updated_at=value["updated_at"],
        )
