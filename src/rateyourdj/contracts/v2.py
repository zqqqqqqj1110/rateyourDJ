"""V2 data contract helpers (data-contract.md "V2 契约：长尾推荐重构").

Pure-python, dependency-free: ID rules, popularity buckets, lightweight
validators for every V2 record, the eval-only guard for GRPO samples and
read adapters from v1 JSON.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = [
    "BUCKET_VERSION",
    "BUCKET_THRESHOLDS",
    "BUCKETS",
    "CHANNELS",
    "FEEDBACK_EVENT_TYPES",
    "REJECT_REASONS",
    "SCHEMA_VERSIONS",
    "ContractError",
    "normalize_name",
    "make_song_id",
    "is_valid_internal_id",
    "assign_bucket",
    "percentile_ranks",
    "validate_record",
    "strip_eval_only",
    "user_context_from_v1",
    "song_profile_from_v1",
]

SCHEMA_VERSIONS = {
    "user_context": "user-context/v2",
    "song_profile": "song-profile/v2",
    "retrieval_candidate": "retrieval-candidate/v2",
    "impression": "impression/v2",
    "feedback": "feedback/v2",
    "sft_sample": "sft-sample/v2",
    "grpo_sample": "grpo-sample/v2",
}

BUCKET_VERSION = "bucket-v2"
# global_percentile: 1.0 = most listened, measured against ListenBrainz-wide listener
# counts. bucket-v2 (2026-09-27): head = global top 1%, mid = top 1–10%, tail = rest.
# (bucket-v1 used top 10% / 50%; with the real global distribution — half of all
# listened recordings have <= 5 listeners — that left only 0.3% of the catalog as tail.)
BUCKET_THRESHOLDS = {"head": 0.99, "mid": 0.90}
BUCKETS = ("head", "mid", "tail", "unknown")
CHANNELS = ("rule", "semantic", "tail", "explore")
PLAYBACK_SOURCES = ("spotify", "youtube", "none")
FEEDBACK_EVENT_TYPES = (
    "play_start", "play_progress", "completed", "quick_skip", "liked", "saved", "hide",
)
REJECT_REASONS = ("dislike_song", "not_in_mood_to_explore", "other")
FEEDBACK_PHASES = ("dev", "final")
SPLITS = ("train", "val", "test")

_INTERNAL_ID = re.compile(r"[A-Za-z0-9_.-]+")


class ContractError(ValueError):
    """A record does not satisfy the V2 contract."""


# ---------------------------------------------------------------- IDs


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def make_song_id(*, recording_mbid: str | None = None,
                 artist: str | None = None, title: str | None = None) -> tuple[str, str]:
    """Return ``(song_id, id_basis)`` following §9.1."""
    if recording_mbid:
        key, basis = "mb:" + recording_mbid.strip().lower(), "mbid"
    elif artist and title:
        key, basis = "na:" + normalize_name(artist) + "|" + normalize_name(title), "name"
    else:
        raise ContractError("need recording_mbid, or both artist and title")
    return "s_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16], basis


def is_valid_internal_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_INTERNAL_ID.fullmatch(value))


# ---------------------------------------------------------------- buckets


def assign_bucket(global_percentile: float | None) -> str:
    if global_percentile is None:
        return "unknown"
    if not 0.0 <= float(global_percentile) <= 1.0:
        raise ContractError("global_percentile must be within [0, 1]")
    if global_percentile >= BUCKET_THRESHOLDS["head"]:
        return "head"
    if global_percentile >= BUCKET_THRESHOLDS["mid"]:
        return "mid"
    return "tail"


def percentile_ranks(counts: Mapping[str, int | None]) -> dict[str, float | None]:
    """Map id -> percentile in [0, 1] (1.0 = most listened); ties share a rank.

    Items with a missing count get ``None`` (bucket ``unknown``).
    """
    known = sorted((c, k) for k, c in counts.items() if c is not None)
    result: dict[str, float | None] = {k: None for k, c in counts.items() if c is None}
    n = len(known)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and known[j + 1][0] == known[i][0]:
            j += 1
        # fraction of items strictly below + half the ties, normalised to [0, 1]
        pct = 1.0 if n == 1 else (i + j) / 2 / (n - 1)
        for _, key in known[i : j + 1]:
            result[key] = round(pct, 6)
        i = j + 1
    return result


# ---------------------------------------------------------------- validation


def _require(record: Mapping[str, Any], fields: tuple[str, ...], kind: str) -> None:
    missing = [f for f in fields if f not in record]
    if missing:
        raise ContractError(f"{kind}: missing fields {missing}")


def _enum(value: Any, allowed: tuple[str, ...], where: str) -> None:
    if value not in allowed:
        raise ContractError(f"{where} must be one of {list(allowed)}, got {value!r}")


def _id(value: Any, where: str) -> None:
    if not is_valid_internal_id(value):
        raise ContractError(f"{where} must match [A-Za-z0-9_.-]+, got {value!r}")


def _unit(value: Any, where: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise ContractError(f"{where} must be a number in [0, 1]")


def validate_record(kind: str, record: Mapping[str, Any]) -> None:
    """Raise ContractError when ``record`` violates the V2 contract for ``kind``."""
    if kind not in SCHEMA_VERSIONS:
        raise ContractError(f"unknown kind {kind!r}")
    if record.get("schema_version") != SCHEMA_VERSIONS[kind]:
        raise ContractError(
            f"{kind}: schema_version must be {SCHEMA_VERSIONS[kind]!r}"
        )
    globals()["_validate_" + kind](record)


def _validate_user_context(r: Mapping[str, Any]) -> None:
    _require(r, ("user_id", "seed_branches", "exploration_level", "exclusions"), "user_context")
    _id(r["user_id"], "user_id")
    _unit(r["exploration_level"], "exploration_level")
    branch_ids = set()
    for branch in r["seed_branches"]:
        _require(branch, ("branch_id", "seed_song_ids"), "seed_branch")
        if branch["branch_id"] in branch_ids:
            raise ContractError(f"duplicate branch_id {branch['branch_id']!r}")
        branch_ids.add(branch["branch_id"])
        for sid in branch["seed_song_ids"]:
            _id(sid, "seed_song_ids[]")


def _validate_song_profile(r: Mapping[str, Any]) -> None:
    _require(r, ("song_id", "id_basis", "title", "artists", "external_ids",
                 "popularity", "playback", "provenance"), "song_profile")
    _id(r["song_id"], "song_id")
    _enum(r["id_basis"], ("mbid", "name"), "id_basis")
    pop = r["popularity"]
    _enum(pop.get("bucket"), BUCKETS, "popularity.bucket")
    pct = pop.get("global_percentile")
    if pct is not None:
        _unit(pct, "popularity.global_percentile")
        if pop.get("bucket_version") == BUCKET_VERSION and assign_bucket(pct) != pop["bucket"]:
            raise ContractError("popularity.bucket disagrees with global_percentile")
    elif pop.get("bucket") != "unknown":
        raise ContractError("bucket must be 'unknown' when global_percentile is missing")
    play = r["playback"]
    _enum(play.get("source"), PLAYBACK_SOURCES, "playback.source")
    if play["source"] != "none" and not play.get("verified"):
        raise ContractError("playback with a source must be verified; else use source 'none'")


def _validate_retrieval_candidate(r: Mapping[str, Any]) -> None:
    _require(r, ("request_id", "candidate_set_id", "song_id", "bucket", "channels",
                 "relevance", "tail_score", "evidence"), "retrieval_candidate")
    _id(r["song_id"], "song_id")
    _enum(r["bucket"], BUCKETS, "bucket")
    if not r["channels"]:
        raise ContractError("channels must not be empty")
    for ch in r["channels"]:
        _enum(ch.get("channel"), CHANNELS, "channels[].channel")
    _unit(r["relevance"], "relevance")
    _unit(r["tail_score"], "tail_score")
    for ev in r["evidence"]:
        _require(ev, ("type", "detail", "ref"), "evidence")


def _validate_impression(r: Mapping[str, Any]) -> None:
    _require(r, ("impression_id", "user_id", "run_id", "song_id", "rank", "bucket",
                 "channel", "strategy_version", "shown_at"), "impression")
    _id(r["song_id"], "song_id")
    _enum(r["bucket"], BUCKETS, "bucket")
    inter = r.get("interleaving")
    if inter is not None:
        _require(inter, ("pair_id", "arm", "arms"), "interleaving")
        if inter["arm"] not in inter["arms"]:
            raise ContractError("interleaving.arm must be a key of interleaving.arms")


def _validate_feedback(r: Mapping[str, Any]) -> None:
    _require(r, ("feedback_id", "impression_id", "user_id", "song_id", "events", "phase"),
             "feedback")
    if not r["impression_id"]:
        raise ContractError("feedback requires an impression_id (no impression, no feedback)")
    _enum(r["phase"], FEEDBACK_PHASES, "phase")
    for ev in r["events"]:
        _enum(ev.get("type"), FEEDBACK_EVENT_TYPES, "events[].type")
    survey = r.get("survey") or {}
    if survey.get("reject_reason") is not None:
        _enum(survey["reject_reason"], REJECT_REASONS, "survey.reject_reason")
    if survey.get("heard_before") is not None:
        _enum(survey["heard_before"], ("yes", "no", "unsure"), "survey.heard_before")


def _validate_sft_sample(r: Mapping[str, Any]) -> None:
    _require(r, ("sample_id", "messages", "meta"), "sft_sample")
    _enum(r["meta"].get("split"), SPLITS, "meta.split")
    for msg in r["messages"]:
        if "thought" in msg:
            raise ContractError("SFT messages must not carry hidden thoughts")


def _validate_grpo_sample(r: Mapping[str, Any]) -> None:
    _require(r, ("sample_id", "prompt", "candidates", "constraints",
                 "reward_spec_version", "meta"), "grpo_sample")
    _enum(r["meta"].get("split"), SPLITS, "meta.split")
    if not r["candidates"]:
        raise ContractError("candidates must not be empty")


def strip_eval_only(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Drop ``eval_only`` (hidden positives) before a sample reaches training/reward."""
    return {k: v for k, v in sample.items() if k != "eval_only"}


