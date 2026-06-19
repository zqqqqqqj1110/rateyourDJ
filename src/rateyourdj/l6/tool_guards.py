from __future__ import annotations

from typing import Any

from .errors import AgentLoopError
from .guards import validate_retrieval_arguments
from .models import AgentRequest
from .provider import AgentDecision
from .runtime import validated_ranking_arguments


def validated_model_tool_arguments(
    decision: AgentDecision,
    user_id: str,
    request: AgentRequest,
    *,
    available_tools: set[str],
) -> dict[str, Any]:
    tool_name = str(decision.tool_name)
    if tool_name not in available_tools:
        raise AgentLoopError(f"model selected unknown tool: {tool_name}")
    arguments = dict(decision.arguments)
    supplied_user_id = arguments.get("user_id")
    if supplied_user_id not in (None, user_id):
        raise AgentLoopError("model cannot access another user's data")

    if tool_name in {
        "L1.inspect_user_profile",
        "get_user_memory",
        "L3.retrieve_candidates",
        "get_similar_tracks",
        "L4.rank_candidates",
        "rank_candidates",
        "L5.inspect_feedback_state",
    }:
        arguments["user_id"] = user_id

    if tool_name in {
        "L1.inspect_user_profile",
        "get_user_memory",
        "L5.inspect_feedback_state",
    }:
        if set(arguments) != {"user_id"}:
            raise AgentLoopError(f"{tool_name} accepts only user_id")
        return arguments

    if tool_name == "search_tracks":
        allowed = {"query", "limit", "market", "providers"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("search_tracks arguments contain unknown fields")
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise AgentLoopError("query must be a non-empty string")
        limit = arguments.get("limit", min(request.top_k * 2, 10))
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 10
        ):
            raise AgentLoopError("limit must be between 1 and 10")
        arguments["limit"] = limit
        return arguments

    if tool_name == "get_track_metadata":
        allowed = {"track_ids", "queries", "include_raw"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("get_track_metadata arguments contain unknown fields")
        track_ids = arguments.get("track_ids")
        queries = arguments.get("queries")
        if track_ids is None and queries is None:
            raise AgentLoopError("get_track_metadata requires track_ids or queries")
        if track_ids is not None and (
            not isinstance(track_ids, list)
            or not 1 <= len(track_ids) <= 50
            or not all(isinstance(track_id, str) and track_id for track_id in track_ids)
        ):
            raise AgentLoopError("track_ids must be a non-empty string list")
        if queries is not None:
            if not isinstance(queries, list) or not 1 <= len(queries) <= 50:
                raise AgentLoopError("queries must be a non-empty list")
            for query in queries:
                if (
                    not isinstance(query, dict)
                    or not isinstance(query.get("title"), str)
                    or not query["title"].strip()
                    or not isinstance(query.get("artist"), str)
                    or not query["artist"].strip()
                ):
                    raise AgentLoopError(
                        "metadata queries require title and artist"
                    )
        if "include_raw" in arguments and not isinstance(
            arguments["include_raw"],
            bool,
        ):
            raise AgentLoopError("include_raw must be boolean")
        return arguments

    if tool_name == "L2.inspect_song_profile":
        if set(arguments) != {"song_id"}:
            raise AgentLoopError("L2.inspect_song_profile requires only song_id")
        song_id = arguments.get("song_id")
        if not isinstance(song_id, str) or not song_id:
            raise AgentLoopError("song_id must be a non-empty string")
        return arguments

    if tool_name == "L3.retrieve_candidates":
        allowed = {"user_id", "top_k", "max_per_artist", "min_score"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("L3 arguments contain unknown fields")
        arguments.setdefault("top_k", min(request.top_k * 5, 1000))
        arguments.setdefault("max_per_artist", request.max_per_artist)
        arguments.setdefault("min_score", request.min_retrieval_score)
        validate_retrieval_arguments(arguments, request)
        return arguments

    if tool_name == "get_similar_tracks":
        allowed = {
            "user_id",
            "seed_track_ids",
            "seed_artists",
            "seed_genres",
            "limit",
            "market",
            "max_per_artist",
            "min_score",
        }
        if not set(arguments) <= allowed:
            raise AgentLoopError("get_similar_tracks arguments contain unknown fields")
        arguments.setdefault("limit", min(request.top_k * 5, 50))
        arguments.setdefault("max_per_artist", request.max_per_artist)
        arguments.setdefault("min_score", request.min_retrieval_score)
        retrieval_arguments = {
            "top_k": arguments["limit"],
            "max_per_artist": arguments["max_per_artist"],
            "min_score": arguments["min_score"],
        }
        validate_retrieval_arguments(retrieval_arguments, request)
        return arguments

    if tool_name == "L4.rank_candidates":
        allowed = {
            "user_id",
            "top_k",
            "candidate_pool_size",
            "max_per_artist",
            "min_retrieval_score",
        }
        if not set(arguments) <= allowed:
            raise AgentLoopError("L4 arguments contain unknown fields")
        pool_size = min(max(request.top_k * 5, request.top_k), 1000)
        supplied_top_k = arguments.get("top_k")
        if isinstance(supplied_top_k, int) and not isinstance(
            supplied_top_k,
            bool,
        ):
            arguments["top_k"] = max(supplied_top_k, pool_size)
        else:
            arguments.setdefault("top_k", pool_size)
        supplied_pool_size = arguments.get("candidate_pool_size")
        if isinstance(supplied_pool_size, int) and not isinstance(
            supplied_pool_size,
            bool,
        ):
            arguments["candidate_pool_size"] = max(
                supplied_pool_size,
                pool_size,
            )
        else:
            arguments.setdefault("candidate_pool_size", pool_size)
        arguments.setdefault("max_per_artist", request.max_per_artist)
        arguments.setdefault("min_retrieval_score", request.min_retrieval_score)
        resolved = validated_ranking_arguments(arguments, {}, request)
        if resolved is None:
            raise AgentLoopError("L4 arguments violate program constraints")
        return resolved

    if tool_name == "rank_candidates":
        allowed = {
            "user_id",
            "session_id",
            "message",
            "candidate_track_ids",
            "limit",
            "candidate_pool_size",
            "max_per_artist",
            "min_retrieval_score",
            "constraints",
        }
        if not set(arguments) <= allowed:
            raise AgentLoopError("rank_candidates arguments contain unknown fields")
        pool_size = min(max(request.top_k * 5, request.top_k), 1000)
        supplied_limit = arguments.get("limit")
        if isinstance(supplied_limit, int) and not isinstance(supplied_limit, bool):
            arguments["limit"] = max(supplied_limit, min(pool_size, 50))
        else:
            arguments.setdefault("limit", min(pool_size, 50))
        supplied_pool_size = arguments.get("candidate_pool_size")
        if isinstance(supplied_pool_size, int) and not isinstance(
            supplied_pool_size,
            bool,
        ):
            arguments["candidate_pool_size"] = max(supplied_pool_size, pool_size)
        else:
            arguments.setdefault("candidate_pool_size", pool_size)
        arguments.setdefault("max_per_artist", request.max_per_artist)
        arguments.setdefault("min_retrieval_score", request.min_retrieval_score)
        resolved = validated_ranking_arguments(
            {
                "top_k": arguments["limit"],
                "candidate_pool_size": arguments["candidate_pool_size"],
                "max_per_artist": arguments["max_per_artist"],
                "min_retrieval_score": arguments["min_retrieval_score"],
            },
            {},
            request,
        )
        if resolved is None:
            raise AgentLoopError("rank_candidates arguments violate program constraints")
        arguments["limit"] = resolved["top_k"]
        arguments["candidate_pool_size"] = resolved["candidate_pool_size"]
        arguments["max_per_artist"] = resolved["max_per_artist"]
        arguments["min_retrieval_score"] = resolved["min_retrieval_score"]
        return arguments

    raise AgentLoopError(f"tool is not allowed in recommendation loop: {tool_name}")
