from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


EXTERNAL_ID_FIELDS = (
    "spotify_track_id",
    "musicbrainz_recording_id",
)

VERSION_TYPES = (
    "remastered",
    "original",
    "live",
    "cover",
    "unknown",
)

METADATA_FIELDS = (
    "title",
    "artist",
    "album",
    "release_year",
    "duration_ms",
    "version_type",
)

SOURCE_TAG_FIELDS = (
    "lastfm_track_tags",
    "lastfm_artist_tags",
)

TOP_LEVEL_PATCH_FIELDS = (
    "external_ids",
    "metadata",
    "source_tags",
    "genres",
    "data_source",
    "confidence_score",
)


class SongValidationError(ValueError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_mapping(fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: None for name in fields}


def _validate_keys(
    payload: Mapping[str, Any],
    allowed: tuple[str, ...],
    field_name: str,
) -> None:
    unexpected = sorted(set(payload) - set(allowed))
    if unexpected:
        raise ValueError(f"unsupported {field_name} fields: {unexpected}")


def _validate_score_mapping(value: Any, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    for key, score in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError(f"{field_name}.{key} must be numeric")
        if not 0 <= float(score) <= 1:
            raise ValueError(f"{field_name}.{key} must be between 0 and 1")


def _validate_string_list_mapping(value: Any, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    for key, sources in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if not isinstance(sources, list) or not all(
            isinstance(source, str) and source.strip() for source in sources
        ):
            raise ValueError(f"{field_name}.{key} must be a list of strings")


def empty_song_dict(song_id: str) -> dict[str, Any]:
    return SongProfile.empty(song_id).to_dict()


def song_schema() -> dict[str, Any]:
    return {
        "song_id": "string",
        "external_ids": {name: "string | null" for name in EXTERNAL_ID_FIELDS},
        "metadata": {
            "title": "string | null",
            "artist": "string | null",
            "album": "string | null",
            "release_year": "integer | null",
            "duration_ms": "integer | null",
            "version_type": f"{' | '.join(VERSION_TYPES)} | null",
        },
        "source_tags": {
            name: "{tag: score[0..1]}" for name in SOURCE_TAG_FIELDS
        },
        "genres": "{normalized_genre: score[0..1]}",
        "data_source": "{field_path: [source_name, ...]}",
        "confidence_score": "number[0..1] | null",
        "version": "integer",
        "updated_at": "ISO-8601 string",
    }


def validate_song_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        raise SongValidationError("song patch must be an object")
    unexpected = sorted(set(patch) - set(TOP_LEVEL_PATCH_FIELDS))
    if unexpected:
        raise SongValidationError(f"unsupported song patch fields: {unexpected}")

    normalized: dict[str, Any] = {}
    try:
        for section_name, allowed_fields in (
            ("external_ids", EXTERNAL_ID_FIELDS),
            ("metadata", METADATA_FIELDS),
            ("source_tags", SOURCE_TAG_FIELDS),
        ):
            if section_name not in patch:
                continue
            section = patch[section_name]
            if not isinstance(section, Mapping):
                raise ValueError(f"{section_name} must be an object")
            _validate_keys(section, allowed_fields, section_name)
            normalized[section_name] = dict(section)

        if "source_tags" in normalized:
            for field_name, value in normalized["source_tags"].items():
                _validate_score_mapping(value, f"source_tags.{field_name}")
                normalized["source_tags"][field_name] = dict(value)

        if "genres" in patch:
            _validate_score_mapping(patch["genres"], "genres")
            normalized["genres"] = dict(patch["genres"])
        if "data_source" in patch:
            _validate_string_list_mapping(patch["data_source"], "data_source")
            normalized["data_source"] = {
                key: list(value) for key, value in patch["data_source"].items()
            }
        if "confidence_score" in patch:
            score = patch["confidence_score"]
            if score is not None and (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not 0 <= float(score) <= 1
            ):
                raise ValueError("confidence_score must be between 0 and 1 or null")
            normalized["confidence_score"] = score

        candidate = SongProfile.empty("validation")
        candidate.external_ids.update(normalized.get("external_ids", {}))
        candidate.metadata.update(normalized.get("metadata", {}))
        for key, value in normalized.get("source_tags", {}).items():
            candidate.source_tags[key] = value
        candidate.genres.update(normalized.get("genres", {}))
        candidate.data_source.update(normalized.get("data_source", {}))
        if "confidence_score" in normalized:
            candidate.confidence_score = normalized["confidence_score"]
        candidate.validate()
    except ValueError as error:
        raise SongValidationError(str(error)) from error
    return normalized


@dataclass(slots=True)
class SongProfile:
    song_id: str
    external_ids: dict[str, str | None] = field(
        default_factory=lambda: _empty_mapping(EXTERNAL_ID_FIELDS)
    )
    metadata: dict[str, Any] = field(
        default_factory=lambda: _empty_mapping(METADATA_FIELDS)
    )
    source_tags: dict[str, dict[str, float]] = field(
        default_factory=lambda: {name: {} for name in SOURCE_TAG_FIELDS}
    )
    genres: dict[str, float] = field(default_factory=dict)
    data_source: dict[str, list[str]] = field(default_factory=dict)
    confidence_score: float | None = None
    version: int = 1
    updated_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def empty(cls, song_id: str) -> "SongProfile":
        return cls(song_id=song_id)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SongProfile":
        required = {
            "song_id",
            "external_ids",
            "metadata",
            "source_tags",
            "genres",
            "data_source",
            "confidence_score",
            "version",
            "updated_at",
        }
        missing = sorted(required - set(payload))
        unexpected = sorted(set(payload) - required)
        if missing:
            raise ValueError(f"missing song profile fields: {missing}")
        if unexpected:
            raise ValueError(f"unsupported song profile fields: {unexpected}")

        external_ids = payload["external_ids"]
        metadata = payload["metadata"]
        source_tags = payload["source_tags"]
        if not isinstance(external_ids, Mapping):
            raise ValueError("external_ids must be an object")
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object")
        if not isinstance(source_tags, Mapping):
            raise ValueError("source_tags must be an object")

        _validate_keys(external_ids, EXTERNAL_ID_FIELDS, "external_ids")
        _validate_keys(metadata, METADATA_FIELDS, "metadata")
        _validate_keys(source_tags, SOURCE_TAG_FIELDS, "source_tags")

        profile = cls(
            song_id=payload["song_id"],
            external_ids=dict(external_ids),
            metadata=dict(metadata),
            source_tags={
                name: dict(source_tags.get(name, {})) for name in SOURCE_TAG_FIELDS
            },
            genres=dict(payload["genres"]),
            data_source={
                key: list(value) for key, value in payload["data_source"].items()
            },
            confidence_score=payload["confidence_score"],
            version=payload["version"],
            updated_at=payload["updated_at"],
        )
        profile.validate()
        return profile

    def validate(self) -> None:
        if not isinstance(self.song_id, str) or not self.song_id.strip():
            raise ValueError("song_id must be a non-empty string")
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise ValueError("version must be an integer")
        if not isinstance(self.updated_at, str) or not self.updated_at:
            raise ValueError("updated_at must be a non-empty string")

        _validate_keys(self.external_ids, EXTERNAL_ID_FIELDS, "external_ids")
        _validate_keys(self.metadata, METADATA_FIELDS, "metadata")
        _validate_keys(self.source_tags, SOURCE_TAG_FIELDS, "source_tags")

        for field_name, value in self.external_ids.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(f"external_ids.{field_name} must be a string or null")

        for field_name in ("title", "artist", "album"):
            value = self.metadata.get(field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"metadata.{field_name} must be a string or null")
        for field_name in ("release_year", "duration_ms"):
            value = self.metadata.get(field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise ValueError(f"metadata.{field_name} must be an integer or null")
            if value is not None and value < 0:
                raise ValueError(
                    f"metadata.{field_name} must be non-negative or null"
                )

        version_type = self.metadata.get("version_type")
        if version_type is not None and version_type not in VERSION_TYPES:
            raise ValueError(
                f"metadata.version_type must be one of {list(VERSION_TYPES)} or null"
            )

        for field_name, value in self.source_tags.items():
            _validate_score_mapping(value, f"source_tags.{field_name}")
        _validate_score_mapping(self.genres, "genres")
        _validate_string_list_mapping(self.data_source, "data_source")

        if self.confidence_score is not None:
            if not isinstance(self.confidence_score, (int, float)) or isinstance(
                self.confidence_score, bool
            ):
                raise ValueError("confidence_score must be numeric or null")
            if not 0 <= float(self.confidence_score) <= 1:
                raise ValueError("confidence_score must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "song_id": self.song_id,
            "external_ids": dict(self.external_ids),
            "metadata": dict(self.metadata),
            "source_tags": {
                name: dict(self.source_tags[name]) for name in SOURCE_TAG_FIELDS
            },
            "genres": dict(self.genres),
            "data_source": {
                key: list(value) for key, value in self.data_source.items()
            },
            "confidence_score": self.confidence_score,
            "version": self.version,
            "updated_at": self.updated_at,
        }
