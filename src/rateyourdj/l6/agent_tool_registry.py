from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from rateyourdj.agent_tools import ToolObservation
from rateyourdj.domain import DiscoveryService, ExplanationGenerator
from rateyourdj.l1 import JsonProfileStore, inspect_user_profile
from rateyourdj.l2 import JsonSongStore, inspect_song_profile
from rateyourdj.l3 import retrieve_candidates_tool
from rateyourdj.l4 import rank_candidates_tool
from rateyourdj.l5 import record_feedback_tool
from rateyourdj.providers import ExternalMusicProvider, TrackQuery

from .agent_tool_schemas import AGENT_TOOL_SCHEMAS
from .sessions import JsonSessionStore


AgentTool = Callable[..., ToolObservation]

LEGACY_TOOL_NAME_MAP = {
    "L1.inspect_user_profile": "get_user_memory",
    "L2.inspect_song_profile": "get_track_metadata",
    "L2.collect_or_import": "get_track_metadata",
    "L2.merge_sources": "get_track_metadata",
    "L3.retrieve_candidates": "get_similar_tracks",
    "L4.rank_candidates": "rank_candidates",
    "L5.record_feedback": "record_feedback",
    "L5.configure_trajectory_sink": "record_feedback",
}

FEEDBACK_EVENT_MAP = {
    "liked": "like",
    "skipped": "skip",
    "saved": "favorite",
    "playlist_add": "playlist_add",
    "request_similar": "like",
    "hide_artist": "dislike",
    "hide_track": "dislike",
}


