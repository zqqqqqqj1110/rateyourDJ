from __future__ import annotations

from typing import Any

from rateyourdj.l2 import JsonSongStore, SongProfile

from .guards import compact, normalized, unique
from .models import AgentRequest


def apply_query_filters(
    ranked_songs: list[dict[str, Any]],
    request: AgentRequest,
    *,
    song_store: JsonSongStore,
    excluded_song_ids: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    artist_counts: dict[str, int] = {}
    for ranked_song in ranked_songs:
        song_id = str(ranked_song["song_id"])
        if song_id in excluded_song_ids:
            continue
        profile = (
            song_store.load(song_id)
            if _song_exists(song_store, song_id)
            else None
        )
        if request.exclude_terms and any(
            song_matches(profile, term)
            if profile is not None
            else ranked_song_matches(ranked_song, term)
            for term in request.exclude_terms
        ):
            continue
        if request.preference_terms and not any(
            (
                song_matches(profile, term)
                if profile is not None
                else ranked_song_matches(ranked_song, term)
            )
            for term in effective_preference_terms(request.preference_terms)
        ):
            continue
        artist = normalized(
            str(
                profile.metadata.get("artist")
                if profile is not None
                else ranked_song.get("artist") or ""
            )
        )
        if artist and artist_counts.get(artist, 0) >= request.max_per_artist:
            continue
        if artist:
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
        result.append(ranked_song)
    return result


def _song_exists(song_store: JsonSongStore, song_id: str) -> bool:
    try:
        return song_store.exists(song_id)
    except ValueError:
        return False


def song_matches(song: SongProfile, term: str) -> bool:
    normalized_term = normalized(term)
    if not normalized_term:
        return False
    compact_term = compact(normalized_term)
    labels = [
        song.metadata.get("title"),
        song.metadata.get("artist"),
        song.metadata.get("album"),
        *song.genres,
        *song.source_tags["lastfm_track_tags"],
        *song.source_tags["lastfm_artist_tags"],
    ]
    for label in labels:
        if not label:
            continue
        normalized_label = normalized(str(label))
        if normalized_term in normalized_label:
            return True
        if compact_term and compact_term in compact(normalized_label):
            return True
    return False


def ranked_song_matches(ranked_song: dict[str, Any], term: str) -> bool:
    normalized_term = normalized(term)
    if not normalized_term:
        return False
    compact_term = compact(normalized_term)
    labels = [
        ranked_song.get("title"),
        ranked_song.get("artist"),
        ranked_song.get("album"),
        *list(ranked_song.get("genres", []) or []),
        *list(ranked_song.get("tags", []) or []),
    ]
    joined_labels = " ".join(str(label) for label in labels if label)
    for label in [*labels, joined_labels]:
        if not label:
            continue
        normalized_label = normalized(str(label))
        if normalized_term in normalized_label:
            return True
        if compact_term and compact_term in compact(normalized_label):
            return True
    return False


def effective_preference_terms(terms: list[str]) -> list[str]:
    aliases = {
        "british rock": "british",
        "uk rock": "british",
        "english rock": "british",
        "britpop": "british",
    }
    expanded = unique(
        [
            resolved
            for term in terms
            for resolved in (
                normalized(term),
                aliases.get(normalized(term), ""),
            )
            if resolved
        ]
    )
    broad_terms = {
        "rock",
        "pop",
        "jazz",
        "electronic",
        "folk",
        "soul",
        "punk",
        "metal",
        "country",
        "blues",
        "classical",
        "ambient",
    }
    specific = [term for term in expanded if term not in broad_terms]
    return specific or expanded
