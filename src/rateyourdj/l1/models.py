from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


LONG_TERM_FIELDS = (
    "genres",
    "artists",
    "languages",
    "moods",
    "scenes",
    "instruments",
    "vocal_styles",
    "sound_textures",
    "tempo_preference",
    "energy_preference",
)

SHORT_TERM_LIST_FIELDS = (
    "reference_songs",
    "reference_artists",
    "genres",
    "artists",
    "languages",
    "moods",
    "scenes",
    "instruments",
    "vocal_styles",
    "sound_textures",
    "tempo_preference",
    "energy_preference",
    "must_have",
    "avoid",
)

NEGATIVE_FIELDS = (
    "genres",
    "artists",
    "moods",
    "instruments",
    "vocal_styles",
    "sound_textures",
    "tempo_preference",
    "energy_preference",
)

EXPLORATION_LEVELS = ("safe", "balanced", "exploratory")
FEEDBACK_TYPES = (
    "play",
    "normal_play",
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
    "query",
    "timestamp",
    "song_tags",
    "reward_score",
    "play_duration_ms",
    "completion_rate",
    "recommendation_context",
)
TOP_LEVEL_IMPORT_FIELDS = (
    "long_term_preference",
    "short_term_intent",
    "negative_preference",
    "feedback_memory",
)


class ProfileValidationError(ValueError):
    """Raised when an incoming profile dictionary violates the L1 schema."""