class AgentToolRegistryV1:
    """Agent-facing tool registry using product-level tool names.

    This registry is an adapter layer for the refactor. It exposes the new tool
    contract while internally reusing existing L1-L5 implementations.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}
        self._memory_proposals: dict[str, dict[str, Any]] = {}

    def register(self, name: str, tool: AgentTool) -> None:
        self._tools[name] = tool

    def call(self, name: str, **arguments: Any) -> ToolObservation:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ValueError(f"unknown agent tool: {name}") from exc
        return tool(**arguments)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def model_schemas(self) -> list[dict[str, Any]]:
        available = set(self.names())
        return [
            dict(schema)
            for schema in AGENT_TOOL_SCHEMAS
            if schema["name"] in available
        ]

    @classmethod
    def default(
        cls,
        profile_store: JsonProfileStore,
        song_store: JsonSongStore,
        music_provider: ExternalMusicProvider | None = None,
        session_store: JsonSessionStore | None = None,
        track_generator: Any | None = None,
    ) -> AgentToolRegistryV1:
        registry = cls()
        profile_dir = profile_store.root
        song_dir = song_store.root
        resolved_session_store = session_store or JsonSessionStore(
            profile_dir.parent / "sessions"
        )

        if music_provider is not None and track_generator is not None:
            discovery_service = DiscoveryService(track_generator, music_provider)
            registry.register(
                "discover_tracks",
                lambda **arguments: _discover_tracks(
                    discovery_service=discovery_service,
                    profile_dir=profile_dir,
                    song_store=song_store,
                    **arguments,
                ),
            )

        if music_provider is not None:
            registry.register(
                "search_tracks",
                lambda **arguments: _search_tracks(
                    music_provider=music_provider,
                    **arguments,
                ),
            )
        registry.register(
            "get_user_memory",
            lambda **arguments: _as_v1_observation(
                inspect_user_profile(
                    data_dir=profile_dir,
                    **arguments,
                ),
                "get_user_memory",
            ),
        )
        registry.register(
            "get_session_memory",
            lambda **arguments: _get_session_memory(
                session_store=resolved_session_store,
                **arguments,
            ),
        )
        registry.register(
            "update_session_memory",
            lambda **arguments: _update_session_memory(
                session_store=resolved_session_store,
                **arguments,
            ),
        )
        registry.register(
            "propose_memory_update",
            lambda **arguments: registry._propose_memory_update(**arguments),
        )
        registry.register(
            "commit_memory_update",
            lambda **arguments: registry._commit_memory_update(**arguments),
        )
        registry.register(
            "L1.inspect_user_profile",
            lambda **arguments: _as_v1_observation(
                inspect_user_profile(
                    data_dir=profile_dir,
                    **arguments,
                ),
                "L1.inspect_user_profile",
            ),
        )
        registry.register(
            "get_track_metadata",
            lambda **arguments: _get_track_metadata(
                song_dir=song_dir,
                music_provider=music_provider,
                **arguments,
            ),
        )
        registry.register(
            "get_artist_profile",
            lambda **arguments: _get_artist_profile(
                song_store=song_store,
                **arguments,
            ),
        )
        if music_provider is not None:
            registry.register(
                "get_similar_artists",
                lambda **arguments: _get_similar_artists_from_provider(
                    music_provider=music_provider,
                    **arguments,
                ),
            )
        registry.register(
            "L2.inspect_song_profile",
            lambda **arguments: _as_v1_observation(
                inspect_song_profile(
                    data_dir=song_dir,
                    **arguments,
                ),
                "L2.inspect_song_profile",
            ),
        )
        registry.register(
            "get_similar_tracks",
            lambda **arguments: _get_similar_tracks(
                profile_dir=profile_dir,
                song_dir=song_dir,
                tool_name="get_similar_tracks",
                **arguments,
            ),
        )
        registry.register(
            "L3.retrieve_candidates",
            lambda **arguments: _get_similar_tracks(
                profile_dir=profile_dir,
                song_dir=song_dir,
                tool_name="L3.retrieve_candidates",
                **_legacy_retrieval_arguments(arguments),
            ),
        )
        registry.register(
            "rank_candidates",
            lambda **arguments: _rank_candidates(
                profile_dir=profile_dir,
                song_dir=song_dir,
                tool_name="rank_candidates",
                **arguments,
            ),
        )
        registry.register(
            "L4.rank_candidates",
            lambda **arguments: _rank_candidates(
                profile_dir=profile_dir,
                song_dir=song_dir,
                tool_name="L4.rank_candidates",
                **_legacy_ranking_arguments(arguments),
            ),
        )
        registry.register(
            "record_feedback",
            lambda **arguments: _record_feedback(
                profile_dir=profile_dir,
                song_dir=song_dir,
                **arguments,
            ),
        )
        registry.register(
            "explain_recommendations",
            lambda **arguments: _explain_recommendations(
                profile_dir=profile_dir,
                **arguments,
            ),
        )
        registry.register(
            "save_to_collection",
            lambda **arguments: _save_to_collection(
                profile_store=profile_store,
                **arguments,
            ),
        )
        return registry

    def _propose_memory_update(
        self,
        *,
        user_id: str,
        source: str,
        proposal: dict[str, Any],
    ) -> ToolObservation:
        proposal_id = "memory_proposal_" + uuid4().hex
        confidence = _safe_float(proposal.get("confidence"), default=0.0)
        accepted = (
            source in {"user_statement", "feedback_pattern", "collection_import"}
            and bool(str(proposal.get("field", "")).strip())
            and bool(str(proposal.get("value", "")).strip())
            and confidence >= 0.5
        )
        stored = {
            "proposal_id": proposal_id,
            "user_id": user_id,
            "source": source,
            "proposal": dict(proposal),
            "accepted_by_policy": accepted,
            "created_at": _now(),
        }
        self._memory_proposals[proposal_id] = stored
        return ToolObservation(
            tool="propose_memory_update",
            status="ok" if accepted else "partial",
            data={
                "proposal_id": proposal_id,
                "accepted_by_policy": accepted,
                "requires_user_confirmation": source == "user_statement",
                "reason": (
                    "Proposal passed minimum durable-memory policy."
                    if accepted
                    else "Proposal was stored but did not meet commit policy."
                ),
            },
            diagnostics=[] if accepted else ["memory proposal confidence is low"],
            retryable=not accepted,
            suggested_actions=[],
        )

    def _commit_memory_update(
        self,
        *,
        user_id: str,
        proposal_id: str,
        run_id: str,
    ) -> ToolObservation:
        proposal = self._memory_proposals.get(proposal_id)
        if proposal is None or proposal.get("user_id") != user_id:
            return ToolObservation(
                tool="commit_memory_update",
                status="empty",
                data={
                    "user_id": user_id,
                    "committed": False,
                    "memory_updates": [],
                },
                diagnostics=["memory proposal was not found for this user"],
                retryable=False,
                suggested_actions=[],
            )
        if not proposal.get("accepted_by_policy"):
            return ToolObservation(
                tool="commit_memory_update",
                status="partial",
                data={
                    "user_id": user_id,
                    "committed": False,
                    "memory_updates": [],
                },
                diagnostics=["memory proposal did not pass policy"],
                retryable=False,
                suggested_actions=[],
            )
        payload = dict(proposal["proposal"])
        return ToolObservation(
            tool="commit_memory_update",
            status="ok",
            data={
                "user_id": user_id,
                "committed": True,
                "run_id": run_id,
                "memory_updates": [
                    {
                        "scope": "long_term",
                        "field": payload.get("field"),
                        "value": payload.get("value"),
                        "delta": payload.get("delta", 0.0),
                        "summary": payload.get("reason", ""),
                    }
                ],
            },
            diagnostics=[],
            retryable=False,
            suggested_actions=[],
        )


def _get_session_memory(
    *,
    session_store: JsonSessionStore,
    user_id: str,
    session_id: str,
) -> ToolObservation:
    session = session_store.load_or_create(user_id, session_id)
    return ToolObservation(
        tool="get_session_memory",
        status="ok",
        data=_session_memory_data(session),
        diagnostics=[],
        retryable=False,
        suggested_actions=[],
    )


def _update_session_memory(
    *,
    session_store: JsonSessionStore,
    user_id: str,
    session_id: str,
    patch: dict[str, Any],
) -> ToolObservation:
    session = session_store.load_or_create(user_id, session_id)
    updated_fields: list[str] = []
    if "current_intent" in patch:
        session.current_intent = str(patch["current_intent"]).strip() or "recommend"
        updated_fields.append("current_intent")
    if "last_user_query" in patch:
        last_user_query = str(patch["last_user_query"]).strip()
        session.last_user_query = last_user_query or None
        updated_fields.append("last_user_query")
    if "preference_terms" in patch:
        session.preference_terms = _string_list(patch["preference_terms"])
        updated_fields.append("preference_terms")
    if "exclude_terms" in patch:
        session.exclude_terms = _string_list(patch["exclude_terms"])
        updated_fields.append("exclude_terms")
    if "seen_track_ids" in patch:
        session.seen_song_ids = _string_list(patch["seen_track_ids"])
        updated_fields.append("seen_track_ids")
    if "seed_track_ids" in patch:
        session.seed_track_ids = _string_list(patch["seed_track_ids"])
        updated_fields.append("seed_track_ids")
    if "last_run_id" in patch:
        session.last_trajectory_id = str(patch["last_run_id"])
        updated_fields.append("last_run_id")
    if "last_recommendation_ids" in patch:
        session.last_recommendation_ids = _string_list(
            patch["last_recommendation_ids"]
        )
        updated_fields.append("last_recommendation_ids")
    if "temporary_feedback" in patch and isinstance(patch["temporary_feedback"], list):
        session.temporary_feedback = [
            dict(item)
            for item in patch["temporary_feedback"]
            if isinstance(item, dict)
        ]
        updated_fields.append("temporary_feedback")
    active_constraints = patch.get("active_constraints")
    if isinstance(active_constraints, dict):
        session.active_constraints.update(dict(active_constraints))
        updated_fields.append("active_constraints")
    session_store.save(session)
    return ToolObservation(
        tool="update_session_memory",
        status="ok",
        data={
            "session_id": session.session_id,
            "updated_fields": updated_fields,
            "memory_updates": [
                {
                    "scope": "session",
                    "type": field,
                    "summary": f"Updated session field {field}.",
                }
                for field in updated_fields
            ],
        },
        diagnostics=[],
        retryable=False,
        suggested_actions=[],
    )


def _session_memory_data(session: Any) -> dict[str, Any]:
    data = asdict(session)
    return {
        "schema_version": data["schema_version"],
        "session_id": data["session_id"],
        "user_id": data["user_id"],
        "turn_count": data["turn_count"],
        "current_intent": data.get("current_intent", "recommend"),
        "last_user_query": data.get("last_user_query"),
        "active_constraints": dict(data.get("active_constraints", {})),
        "preference_terms": list(data.get("preference_terms", [])),
        "exclude_terms": list(data.get("exclude_terms", [])),
        "seen_track_ids": list(data.get("seen_track_ids", [])),
        "seen_track_signatures": list(
            data.get("seen_track_signatures", [])
        ),
        "seed_track_ids": list(data.get("seed_track_ids", [])),
        "last_run_id": data.get("last_run_id"),
        "last_recommendation_ids": list(
            data.get("last_recommendation_ids", [])
        ),
        "temporary_feedback": list(data.get("temporary_feedback", [])),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _get_track_metadata(
    *,
    song_dir: str | Path,
    music_provider: ExternalMusicProvider | None = None,
    track_ids: list[str] | None = None,
    queries: list[dict[str, Any]] | None = None,
    include_raw: bool = False,
) -> ToolObservation:
    provider_tracks: list[dict[str, Any]] = []
    provider_diagnostics: list[str] = []
    if queries:
        if music_provider is None:
            return ToolObservation(
                tool="get_track_metadata",
                status="empty",
                data={"tracks": [], "missing_track_ids": []},
                diagnostics=["no external metadata provider is configured"],
                retryable=True,
                suggested_actions=[],
            )
        for query in queries:
            try:
                track = music_provider.get_track_metadata(
                    TrackQuery(
                        title=str(query.get("title", "")),
                        artist=str(query.get("artist", "")),
                        album=(
                            str(query["album"])
                            if query.get("album") is not None
                            else None
                        ),
                    )
                )
                track_data = track.to_dict()
                if not include_raw:
                    track_data.pop("raw", None)
                provider_tracks.append(track_data)
            except Exception as error:
                provider_diagnostics.append(str(error))
        if provider_tracks or not track_ids:
            return ToolObservation(
                tool="get_track_metadata",
                status=(
                    "partial"
                    if provider_diagnostics and provider_tracks
                    else "empty"
                    if provider_diagnostics
                    else "ok"
                ),
                data={
                    "tracks": provider_tracks,
                    "missing_track_ids": [],
                },
                diagnostics=provider_diagnostics,
                retryable=bool(provider_diagnostics),
                suggested_actions=[],
            )

    track_ids = track_ids or []
    observations = [
        inspect_song_profile(track_id, data_dir=song_dir)
        for track_id in track_ids
    ]
    missing_track_ids: list[str] = []
    tracks: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    status = "ok"
    for track_id, observation in zip(track_ids, observations):
        if observation.status == "empty":
            missing_track_ids.append(track_id)
            status = "partial"
            continue
        track = dict(observation.data)
        if not include_raw:
            track.pop("raw", None)
        tracks.append(track)
        diagnostics.extend(observation.diagnostics)
        if observation.status == "partial" and status == "ok":
            status = "partial"
    return ToolObservation(
        tool="get_track_metadata",
        status=status,
        data={
            "tracks": tracks,
            "missing_track_ids": missing_track_ids,
        },
        diagnostics=diagnostics,
        retryable=bool(diagnostics or missing_track_ids),
        suggested_actions=_map_suggested_actions(
            action
            for observation in observations
            for action in observation.suggested_actions
        ),
    )


def _get_artist_profile(
    *,
    song_store: JsonSongStore,
    artist_ids: list[str] | None = None,
    artist_names: list[str] | None = None,
) -> ToolObservation:
    requested_ids = _string_list(artist_ids or [])
    requested_names = _string_list(artist_names or [])
    if not requested_ids and not requested_names:
        return ToolObservation(
            tool="get_artist_profile",
            status="empty",
            data={"artists": [], "missing": []},
            diagnostics=["artist_ids or artist_names is required"],
            retryable=False,
            suggested_actions=[],
        )

    requested_name_keys = {_normalize_label(name) for name in requested_names}
    artists: dict[str, dict[str, Any]] = {}
    for path in song_store.root.glob("*.json"):
        try:
            song = song_store.load(path.stem)
        except Exception:
            continue
        artist = str(song.metadata.get("artist") or "").strip()
        if not artist:
            continue
        key = _normalize_label(artist)
        if requested_name_keys and key not in requested_name_keys:
            continue
        profile = artists.setdefault(
            key,
            {
                "artist_id": None,
                "name": artist,
                "genres": {},
                "tags": {},
                "historical_context": [],
                "provider": "local_cache",
                "track_count": 0,
            },
        )
        profile["track_count"] += 1
        for genre, score in song.genres.items():
            profile["genres"][genre] = max(
                float(score),
                float(profile["genres"].get(genre, 0.0)),
            )
        for tag_map in song.source_tags.values():
            for tag, score in tag_map.items():
                profile["tags"][tag] = max(
                    float(score),
                    float(profile["tags"].get(tag, 0.0)),
                )

    missing = [
        name
        for name in requested_names
        if _normalize_label(name) not in artists
    ]
    missing.extend(requested_ids)
    data_artists = []
    for profile in artists.values():
        data_artists.append(
            {
                **profile,
                "genres": sorted(profile["genres"]),
                "tags": dict(sorted(profile["tags"].items())),
            }
        )
    return ToolObservation(
        tool="get_artist_profile",
        status="ok" if data_artists else "empty",
        data={"artists": data_artists, "missing": missing},
        diagnostics=[],
        retryable=not data_artists,
        suggested_actions=[],
    )


def _search_tracks(
    *,
    music_provider: ExternalMusicProvider,
    query: str,
    limit: int = 10,
    market: str | None = None,
    **_ignored: Any,
) -> ToolObservation:
    try:
        results = music_provider.search_tracks(
            query,
            limit=limit,
            market=market,
        )
    except Exception as error:
        return ToolObservation(
            tool="search_tracks",
            status="empty",
            data={"query": query, "provider_results": [], "tracks": []},
            diagnostics=[str(error)],
            retryable=True,
            suggested_actions=[],
        )
    provider_results = []
    tracks = []
    diagnostics: list[str] = []
    for result in results:
        provider_results.append(
            {
                "provider": result.provider,
                "result_count": len(result.tracks),
                "cache_hit": result.cache_hit,
            }
        )
        tracks.extend(track.to_dict() for track in result.tracks)
        diagnostics.extend(result.diagnostics)
    return ToolObservation(
        tool="search_tracks",
        status="empty" if not tracks else "partial" if diagnostics else "ok",
        data={
            "query": query,
            "provider_results": provider_results,
            "tracks": tracks,
        },
        diagnostics=diagnostics,
        retryable=bool(diagnostics or not tracks),
        suggested_actions=[],
    )


def _discover_tracks(
    *,
    discovery_service: DiscoveryService,
    profile_dir: str | Path,
    song_store: JsonSongStore,
    user_id: str,
    intent: str | None = None,
    limit: int = 10,
    exclude_artists: list[str] | None = None,
    **_ignored: Any,
) -> ToolObservation:
    """Generate candidate songs from user taste, then ground them.

    The LLM (or fallback generator) proposes songs; the music provider confirms
    each one exists. Confirmed tracks are shaped to match the ranked-song
    structure used by the rest of the pipeline.
    """
    user_taste = _user_taste_from_profile(
        user_id=user_id,
        profile_dir=profile_dir,
        song_store=song_store,
    )
    resolved_intent = str(intent or "").strip() or "recommend music I would enjoy"
    try:
        result = discovery_service.discover(
            intent=resolved_intent,
            user_taste=user_taste,
            count=max(1, int(limit)),
            exclude_artists=_string_list(exclude_artists or []),
        )
    except Exception as error:  # noqa: BLE001
        return ToolObservation(
            tool="discover_tracks",
            status="empty",
            data={"intent": resolved_intent, "tracks": []},
            diagnostics=[str(error)],
            retryable=True,
            suggested_actions=[],
        )

    tracks = [
        _discovered_track_to_ranked_song(item, rank)
        for rank, item in enumerate(result.tracks, start=1)
    ]
    # Provider-track-shaped dicts let the model loop reuse external ranking.
    provider_tracks = []
    for item in result.tracks:
        track_data = item.track.to_dict()
        track_data.pop("raw", None)
        track_data["discovery_reason"] = item.discovery_reason
        provider_tracks.append(track_data)
    diagnostics = list(result.diagnostics)
    diagnostics.append(
        f"generated {result.generated}, grounded {result.grounded}, "
        f"dropped {result.dropped} (hallucination_rate "
        f"{result.hallucination_rate})"
    )
    status = "ok" if tracks else "empty"
    return ToolObservation(
        tool="discover_tracks",
        status=status,
        data={
            "intent": resolved_intent,
            "tracks": tracks,
            "provider_tracks": provider_tracks,
            "generated": result.generated,
            "grounded": result.grounded,
            "dropped": result.dropped,
            "hallucination_rate": result.hallucination_rate,
            "dropped_candidates": list(result.dropped_candidates),
        },
        diagnostics=diagnostics,
        retryable=not tracks,
        suggested_actions=[],
    )


def _user_taste_from_profile(
    *,
    user_id: str,
    profile_dir: str | Path,
    song_store: JsonSongStore,
) -> dict[str, Any]:
    observation = inspect_user_profile(user_id, data_dir=profile_dir)
    data = dict(observation.data)
    taste: dict[str, Any] = {
        "artist_preferences": dict(data.get("artist_preferences", {})),
        "genre_preferences": dict(data.get("genre_preferences", {})),
        "tag_preferences": dict(data.get("tag_preferences", {})),
    }
    seed_tracks: list[dict[str, Any]] = []
    for song_id in data.get("collection_song_ids", [])[:25]:
        try:
            song = song_store.load(str(song_id))
        except Exception:
            continue
        title = str(song.metadata.get("title") or "").strip()
        artist = str(song.metadata.get("artist") or "").strip()
        if title and artist:
            seed_tracks.append({"title": title, "artist": artist})
    taste["seed_tracks"] = seed_tracks
    return taste


def _discovered_track_to_ranked_song(
    item: Any,
    rank: int,
) -> dict[str, Any]:
    track = item.track
    track_id = (track.track_id or "").strip()
    spotify_track_id = None
    if track_id.startswith("spotify:track:"):
        spotify_track_id = track_id.rsplit(":", 1)[-1] or None
    return {
        "rank": rank,
        "song_id": track_id or f"{item.artist}::{item.title}",
        "title": item.title,
        "artist": item.artist,
        "album": track.album,
        "release_year": track.release_year,
        "duration_ms": track.duration_ms,
        "genres": list(track.genres or {}),
        "tags": list(track.tags or {}),
        "final_score": 0.0,
        "base_score": 0.0,
        "score_breakdown": {},
        "diversity_penalty": 0.0,
        "ranking_reasons": (
            [item.discovery_reason] if item.discovery_reason else []
        ),
        "best_seed_song_id": None,
        "retrieval_sources": [f"{track.provider or 'provider'}_discovery"],
        "provider": track.provider,
        "spotify_track_id": spotify_track_id,
        "preview_url": track.preview_url,
        "image_url": track.image_url,
        "preview_available": bool(spotify_track_id),
        "discovery_reason": item.discovery_reason,
        "evidence": {"discovery_reason": item.discovery_reason},
    }


def _get_similar_artists_from_provider(
    *,
    music_provider: ExternalMusicProvider,
    artist_names: list[str],
    limit: int = 10,
    **_ignored: Any,
) -> ToolObservation:
    provider_results: list[dict[str, Any]] = []
    artists: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    for artist_name in _string_list(artist_names):
        try:
            result = music_provider.get_similar_artists(
                artist_name,
                limit=limit,
            )
        except Exception as error:
            diagnostics.append(f"{artist_name}: {error}")
            continue
        provider_results.append(
            {
                "provider": result.provider,
                "artist": result.artist,
                "result_count": len(result.artists),
                "cache_hit": result.cache_hit,
            }
        )
        diagnostics.extend(result.diagnostics)
        for artist in result.artists:
            artist_data = artist.to_dict()
            artist_data["source_artist"] = result.artist
            artists.append(artist_data)
    return ToolObservation(
        tool="get_similar_artists",
        status="empty" if not artists else "partial" if diagnostics else "ok",
        data={
            "provider_results": provider_results,
            "artists": artists,
        },
        diagnostics=diagnostics,
        retryable=bool(diagnostics or not artists),
        suggested_actions=[],
    )


def _legacy_retrieval_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(arguments)
    if "top_k" in resolved and "limit" not in resolved:
        resolved["limit"] = resolved.pop("top_k")
    return resolved


def _get_similar_tracks(
    *,
    user_id: str,
    profile_dir: str | Path,
    song_dir: str | Path,
    tool_name: str,
    limit: int = 20,
    max_per_artist: int = 2,
    min_score: float = 0.0,
    **_ignored: Any,
) -> ToolObservation:
    return _as_v1_observation(
        retrieve_candidates_tool(
            user_id,
            profile_dir=profile_dir,
            song_dir=song_dir,
            top_k=limit,
            max_per_artist=max_per_artist,
            min_score=min_score,
        ),
        tool_name,
    )


def _legacy_ranking_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(arguments)
    if "top_k" in resolved and "limit" not in resolved:
        resolved["limit"] = resolved.pop("top_k")
    return resolved


def _rank_candidates(
    *,
    user_id: str,
    profile_dir: str | Path,
    song_dir: str | Path,
    tool_name: str,
    limit: int = 20,
    candidate_pool_size: int | None = None,
    max_per_artist: int = 2,
    min_retrieval_score: float = 0.0,
    **_ignored: Any,
) -> ToolObservation:
    return _as_v1_observation(
        rank_candidates_tool(
            user_id,
            profile_dir=profile_dir,
            song_dir=song_dir,
            top_k=limit,
            candidate_pool_size=candidate_pool_size,
            max_per_artist=max_per_artist,
            min_retrieval_score=min_retrieval_score,
        ),
        tool_name,
    )


def _record_feedback(
    *,
    user_id: str,
    track_id: str,
    event: str,
    profile_dir: str | Path,
    song_dir: str | Path,
    run_id: str | None = None,
    context: dict[str, Any] | None = None,
    **_ignored: Any,
) -> ToolObservation:
    recommendation_context = dict(context or {})
    if run_id is not None:
        recommendation_context.setdefault("trajectory_id", run_id)
    return _as_v1_observation(
        record_feedback_tool(
            user_id,
            track_id,
            FEEDBACK_EVENT_MAP.get(event, event),
            profile_dir=profile_dir,
            song_dir=song_dir,
            recommendation_context=recommendation_context,
        ),
        "record_feedback",
    )


def _explain_recommendations(
    *,
    user_id: str,
    message: str,
    ranked_tracks: list[dict[str, Any]],
    session_id: str | None = None,
    style: str = "balanced",
    profile_dir: str | Path | None = None,
) -> ToolObservation:
    user_memory: dict[str, Any] = {}
    if profile_dir is not None:
        try:
            user_memory = dict(
                inspect_user_profile(user_id, data_dir=profile_dir).data
            )
        except Exception:
            user_memory = {}

    generator = ExplanationGenerator()
    explanations = generator.explain_all(
        ranked_tracks,
        user_memory=user_memory,
        style=style,
    )
    recommendations = []
    for track, explanation in zip(ranked_tracks, explanations):
        recommendations.append(
            {
                "track_id": str(
                    track.get("track_id") or track.get("song_id") or ""
                ).strip(),
                "song_id": track.get("song_id"),
                "reasons": [reason.to_dict() for reason in explanation.reasons],
                "evidence": [item.to_dict() for item in explanation.evidence],
            }
        )
    return ToolObservation(
        tool="explain_recommendations",
        status="ok" if recommendations else "empty",
        data={
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
            "recommendations": recommendations,
        },
        diagnostics=[],
        retryable=False,
        suggested_actions=[],
    )


def _save_to_collection(
    *,
    profile_store: JsonProfileStore,
    user_id: str,
    track_id: str,
    source: str,
    run_id: str | None = None,
) -> ToolObservation:
    def updater(profile: Any) -> Any:
        if track_id not in profile.collection_song_ids:
            profile.collection_song_ids = [
                *profile.collection_song_ids,
                track_id,
            ]
        return profile

    profile = profile_store.update(user_id, updater)
    return ToolObservation(
        tool="save_to_collection",
        status="ok",
        data={
            "user_id": user_id,
            "track_id": track_id,
            "saved": track_id in profile.collection_song_ids,
            "source": source,
            "run_id": run_id,
            "collection_count": len(profile.collection_song_ids),
            "memory_updates": [
                {
                    "scope": "collection",
                    "type": "saved_track",
                    "summary": f"Saved {track_id} to the user's collection.",
                }
            ],
        },
        diagnostics=[],
        retryable=False,
        suggested_actions=[],
    )


def _as_v1_observation(
    observation: ToolObservation,
    tool_name: str,
) -> ToolObservation:
    return ToolObservation(
        tool=tool_name,
        status=observation.status,
        data=dict(observation.data),
        diagnostics=list(observation.diagnostics),
        retryable=observation.retryable,
        suggested_actions=_map_suggested_actions(observation.suggested_actions),
    )


def _map_suggested_actions(
    actions: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for action in actions:
        mapped = dict(action)
        tool = mapped.get("tool")
        if isinstance(tool, str):
            mapped["tool"] = LEGACY_TOOL_NAME_MAP.get(tool, tool)
        result.append(mapped)
    return result


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_float(value: Any, *, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return result


def _normalize_label(value: str) -> str:
    return " ".join(value.casefold().split())