# ---------------------------------------------------------------- v1 adapters


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def user_context_from_v1(profile: Mapping[str, Any], *, exploration_level: float = 0.5
                         ) -> tuple[dict[str, Any], list[str]]:
    """Read a v1 L1 profile as UserContextV2.

    Returns ``(context, candidate_seed_external_ids)``. Seeds are NOT assigned to
    branches automatically (§9.11: needs manual confirmation); the v1 collection
    is returned as candidates. Preference weights are not migrated.
    """
    if profile.get("schema_version") == SCHEMA_VERSIONS["user_context"]:
        return dict(profile), []
    user_id = profile.get("user_id")
    _id(user_id, "user_id")
    candidates = [str(x) for x in profile.get("collection_song_ids") or []]
    context = {
        "schema_version": SCHEMA_VERSIONS["user_context"],
        "user_id": user_id,
        "seed_branches": [],
        "exploration_level": exploration_level,
        "exclusions": {"song_ids": [], "artists_mbid": [], "tags": []},
        "heard_song_ids": [],
        "recommended_song_ids": [],
        "recent_feedback_ids": [],
        "migrated_from": {"version": "v1", "v1_version": profile.get("version")},
        "created_at": profile.get("updated_at") or _now(),
        "updated_at": _now(),
    }
    validate_record("user_context", context)
    return context, candidates