def empty_weighted_preferences(
    fields: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    return {name: {} for name in fields}


def empty_short_term_intent() -> dict[str, Any]:
    intent: dict[str, Any] = {
        name: [] for name in SHORT_TERM_LIST_FIELDS
    }
    intent["exploration_level"] = "balanced"
    return intent


def empty_profile_dict(user_id: str) -> dict[str, Any]:
    """Return the complete, serializable L1 profile framework."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "user_id": user_id,
        "long_term_preference": empty_weighted_preferences(LONG_TERM_FIELDS),
        "short_term_intent": empty_short_term_intent(),
        "negative_preference": empty_weighted_preferences(NEGATIVE_FIELDS),
        "feedback_memory": [],
        "version": 1,
        "updated_at": now,
    }


def profile_schema() -> dict[str, Any]:
    """Return a machine-readable description of dictionaries accepted by L1."""
    return {
        "long_term_preference": {
            field_name: {"<tag>": "number between 0 and 1"}
            for field_name in LONG_TERM_FIELDS
        },
        "short_term_intent": {
            **{field_name: ["string"] for field_name in SHORT_TERM_LIST_FIELDS},
            "exploration_level": list(EXPLORATION_LEVELS),
        },
        "negative_preference": {
            field_name: {"<tag>": "number between 0 and 1"}
            for field_name in NEGATIVE_FIELDS
        },
        "feedback_memory": [
            {
                "feedback_type": list(FEEDBACK_TYPES),
                "song_id": "string",
                "query": "string",
                "timestamp": "ISO 8601 string",
                "song_tags": "object supplied by L2/L7",
                "reward_score": "number supplied by L7",
                "play_duration_ms": "non-negative integer",
                "completion_rate": "number between 0 and 1",
                "recommendation_context": "object supplied by L6/L7",
            }
        ],
    }


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError(f"{path} must be an object")
    return value


def _reject_unknown_keys(
    value: dict[str, Any], allowed: tuple[str, ...], path: str
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ProfileValidationError(
            f"{path} contains unknown fields: {', '.join(unknown)}"
        )


def _validate_weighted_section(
    value: Any, fields: tuple[str, ...], path: str
) -> dict[str, dict[str, float]]:
    section = _require_mapping(value, path)
    _reject_unknown_keys(section, fields, path)
    validated: dict[str, dict[str, float]] = {}
    for field_name, weights_value in section.items():
        weights = _require_mapping(weights_value, f"{path}.{field_name}")
        validated[field_name] = {}
        for tag, weight in weights.items():
            if not isinstance(tag, str) or not tag.strip():
                raise ProfileValidationError(
                    f"{path}.{field_name} keys must be non-empty strings"
                )
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise ProfileValidationError(
                    f"{path}.{field_name}.{tag} must be a number"
                )
            numeric_weight = float(weight)
            if not 0 <= numeric_weight <= 1:
                raise ProfileValidationError(
                    f"{path}.{field_name}.{tag} must be between 0 and 1"
                )
            validated[field_name][tag.strip()] = numeric_weight
    return validated


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


def _validate_feedback_record(value: Any, path: str) -> dict[str, Any]:
    record = _require_mapping(value, path)
    _reject_unknown_keys(record, FEEDBACK_RECORD_FIELDS, path)
    validated = deepcopy(record)

    string_fields = ("song_id", "query", "timestamp")
    for field_name in string_fields:
        if field_name in record and not isinstance(record[field_name], str):
            raise ProfileValidationError(f"{path}.{field_name} must be a string")

    if "feedback_type" in record and record["feedback_type"] not in FEEDBACK_TYPES:
        raise ProfileValidationError(
            f"{path}.feedback_type must be one of "
            + ", ".join(FEEDBACK_TYPES)
        )

    for field_name in ("song_tags", "recommendation_context"):
        if field_name in record and not isinstance(record[field_name], dict):
            raise ProfileValidationError(f"{path}.{field_name} must be an object")

    if "reward_score" in record and (
        isinstance(record["reward_score"], bool)
        or not isinstance(record["reward_score"], (int, float))
    ):
        raise ProfileValidationError(f"{path}.reward_score must be a number")

    if "play_duration_ms" in record and (
        isinstance(record["play_duration_ms"], bool)
        or not isinstance(record["play_duration_ms"], int)
        or record["play_duration_ms"] < 0
    ):
        raise ProfileValidationError(
            f"{path}.play_duration_ms must be a non-negative integer"
        )

    if "completion_rate" in record:
        completion_rate = record["completion_rate"]
        if (
            isinstance(completion_rate, bool)
            or not isinstance(completion_rate, (int, float))
            or not 0 <= completion_rate <= 1
        ):
            raise ProfileValidationError(
                f"{path}.completion_rate must be between 0 and 1"
            )
    return validated


def validate_profile_patch(patch: Any) -> dict[str, Any]:
    """Validate and normalize a partial dictionary supplied by L2/L6/L7."""
    value = _require_mapping(patch, "profile patch")
    _reject_unknown_keys(value, TOP_LEVEL_IMPORT_FIELDS, "profile patch")
    validated: dict[str, Any] = {}

    if "long_term_preference" in value:
        validated["long_term_preference"] = _validate_weighted_section(
            value["long_term_preference"],
            LONG_TERM_FIELDS,
            "long_term_preference",
        )

    if "short_term_intent" in value:
        intent = _require_mapping(
            value["short_term_intent"], "short_term_intent"
        )
        allowed = SHORT_TERM_LIST_FIELDS + ("exploration_level",)
        _reject_unknown_keys(intent, allowed, "short_term_intent")
        validated_intent: dict[str, Any] = {}
        for field_name, field_value in intent.items():
            if field_name == "exploration_level":
                if field_value not in EXPLORATION_LEVELS:
                    raise ProfileValidationError(
                        "short_term_intent.exploration_level must be one of "
                        + ", ".join(EXPLORATION_LEVELS)
                    )
                validated_intent[field_name] = field_value
            else:
                validated_intent[field_name] = _validate_string_list(
                    field_value, f"short_term_intent.{field_name}"
                )
        validated["short_term_intent"] = validated_intent

    if "negative_preference" in value:
        validated["negative_preference"] = _validate_weighted_section(
            value["negative_preference"],
            NEGATIVE_FIELDS,
            "negative_preference",
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


@dataclass(slots=True)
class UserProfile:
    user_id: str
    long_term_preference: dict[str, dict[str, float]] = field(
        default_factory=lambda: empty_weighted_preferences(LONG_TERM_FIELDS)
    )
    short_term_intent: dict[str, Any] = field(
        default_factory=empty_short_term_intent
    )
    negative_preference: dict[str, dict[str, float]] = field(
        default_factory=lambda: empty_weighted_preferences(NEGATIVE_FIELDS)
    )
    feedback_memory: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "long_term_preference": deepcopy(self.long_term_preference),
            "short_term_intent": deepcopy(self.short_term_intent),
            "negative_preference": deepcopy(self.negative_preference),
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
        required = {
            "user_id",
            "long_term_preference",
            "short_term_intent",
            "negative_preference",
            "feedback_memory",
            "version",
            "updated_at",
        }
        missing = sorted(required - set(value))
        unknown = sorted(set(value) - required)
        if missing:
            raise ProfileValidationError(
                f"profile is missing fields: {', '.join(missing)}"
            )
        if unknown:
            raise ProfileValidationError(
                f"profile contains unknown fields: {', '.join(unknown)}"
            )
        if not isinstance(value["user_id"], str) or not value["user_id"]:
            raise ProfileValidationError("profile.user_id must be a string")
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
            {
                key: value[key]
                for key in TOP_LEVEL_IMPORT_FIELDS
            }
        )
        long_term = empty_weighted_preferences(LONG_TERM_FIELDS)
        long_term.update(patch["long_term_preference"])
        short_term = empty_short_term_intent()
        short_term.update(patch["short_term_intent"])
        negative = empty_weighted_preferences(NEGATIVE_FIELDS)
        negative.update(patch["negative_preference"])
        return cls(
            user_id=value["user_id"],
            long_term_preference=long_term,
            short_term_intent=short_term,
            negative_preference=negative,
            feedback_memory=patch["feedback_memory"],
            version=value["version"],
            updated_at=value["updated_at"],
        )
