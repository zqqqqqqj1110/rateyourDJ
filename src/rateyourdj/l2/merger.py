from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .matching import (
    VERSION_PRIORITY,
    classify_version,
    ensure_cross_source_match,
    primary_artist,
    select_preferred_version,
)
from .models import SongProfile
from .normalizers import GenreNormalizer, normalize_tag_scores


SourceInput = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None


class SongDataMerger:
    def __init__(self, genre_normalizer: GenreNormalizer | None = None) -> None:
        self.genre_normalizer = genre_normalizer or GenreNormalizer()

    def merge(
        self,
        song_id: str,
        *,
        spotify: SourceInput = None,
        musicbrainz: SourceInput = None,
        lastfm: SourceInput = None,
    ) -> SongProfile:
        spotify_record = select_preferred_version(spotify)
        musicbrainz_record = select_preferred_version(musicbrainz)
        lastfm_record = select_preferred_version(lastfm)
        if not any((spotify_record, musicbrainz_record, lastfm_record)):
            raise ValueError("at least one source record is required")

        identity_records = [
            record
            for record in (spotify_record, musicbrainz_record, lastfm_record)
            if self._has_identity(record)
        ]
        sources_agree = ensure_cross_source_match(identity_records)

        versioned_records = [
            record
            for record in (spotify_record, musicbrainz_record)
            if record is not None
        ]
        metadata_record = (
            max(
                versioned_records,
                key=lambda record: VERSION_PRIORITY[classify_version(record)],
            )
            if versioned_records
            else lastfm_record
        )
        assert metadata_record is not None
        metadata_sources = (
            (metadata_record, spotify_record, musicbrainz_record, lastfm_record)
        )
        artist = primary_artist(metadata_record)
        track_tags = self._tag_payload(lastfm_record, "track_tags")
        artist_tags = self._tag_payload(lastfm_record, "artist_tags")
        normalized_track_tags = normalize_tag_scores(track_tags)
        normalized_artist_tags = normalize_tag_scores(artist_tags)
        genres = self.genre_normalizer.normalize(
            track_tags,
            artist_tags,
            artist=artist,
        )

        profile = SongProfile.empty(song_id)
        profile.external_ids.update(
            {
                "spotify_track_id": self._value(
                    spotify_record, "spotify_track_id"
                ),
                "musicbrainz_recording_id": self._value(
                    musicbrainz_record, "musicbrainz_recording_id"
                ),
            }
        )
        profile.metadata.update(
            {
                "title": self._first_value(
                    metadata_sources,
                    ("title", "track"),
                ),
                "artist": self._first_artist(
                    *metadata_sources
                ),
                "album": self._first_value(
                    metadata_sources, ("album",)
                ),
                "release_year": self._first_value(
                    metadata_sources, ("release_year",)
                ),
                "duration_ms": self._first_value(
                    metadata_sources, ("duration_ms",)
                ),
                "version_type": classify_version(metadata_record),
            }
        )
        profile.source_tags.update(
            {
                "lastfm_track_tags": normalized_track_tags,
                "lastfm_artist_tags": normalized_artist_tags,
            }
        )
        profile.genres = genres
        profile.data_source = self._build_data_source(
            spotify_record,
            musicbrainz_record,
            lastfm_record,
            profile,
        )
        profile.confidence_score = self._confidence(
            spotify_record=spotify_record,
            musicbrainz_record=musicbrainz_record,
            lastfm_record=lastfm_record,
            sources_agree=sources_agree,
            profile=profile,
        )
        profile.validate()
        return profile

    @staticmethod
    def _has_identity(record: Mapping[str, Any] | None) -> bool:
        return bool(
            record
            and (record.get("title") or record.get("track"))
            and primary_artist(record)
        )

    @staticmethod
    def _value(
        record: Mapping[str, Any] | None,
        key: str,
    ) -> Any:
        return record.get(key) if record else None

    @staticmethod
    def _tag_payload(
        record: Mapping[str, Any] | None,
        key: str,
    ) -> Any:
        if not record:
            return None
        return record.get(key) or record.get(f"normalized_{key}")

    @staticmethod
    def _first_value(
        records: Sequence[Mapping[str, Any] | None],
        keys: Sequence[str],
    ) -> Any:
        for record in records:
            if not record:
                continue
            for key in keys:
                value = record.get(key)
                if value not in (None, "", []):
                    return value
        return None

    @staticmethod
    def _first_artist(
        *records: Mapping[str, Any] | None,
    ) -> str | None:
        for record in records:
            if record:
                artist = primary_artist(record)
                if artist:
                    return artist
        return None

    @staticmethod
    def _build_data_source(
        spotify_record: Mapping[str, Any] | None,
        musicbrainz_record: Mapping[str, Any] | None,
        lastfm_record: Mapping[str, Any] | None,
        profile: SongProfile,
    ) -> dict[str, list[str]]:
        sources: dict[str, list[str]] = {}
        source_records = (
            ("Spotify", spotify_record),
            ("MusicBrainz", musicbrainz_record),
            ("Last.fm", lastfm_record),
        )
        for field_name in ("title", "artist", "album", "release_year", "duration_ms"):
            field_sources = []
            for source_name, record in source_records:
                if not record:
                    continue
                source_value = (
                    primary_artist(record)
                    if field_name == "artist"
                    else record.get(field_name)
                    or (record.get("track") if field_name == "title" else None)
                )
                if source_value not in (None, "", []):
                    field_sources.append(source_name)
            if field_sources:
                sources[f"metadata.{field_name}"] = field_sources

        if profile.external_ids["spotify_track_id"]:
            sources["external_ids.spotify_track_id"] = ["Spotify"]
        if profile.external_ids["musicbrainz_recording_id"]:
            sources["external_ids.musicbrainz_recording_id"] = ["MusicBrainz"]
        if lastfm_record:
            sources["source_tags"] = ["Last.fm"]
        if profile.genres:
            sources["genres"] = ["Last.fm", "GenreNormalizer"]
        version_sources = [
            source_name
            for source_name, record in source_records[:2]
            if record is not None
            and classify_version(record) == profile.metadata["version_type"]
        ]
        if version_sources:
            sources["metadata.version_type"] = version_sources
        return sources

    @staticmethod
    def _confidence(
        *,
        spotify_record: Mapping[str, Any] | None,
        musicbrainz_record: Mapping[str, Any] | None,
        lastfm_record: Mapping[str, Any] | None,
        sources_agree: bool,
        profile: SongProfile,
    ) -> float:
        score = 0.0
        if spotify_record:
            score += 0.2
        if musicbrainz_record:
            source_score = float(musicbrainz_record.get("score") or 100)
            score += 0.2 * max(0.0, min(source_score / 100, 1.0))
        if lastfm_record and (
            profile.source_tags["lastfm_track_tags"]
            or profile.source_tags["lastfm_artist_tags"]
        ):
            score += 0.15
        if sources_agree:
            score += 0.25

        metadata_fields = ("title", "artist", "album", "release_year", "duration_ms")
        completeness = sum(
            profile.metadata[field] not in (None, "", [])
            for field in metadata_fields
        ) / len(metadata_fields)
        score += 0.1 * completeness
        if profile.genres:
            score += 0.1
        return round(min(score, 1.0), 4)