def song_profile_from_v1(song: Mapping[str, Any]) -> dict[str, Any]:
    """Read a v1 L2 song profile as SongProfileV2 (bucket 'unknown' until LB data)."""
    if song.get("schema_version") == SCHEMA_VERSIONS["song_profile"]:
        return dict(song)
    meta = song.get("metadata") or {}
    old_id = str(song.get("song_id") or "")
    v1_ext = {k: v for k, v in (song.get("external_ids") or {}).items() if v}
    title, artist = meta.get("title"), meta.get("artist")
    if not (title and artist):
        raise ContractError(f"v1 song {old_id!r} lacks title/artist; cannot derive an ID")
    song_id, basis = make_song_id(artist=artist, title=title)
    spotify = v1_ext.get("spotify_track_id")
    if not spotify and old_id.startswith("spotify:track:"):
        spotify = old_id.split(":")[-1]
    tags = []
    for group in (song.get("source_tags") or {}).values():
        for name, weight in (group or {}).items():
            tags.append({"name": name, "weight": weight, "source": "lastfm"})
    record = {
        "schema_version": SCHEMA_VERSIONS["song_profile"],
        "song_id": song_id,
        "id_basis": basis,
        "title": title,
        "artists": [{"name": artist, "mbid": None}],
        "release": {"title": meta.get("album"), "year": meta.get("release_year"), "country": None},
        "duration_ms": meta.get("duration_ms"),
        "external_ids": {
            "musicbrainz_recording": v1_ext.get("musicbrainz_recording_id"),
            "isrc": [],
            "spotify_track": spotify,
            "youtube_video": None,
            "legacy_song_id": old_id or None,
        },
        "tags": tags,
        "genres": sorted((song.get("genres") or {}), key=lambda g: -song["genres"][g]),
        "popularity": {
            "listen_count": None, "listener_count": None, "source": None,
            "global_percentile": None, "genre_percentile": {},
            "bucket": "unknown", "bucket_version": BUCKET_VERSION, "computed_at": None,
        },
        # Spotify IDs from v1 were confirmed by the API during collection.
        "playback": (
            {"source": "spotify", "url": f"https://open.spotify.com/track/{spotify}",
             "verified": True, "verification_method": "legacy_v1_spotify", "verified_at": None}
            if spotify else
            {"source": "none", "url": None, "verified": False,
             "verification_method": None, "verified_at": None}
        ),
        "rag_doc_id": None,
        "provenance": [{"source": "legacy_v1", "license": "unknown",
                        "fetched_at": song.get("updated_at")}],
    }
    validate_record("song_profile", record)
    return record
