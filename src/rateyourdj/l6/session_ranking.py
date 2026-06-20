from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rateyourdj.l2 import JsonSongStore, SongProfile

from .guards import compact, normalized, unique
from .models import AgentRequest
from .sessions import AgentSession


SESSION_MEMORY_RANKING_FIELDS = (
    "preference_terms",
    "exclude_terms",
    "seen_track_ids",
    "seed_track_ids",
    "active_constraints",
    "temporary_feedback",
)


@dataclass(frozen=True, slots=True)
class SessionRankingContext:
    preference_terms: list[str]
    exclude_terms: list[str]
    excluded_song_ids: set[str]
    excluded_track_signatures: set[str]
    seed_track_ids: list[str]
    active_constraints: dict[str, Any]
    temporary_feedback: list[dict[str, Any]]
    ranking_fields: tuple[str, ...] = SESSION_MEMORY_RANKING_FIELDS

    def evidence(self) -> dict[str, Any]:
        return {
            "preference_terms": list(self.preference_terms),
            "exclude_terms": list(self.exclude_terms),
            "seed_track_ids": list(self.seed_track_ids),
            "temporary_feedback_events": [
                str(item.get("event"))
                for item in self.temporary_feedback
                if item.get("event")
            ],
            "active_constraints": dict(self.active_constraints),
        }


def build_session_ranking_context(
    session: AgentSession,
    request: AgentRequest,
    *,
    excluded_song_ids: set[str] | None = None,
) -> SessionRankingContext:
    active_constraints = dict(session.active_constraints)
    feedback_excluded_song_ids = {
        track_id
        for item in session.temporary_feedback
        if str(item.get("event") or "") in {"hide_track", "skipped"}
        for track_id in [str(item.get("track_id") or "").strip()]
        if track_id
    }
    feedback_excluded_artists = [
        normalized(str(item.get("artist") or item.get("value") or ""))
        for item in session.temporary_feedback
        if str(item.get("event") or "") == "hide_artist"
    ]
    effective_excluded_song_ids = set(excluded_song_ids or set())
    if request.exclude_seen or bool(active_constraints.get("exclude_seen")):
        effective_excluded_song_ids.update(session.seen_track_ids)
    effective_excluded_song_ids.update(feedback_excluded_song_ids)
    effective_excluded_track_signatures = {
        normalized(signature)
        for signature in session.seen_track_signatures
        if normalized(signature)
    }
    effective_seed_track_ids = unique(
        [
            *session.seed_track_ids,
            *[
                str(item.get("track_id")).strip()
                for item in session.temporary_feedback
                if str(item.get("event") or "") in {
                    "liked",
                    "saved",
                    "request_similar",
                }
                and str(item.get("track_id") or "").strip()
            ],
        ]
    )
    effective_exclude_terms = unique(
        [
            *session.exclude_terms,
            *request.exclude_terms,
            *request.avoid_artists,
            *[
                artist
                for artist in feedback_excluded_artists
                if artist
            ],
        ]
    )
    return SessionRankingContext(
        preference_terms=list(request.preference_terms),
        exclude_terms=effective_exclude_terms,
        excluded_song_ids=effective_excluded_song_ids,
        excluded_track_signatures=effective_excluded_track_signatures,
        seed_track_ids=effective_seed_track_ids,
        active_constraints=active_constraints,
        temporary_feedback=[dict(item) for item in session.temporary_feedback],
    )


def apply_session_ranking_filters(
    ranked_songs: list[dict[str, Any]],
    request: AgentRequest,
    *,
    song_store: JsonSongStore,
    context: SessionRankingContext,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    artist_counts: dict[str, int] = {}
    seen_signatures: set[str] = set(context.excluded_track_signatures)
    for ranked_song in ranked_songs:
        song_id = str(ranked_song["song_id"])
        if song_id in context.excluded_song_ids:
            continue
        profile = (
            song_store.load(song_id)
            if _song_exists(song_store, song_id)
            else None
        )
        if context.exclude_terms and any(
            song_matches(profile, term)
            if profile is not None
            else ranked_song_matches(ranked_song, term)
            for term in context.exclude_terms
        ):
            continue
        if context.preference_terms and not any(
            (
                song_matches(profile, term)
                if profile is not None
                else ranked_song_matches(ranked_song, term)
            )
            for term in effective_preference_terms(context.preference_terms)
        ):
            continue
        signature = (
            track_signature_from_profile(profile)
            if profile is not None
            else track_signature_from_ranked_song(ranked_song)
        )
        if signature and signature in seen_signatures:
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
        enriched = dict(ranked_song)
        evidence = dict(enriched.get("evidence") or {})
        evidence.update(context.evidence())
        enriched["evidence"] = evidence
        if signature:
            seen_signatures.add(signature)
        result.append(enriched)
    return result


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


def track_signature(title: str, artist: str) -> str:
    normalized_title = normalized(title)
    normalized_artist = normalized(artist)
    if not normalized_title or not normalized_artist:
        return ""
    normalized_title = _strip_version_suffix(normalized_title)
    return f"{normalized_artist}::{normalized_title}"


def track_signature_from_profile(song: SongProfile) -> str:
    return track_signature(
        str(song.metadata.get("title") or ""),
        str(song.metadata.get("artist") or ""),
    )


def track_signature_from_ranked_song(ranked_song: dict[str, Any]) -> str:
    return track_signature(
        str(ranked_song.get("title") or ""),
        str(ranked_song.get("artist") or ""),
    )


def _strip_version_suffix(title: str) -> str:
    stripped = compact(title)
    for marker in (
        "remaster",
        "remastered",
        "deluxe",
        "radio2session",
        "session",
        "live",
        "edit",
        "version",
        "mono",
        "stereo",
    ):
        if marker in stripped:
            stripped = stripped.split(marker, 1)[0]
    return stripped.strip("-_ ") or compact(title)


def _song_exists(song_store: JsonSongStore, song_id: str) -> bool:
    try:
        return song_store.exists(song_id)
    except ValueError:
        return False
