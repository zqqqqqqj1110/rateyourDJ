from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


METADATA_FIELDS = (
    "title",
    "artist",
    "album",
    "release_year",
    "language",
    "duration_ms",
    "popularity",
)

ALIGNED_FEATURE_FIELDS = (
    "genres",
    "artists",
    "languages",
    "moods",
    "scenes",
    "instruments",
    "vocal_styles",
    "sound_textures",
    "tempo",
    "energy",
)

TOP_LEVEL_PATCH_FIELDS = (
    "metadata",
    "aligned_features",
    "avoid_tags",
    "semantic_tags",
    "source_tags",
    "data_source",
    "confidence_score",
    "embedding_text",
    "embedding",
)


class SongValidationError(ValueError):
    """Raised when an incoming song dictionary violates the L2 schema."""


def empty_metadata() -> dict[str, Any]:
    return {
        "title": "",
        "artist": "",
        "album": "",
        "release_year": None,
        "language": "",
        "duration_ms": None,
        "popularity": None,
    }


def empty_aligned_features() -> dict[str, dict[str, float]]:
    return {name: {} for name in ALIGNED_FEATURE_FIELDS}


def empty_song_dict(song_id: str) -> dict[str, Any]:
    """Return the complete, serializable L2 song profile framework."""
    return {
        "song_id": song_id,
        "metadata": empty_metadata(),
        "aligned_features": empty_aligned_features(),
        "avoid_tags": {},
        "semantic_tags": {},
        "source_tags": {},
        "data_source": {},
        "confidence_score": None,
        "embedding_text": "",
        "embedding": [],
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def song_schema() -> dict[str, Any]:
    """Return a machine-readable description of dictionaries accepted by L2."""
    return {
        "metadata": {
            "title": "string",
            "artist": "string",
            "album": "string",
            "release_year": "integer or null",
            "language": "string",
            "duration_ms": "non-negative integer or null",
            "popularity": "number between 0 and 1 or null",
        },
        "aligned_features": {
            field_name: {"<tag>": "number between 0 and 1"}
            for field_name in ALIGNED_FEATURE_FIELDS
        },
        "avoid_tags": {"<tag>": "number between 0 and 1"},
        "semantic_tags": {"<tag>": "number between 0 and 1"},
        "source_tags": {"<source>": ["string"]},
        "data_source": {"<field>": ["string"]},
        "confidence_score": "number between 0 and 1 or null",
        "embedding_text": "string",
        "embedding": ["number"],
    }


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SongValidationError(f"{path} must be an object")
    return value


def _reject_unknown_keys(
    value: dict[str, Any], allowed: tuple[str, ...], path: str
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise SongValidationError(
            f"{path} contains unknown fields: {', '.join(unknown)}"
        )


def _validate_score(value: Any, path: str, *, nullable: bool = False) -> float | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SongValidationError(f"{path} must be a number")
    score = float(value)
    if not 0 <= score <= 1:
        raise SongValidationError(f"{path} must be between 0 and 1")
    return score


def _validate_weighted_tags(value: Any, path: str) -> dict[str, float]:
    tags = _require_mapping(value, path)
    validated: dict[str, float] = {}
    for tag, score in tags.items():
        if not isinstance(tag, str) or not tag.strip():
            raise SongValidationError(f"{path} keys must be non-empty strings")
        validated[tag.strip()] = _validate_score(score, f"{path}.{tag}")  # type: ignore[assignment]
    return validated


def _validate_metadata(value: Any) -> dict[str, Any]:
    metadata = _require_mapping(value, "metadata")
    _reject_unknown_keys(metadata, METADATA_FIELDS, "metadata")
    validated: dict[str, Any] = {}

    for field_name in ("title", "artist", "album", "language"):
        if field_name in metadata:
            field_value = metadata[field_name]
            if not isinstance(field_value, str):
                raise SongValidationError(f"metadata.{field_name} must be a string")
            validated[field_name] = field_value.strip()

    if "release_year" in metadata:
        release_year = metadata["release_year"]
        if release_year is not None and (
            isinstance(release_year, bool)
            or not isinstance(release_year, int)
            or not 1000 <= release_year <= 9999
        ):
            raise SongValidationError(
                "metadata.release_year must be a four-digit integer or null"
            )
        validated["release_year"] = release_year

    if "duration_ms" in metadata:
        duration_ms = metadata["duration_ms"]
        if duration_ms is not None and (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms < 0
        ):
            raise SongValidationError(
                "metadata.duration_ms must be a non-negative integer or null"
            )
        validated["duration_ms"] = duration_ms

    if "popularity" in metadata:
        validated["popularity"] = _validate_score(
            metadata["popularity"], "metadata.popularity", nullable=True
        )
    return validated


def _validate_aligned_features(value: Any) -> dict[str, dict[str, float]]:
    features = _require_mapping(value, "aligned_features")
    _reject_unknown_keys(features, ALIGNED_FEATURE_FIELDS, "aligned_features")
    return {
        field_name: _validate_weighted_tags(
            field_value, f"aligned_features.{field_name}"
        )
        for field_name, field_value in features.items()
    }


def _validate_tag_sources(value: Any, path: str) -> dict[str, list[str]]:
    sources = _require_mapping(value, path)
    validated: dict[str, list[str]] = {}
    for source, tags_value in sources.items():
        if not isinstance(source, str) or not source.strip():
            raise SongValidationError(f"{path} keys must be non-empty strings")
        if not isinstance(tags_value, list):
            raise SongValidationError(f"{path}.{source} must be an array")
        tags: list[str] = []
        for index, tag in enumerate(tags_value):
            if not isinstance(tag, str) or not tag.strip():
                raise SongValidationError(
                    f"{path}.{source}[{index}] must be a non-empty string"
                )
            normalized = tag.strip()
            if normalized not in tags:
                tags.append(normalized)
        validated[source.strip()] = tags
    return validated


def _validate_embedding(value: Any) -> list[float]:
    if not isinstance(value, list):
        raise SongValidationError("embedding must be an array")
    embedding: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise SongValidationError(f"embedding[{index}] must be a number")
        embedding.append(float(item))
    return embedding


def validate_song_patch(patch: Any) -> dict[str, Any]:
    """Validate and normalize a partial dictionary supplied by L2 collectors."""
    value = _require_mapping(patch, "song patch")
    _reject_unknown_keys(value, TOP_LEVEL_PATCH_FIELDS, "song patch")
    validated: dict[str, Any] = {}

    if "metadata" in value:
        validated["metadata"] = _validate_metadata(value["metadata"])
    if "aligned_features" in value:
        validated["aligned_features"] = _validate_aligned_features(
            value["aligned_features"]
        )
    for field_name in ("avoid_tags", "semantic_tags"):
        if field_name in value:
            validated[field_name] = _validate_weighted_tags(
                value[field_name], field_name
            )
    for field_name in ("source_tags", "data_source"):
        if field_name in value:
            validated[field_name] = _validate_tag_sources(
                value[field_name], field_name
            )
    if "confidence_score" in value:
        validated["confidence_score"] = _validate_score(
            value["confidence_score"], "confidence_score", nullable=True
        )
    if "embedding_text" in value:
        if not isinstance(value["embedding_text"], str):
            raise SongValidationError("embedding_text must be a string")
        validated["embedding_text"] = value["embedding_text"]
    if "embedding" in value:
        validated["embedding"] = _validate_embedding(value["embedding"])
    return validated


@dataclass(slots=True)
class SongProfile:
    song_id: str
    metadata: dict[str, Any] = field(default_factory=empty_metadata)
    aligned_features: dict[str, dict[str, float]] = field(
        default_factory=empty_aligned_features
    )
    avoid_tags: dict[str, float] = field(default_factory=dict)
    semantic_tags: dict[str, float] = field(default_factory=dict)
    source_tags: dict[str, list[str]] = field(default_factory=dict)
    data_source: dict[str, list[str]] = field(default_factory=dict)
    confidence_score: float | None = None
    embedding_text: str = ""
    embedding: list[float] = field(default_factory=list)
    version: int = 1
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "song_id": self.song_id,
            "metadata": deepcopy(self.metadata),
            "aligned_features": deepcopy(self.aligned_features),
            "avoid_tags": deepcopy(self.avoid_tags),
            "semantic_tags": deepcopy(self.semantic_tags),
            "source_tags": deepcopy(self.source_tags),
            "data_source": deepcopy(self.data_source),
            "confidence_score": self.confidence_score,
            "embedding_text": self.embedding_text,
            "embedding": list(self.embedding),
            "version": self.version,
            "updated_at": self.updated_at,
        }

    @classmethod
    def empty(cls, song_id: str) -> SongProfile:
        return cls.from_dict(empty_song_dict(song_id))

    @classmethod
    def from_dict(cls, data: Any) -> SongProfile:
        value = _require_mapping(data, "song profile")
        required = {
            "song_id",
            *TOP_LEVEL_PATCH_FIELDS,
            "version",
            "updated_at",
        }
        missing = sorted(required - set(value))
        unknown = sorted(set(value) - required)
        if missing:
            raise SongValidationError(
                f"song profile is missing fields: {', '.join(missing)}"
            )
        if unknown:
            raise SongValidationError(
                f"song profile contains unknown fields: {', '.join(unknown)}"
            )
        if not isinstance(value["song_id"], str) or not value["song_id"]:
            raise SongValidationError("song_profile.song_id must be a string")
        if (
            isinstance(value["version"], bool)
            or not isinstance(value["version"], int)
            or value["version"] < 1
        ):
            raise SongValidationError(
                "song_profile.version must be a positive integer"
            )
        if not isinstance(value["updated_at"], str):
            raise SongValidationError("song_profile.updated_at must be a string")

        patch = validate_song_patch(
            {key: value[key] for key in TOP_LEVEL_PATCH_FIELDS}
        )
        metadata = empty_metadata()
        metadata.update(patch["metadata"])
        aligned_features = empty_aligned_features()
        aligned_features.update(patch["aligned_features"])
        return cls(
            song_id=value["song_id"],
            metadata=metadata,
            aligned_features=aligned_features,
            avoid_tags=patch["avoid_tags"],
            semantic_tags=patch["semantic_tags"],
            source_tags=patch["source_tags"],
            data_source=patch["data_source"],
            confidence_score=patch["confidence_score"],
            embedding_text=patch["embedding_text"],
            embedding=patch["embedding"],
            version=value["version"],
            updated_at=value["updated_at"],
        )
