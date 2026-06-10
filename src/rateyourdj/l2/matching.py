from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


class SourceMatchError(ValueError):
    pass


VERSION_PRIORITY = {
    "remastered": 3,
    "original": 2,
    "live": 1,
    "cover": 1,
    "unknown": 0,
}

_REMASTERED_RE = re.compile(r"\b(?:re-?master(?:ed)?|remaster)\b", re.IGNORECASE)
_LIVE_RE = re.compile(
    r"\b(?:live|concert|unplugged|session|at\s+\w+\s+(?:arena|stadium|hall))\b",
    re.IGNORECASE,
)
_COVER_RE = re.compile(r"\b(?:cover|tribute|karaoke)\b", re.IGNORECASE)
_VERSION_SUFFIX_RE = re.compile(
    r"\s*[-–—([]\s*(?:"
    r"(?:\d{4}\s+)?re-?master(?:ed)?(?:\s+version)?|"
    r"remaster(?:ed)?|live(?:\s+at[^)\]]*)?|"
    r"concert|unplugged|session|cover|tribute|karaoke|"
    r"original(?:\s+version)?"
    r")[^)\]]*[)\]]?\s*$",
    re.IGNORECASE,
)


def classify_version(record: Mapping[str, Any]) -> str:
    text = " ".join(
        str(record.get(field) or "") for field in ("title", "album", "version")
    )
    if _REMASTERED_RE.search(text):
        return "remastered"
    if _LIVE_RE.search(text):
        return "live"
    if _COVER_RE.search(text):
        return "cover"
    if text.strip():
        return "original"
    return "unknown"


def normalize_identity(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("&", " and ")
    value = value.replace("?", " questionmark ")
    value = re.sub(r"\bpt\.?\s*(\d+)\b", r"part \1", value)
    value = _VERSION_SUFFIX_RE.sub("", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def primary_artist(record: Mapping[str, Any]) -> str | None:
    artist = record.get("artist")
    if isinstance(artist, str):
        return artist
    artists = record.get("artists")
    if isinstance(artists, list) and artists and isinstance(artists[0], str):
        return artists[0]
    return None


def records_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_title = normalize_identity(str(left.get("title") or left.get("track") or ""))
    right_title = normalize_identity(
        str(right.get("title") or right.get("track") or "")
    )
    left_artist = normalize_identity(primary_artist(left))
    right_artist = normalize_identity(primary_artist(right))
    return bool(
        left_title
        and right_title
        and left_artist
        and right_artist
        and left_title == right_title
        and left_artist == right_artist
    )


def _completeness(record: Mapping[str, Any]) -> int:
    fields = (
        "spotify_track_id",
        "musicbrainz_recording_id",
        "title",
        "artists",
        "artist",
        "album",
        "release_year",
        "duration_ms",
    )
    return sum(record.get(field) not in (None, "", []) for field in fields)


def select_preferred_version(
    candidates: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
) -> Mapping[str, Any] | None:
    if candidates is None:
        return None
    if isinstance(candidates, Mapping):
        return candidates

    candidate_list = list(candidates)
    if not candidate_list:
        return None
    if not all(isinstance(candidate, Mapping) for candidate in candidate_list):
        raise ValueError("source candidates must be objects")

    return max(
        candidate_list,
        key=lambda candidate: (
            VERSION_PRIORITY[classify_version(candidate)],
            _completeness(candidate),
            float(candidate.get("score") or 0),
        ),
    )


def ensure_cross_source_match(
    records: Iterable[Mapping[str, Any] | None],
) -> bool:
    available = [record for record in records if record is not None]
    for index, left in enumerate(available):
        for right in available[index + 1 :]:
            if not records_match(left, right):
                raise SourceMatchError(
                    "source records do not describe the same title and artist"
                )
    return len(available) >= 2
