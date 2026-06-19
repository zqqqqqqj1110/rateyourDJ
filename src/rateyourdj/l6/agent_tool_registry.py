from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from rateyourdj.agent_tools import ToolObservation
from rateyourdj.l1 import JsonProfileStore, inspect_user_profile
from rateyourdj.l2 import JsonSongStore, inspect_song_profile
from rateyourdj.l3 import retrieve_candidates_tool
from rateyourdj.l4 import rank_candidates_tool
from rateyourdj.l5 import record_feedback_tool
from rateyourdj.providers import ExternalMusicProvider, TrackQuery

from .agent_tool_schemas import AGENT_TOOL_SCHEMAS


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
    ) -> AgentToolRegistryV1:
        registry = cls()
        profile_dir = profile_store.root
        song_dir = song_store.root

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
        return registry


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
