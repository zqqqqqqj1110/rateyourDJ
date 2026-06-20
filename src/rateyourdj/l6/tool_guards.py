from __future__ import annotations

from typing import Any

from .errors import AgentLoopError
from .guards import validate_retrieval_arguments
from .loop_contract import validate_tool_allowed_in_recommendation_loop
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
    try:
        validate_tool_allowed_in_recommendation_loop(tool_name)
    except ValueError as error:
        raise AgentLoopError(str(error)) from error
    arguments = dict(decision.arguments)
    supplied_user_id = arguments.get("user_id")
    if supplied_user_id not in (None, user_id):
        raise AgentLoopError("model cannot access another user's data")

    if tool_name in {
        "L1.inspect_user_profile",
        "get_user_memory",
        "get_session_memory",
        "update_session_memory",
        "propose_memory_update",
        "commit_memory_update",
        "L3.retrieve_candidates",
        "get_similar_tracks",
        "L4.rank_candidates",
        "rank_candidates",
        "explain_recommendations",
        "record_feedback",
        "save_to_collection",
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

    if tool_name == "get_session_memory":
        if set(arguments) != {"user_id", "session_id"}:
            raise AgentLoopError("get_session_memory requires user_id and session_id")
        _require_non_empty_string(arguments, "session_id")
        return arguments

    if tool_name == "update_session_memory":
        allowed = {"user_id", "session_id", "patch"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("update_session_memory arguments contain unknown fields")
        _require_non_empty_string(arguments, "session_id")
        patch = arguments.get("patch")
        allowed_patch = {
            "current_intent",
            "last_user_query",
            "active_constraints",
            "preference_terms",
            "exclude_terms",
            "seen_track_ids",
            "seed_track_ids",
            "last_run_id",
            "last_recommendation_ids",
            "temporary_feedback",
        }
        if not isinstance(patch, dict) or not set(patch) <= allowed_patch:
            raise AgentLoopError("update_session_memory.patch is invalid")
        for field_name in (
            "preference_terms",
            "exclude_terms",
            "seen_track_ids",
            "seed_track_ids",
            "last_recommendation_ids",
        ):
            if field_name in patch and not _is_string_list_allow_empty(
                patch[field_name]
            ):
                raise AgentLoopError(f"patch.{field_name} must be a string list")
        if "current_intent" in patch and (
            not isinstance(patch["current_intent"], str)
            or not patch["current_intent"].strip()
        ):
            raise AgentLoopError("patch.current_intent must be a non-empty string")
        if "last_user_query" in patch and (
            not isinstance(patch["last_user_query"], str)
            or not patch["last_user_query"].strip()
        ):
            raise AgentLoopError("patch.last_user_query must be a non-empty string")
        if "active_constraints" in patch and not isinstance(
            patch["active_constraints"],
            dict,
        ):
            raise AgentLoopError("patch.active_constraints must be an object")
        if "temporary_feedback" in patch and (
            not isinstance(patch["temporary_feedback"], list)
            or not all(
                isinstance(item, dict) for item in patch["temporary_feedback"]
            )
        ):
            raise AgentLoopError("patch.temporary_feedback must be an object list")
        return arguments

    if tool_name == "propose_memory_update":
        allowed = {"user_id", "source", "proposal"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("propose_memory_update arguments contain unknown fields")
        source = arguments.get("source")
        if source not in {"user_statement", "feedback_pattern", "collection_import"}:
            raise AgentLoopError("source is not a supported memory update source")
        proposal = arguments.get("proposal")
        if not isinstance(proposal, dict):
            raise AgentLoopError("proposal must be an object")
        required = {"field", "value", "confidence", "reason"}
        allowed_proposal = {*required, "delta"}
        if not required <= set(proposal) or not set(proposal) <= allowed_proposal:
            raise AgentLoopError("proposal fields are invalid")
        for field_name in ("field", "value", "reason"):
            if not isinstance(proposal.get(field_name), str) or not proposal[
                field_name
            ].strip():
                raise AgentLoopError(f"proposal.{field_name} must be non-empty")
        confidence = proposal.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise AgentLoopError("proposal.confidence must be numeric")
        if "delta" in proposal and (
            isinstance(proposal["delta"], bool)
            or not isinstance(proposal["delta"], (int, float))
        ):
            raise AgentLoopError("proposal.delta must be numeric")
        return arguments

    if tool_name == "commit_memory_update":
        if set(arguments) != {"user_id", "proposal_id", "run_id"}:
            raise AgentLoopError(
                "commit_memory_update requires user_id, proposal_id, and run_id"
            )
        _require_non_empty_string(arguments, "proposal_id")
        _require_non_empty_string(arguments, "run_id")
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
            or not 1 <= limit <= 50
        ):
            raise AgentLoopError("limit must be between 1 and 50")
        arguments["limit"] = limit
        return arguments

    if tool_name == "get_similar_artists":
        allowed = {"artist_names", "limit", "providers"}
        if not set(arguments) <= allowed:
            raise AgentLoopError(
                "get_similar_artists arguments contain unknown fields"
            )
        artist_names = arguments.get("artist_names")
        if (
            not isinstance(artist_names, list)
            or not 1 <= len(artist_names) <= 10
            or not all(
                isinstance(name, str) and name.strip()
                for name in artist_names
            )
        ):
            raise AgentLoopError(
                "artist_names must be a non-empty string list with max 10 items"
            )
        limit = arguments.get("limit", min(request.top_k * 3, 10))
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 25
        ):
            raise AgentLoopError("limit must be between 1 and 25")
        arguments["limit"] = limit
        providers = arguments.get("providers")
        if providers is not None and (
            not isinstance(providers, list)
            or not all(provider == "lastfm" for provider in providers)
        ):
            raise AgentLoopError(
                "providers must be a list containing only supported providers"
            )
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

    if tool_name == "get_artist_profile":
        allowed = {"artist_ids", "artist_names"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("get_artist_profile arguments contain unknown fields")
        artist_ids = arguments.get("artist_ids")
        artist_names = arguments.get("artist_names")
        if artist_ids is None and artist_names is None:
            raise AgentLoopError("get_artist_profile requires artist_ids or artist_names")
        if artist_ids is not None and (
            not _is_string_list(artist_ids) or len(artist_ids) > 25
        ):
            raise AgentLoopError("artist_ids must be a string list with max 25 items")
        if artist_names is not None and (
            not _is_string_list(artist_names) or len(artist_names) > 25
        ):
            raise AgentLoopError("artist_names must be a string list with max 25 items")
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

    if tool_name == "explain_recommendations":
        allowed = {"user_id", "session_id", "message", "ranked_tracks", "style"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("explain_recommendations arguments contain unknown fields")
        _require_non_empty_string(arguments, "message")
        ranked_tracks = arguments.get("ranked_tracks")
        if (
            not isinstance(ranked_tracks, list)
            or not 1 <= len(ranked_tracks) <= 50
            or not all(isinstance(track, dict) for track in ranked_tracks)
        ):
            raise AgentLoopError("ranked_tracks must be a non-empty object list")
        if "style" in arguments and arguments["style"] not in {
            "short",
            "balanced",
            "historical",
        }:
            raise AgentLoopError("style is invalid")
        return arguments

    if tool_name == "record_feedback":
        allowed = {"user_id", "session_id", "run_id", "track_id", "event", "context"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("record_feedback arguments contain unknown fields")
        _require_non_empty_string(arguments, "track_id")
        if arguments.get("event") not in {
            "liked",
            "skipped",
            "saved",
            "playlist_add",
            "request_similar",
            "hide_artist",
            "hide_track",
        }:
            raise AgentLoopError("event is invalid")
        if "context" in arguments and not isinstance(arguments["context"], dict):
            raise AgentLoopError("context must be an object")
        return arguments

    if tool_name == "save_to_collection":
        allowed = {"user_id", "track_id", "source", "run_id"}
        if not set(arguments) <= allowed:
            raise AgentLoopError("save_to_collection arguments contain unknown fields")
        _require_non_empty_string(arguments, "track_id")
        if arguments.get("source") not in {
            "agent_recommendation",
            "manual",
            "import",
        }:
            raise AgentLoopError("source is invalid")
        return arguments

    raise AgentLoopError(f"tool is not allowed in recommendation loop: {tool_name}")


def _require_non_empty_string(arguments: dict[str, Any], field_name: str) -> None:
    if (
        not isinstance(arguments.get(field_name), str)
        or not arguments[field_name].strip()
    ):
        raise AgentLoopError(f"{field_name} must be a non-empty string")


def _is_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 1
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _is_string_list_allow_empty(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value
    )
