from __future__ import annotations

import re
from typing import Any, Protocol

from .errors import AgentLoopError
from .guards import apply_request_patch
from .loop_contract import LOOP_CONTRACT_VERSION, loop_phase_for_tool
from .models import AgentRequest
from .provider import (
    AgentDecision,
    AgentTurn,
    LLMProvider,
    LLMProviderError,
    LLMResponseError,
)
from .ranking_runner import QueryFilter, ToolRegistry, guarded_model_ranking


class ValidateToolArguments(Protocol):
    def __call__(
        self,
        decision: AgentDecision,
        user_id: str,
        request: AgentRequest,
    ) -> dict[str, Any]:
        ...


def execute_model_loop(
    *,
    user_id: str,
    request: AgentRequest,
    session: Any,
    steps: list[dict[str, Any]],
    max_steps: int,
    llm_provider: LLMProvider,
    tool_registry: ToolRegistry,
    validate_tool_arguments: ValidateToolArguments,
    apply_query_filters: QueryFilter,
) -> tuple[
    AgentRequest,
    list[dict[str, Any]],
    list[str],
    list[str],
    int,
    str,
    str | None,
    list[dict[str, Any]],
]:
    best: list[dict[str, Any]] = []
    seed_song_ids: list[str] = []
    missing_seed_song_ids: list[str] = []
    rank_attempts = 0
    stop_reason = "insufficient_candidates"
    response_text: str | None = None
    validation_feedback: list[str] = []
    decisions: list[dict[str, Any]] = []
    profile_empty = False
    executed_calls: set[tuple[str, str]] = set()
    last_ranked_request: AgentRequest | None = None
    provider_search_available = "search_tracks" in set(tool_registry.names())

    model_step_limit = min(max_steps, 5)
    if provider_search_available:
        best = _run_provider_search(
            user_id=user_id,
            request=request,
            session=session,
            steps=steps,
            tool_registry=tool_registry,
            apply_query_filters=apply_query_filters,
            decision_source="program_provider_first",
        )
        decisions.append(
            {
                "kind": "program_provider_first",
                "summary": (
                    "program searched external music provider before model "
                    "tool selection"
                ),
                "decision_index": 0,
            }
        )
        if len(best) >= request.top_k:
            return (
                request,
                best[: request.top_k],
                seed_song_ids,
                missing_seed_song_ids,
                rank_attempts,
                "goal_satisfied",
                None,
                decisions,
            )

    for decision_index in range(model_step_limit):
        turn = AgentTurn(
            user_id=user_id,
            query=request.query,
            request=request.to_dict(),
            session={
                "schema_version": session.schema_version,
                "session_id": session.session_id,
                "turn_count": session.turn_count,
                "current_intent": session.current_intent,
                "last_user_query": session.last_user_query,
                "preference_terms": list(session.preference_terms),
                "exclude_terms": list(session.exclude_terms),
                "seen_track_ids": list(session.seen_track_ids),
                "seed_track_ids": list(session.seed_track_ids),
                "active_constraints": dict(session.active_constraints),
                "last_run_id": session.last_run_id,
                "last_recommendation_ids": list(session.last_recommendation_ids),
                "temporary_feedback": list(session.temporary_feedback),
            },
            tool_schemas=tool_registry.model_schemas(),
            tool_history=[dict(step) for step in steps],
            validation_feedback=list(validation_feedback),
            remaining_steps=model_step_limit - decision_index,
        )
        try:
            decision = llm_provider.next_decision(turn)
        except LLMResponseError as error:
            validation_feedback.append(
                f"provider response rejected: {error}; return exactly one "
                "valid tool call with all required arguments"
            )
            decisions.append(
                {
                    "kind": "provider_response_error",
                    "summary": str(error),
                    "decision_index": decision_index + 1,
                }
            )
            continue
        except LLMProviderError as error:
            if provider_search_available:
                validation_feedback.append(
                    f"provider response unavailable after external search: {error}"
                )
                decisions.append(
                    {
                        "kind": "provider_response_error",
                        "summary": str(error),
                        "decision_index": decision_index + 1,
                    }
                )
                break
            raise
        except Exception as error:
            raise LLMProviderError(
                f"provider {llm_provider.name} failed: {error}"
            ) from error

        decision_record = decision.to_dict()
        decision_record["decision_index"] = decision_index + 1
        decisions.append(decision_record)

        if decision.kind == "update":
            updated_request = apply_request_patch(request, decision.request_patch)
            if updated_request == request:
                validation_feedback.append(
                    "request update ignored because it made no effective "
                    "change; call a data or ranking tool next"
                )
            else:
                request = updated_request
                validation_feedback.append(
                    "structured request updated and accepted; call a tool next"
                )
            continue
        request = apply_request_patch(request, decision.request_patch)

        if decision.kind == "finish":
            eligible = _filter_ranked_candidates(
                best,
                request=request,
                session=session,
                provider_search_available=provider_search_available,
                apply_query_filters=apply_query_filters,
            )
            if len(eligible) >= request.top_k:
                return (
                    request,
                    eligible[: request.top_k],
                    seed_song_ids,
                    missing_seed_song_ids,
                    rank_attempts,
                    "goal_satisfied",
                    decision.response_text,
                    decisions,
                )
            if profile_empty:
                return (
                    request,
                    [],
                    seed_song_ids,
                    missing_seed_song_ids,
                    rank_attempts,
                    "empty_profile",
                    decision.response_text,
                    decisions,
                )
            validation_feedback.append(
                f"finish rejected: {len(eligible)} eligible songs for "
                f"requested top_k={request.top_k}"
            )
            continue

        if provider_search_available and str(decision.tool_name) in {
            "L3.retrieve_candidates",
            "get_similar_tracks",
            "L4.rank_candidates",
            "rank_candidates",
        }:
            validation_feedback.append(
                f"{decision.tool_name} ignored because external provider "
                "search is enabled; use search_tracks with a different query"
            )
            continue

        arguments = validate_tool_arguments(decision, user_id, request)
        call_key = (str(decision.tool_name), _stable_arguments(arguments))
        if call_key in executed_calls:
            validation_feedback.append(
                f"duplicate {decision.tool_name} call ignored; choose a "
                "different tool, normally L4.rank_candidates"
            )
            continue
        executed_calls.add(call_key)
        try:
            observation = tool_registry.call(str(decision.tool_name), **arguments)
        except Exception as error:
            raise AgentLoopError(
                f"tool {decision.tool_name} failed validation or execution: "
                f"{error}"
            ) from error

        step = {
            "step": len(steps) + 1,
            "tool": observation.tool,
            "loop_contract": LOOP_CONTRACT_VERSION,
            "loop_phase": loop_phase_for_tool(observation.tool),
            "arguments": dict(arguments),
            "observation": observation.to_dict(),
            "decision": decision.summary,
            "decision_source": "model",
        }
        steps.append(step)

        if observation.tool in {"L1.inspect_user_profile", "get_user_memory"}:
            profile_empty = observation.status == "empty"
        if observation.tool == "search_tracks":
            provider_ranked = _rank_provider_tracks(
                observation.data.get("tracks", []),
                request=request,
            )
            eligible = _filter_ranked_candidates(
                provider_ranked,
                request=request,
                session=session,
                provider_search_available=True,
                apply_query_filters=apply_query_filters,
            )
            if len(eligible) > len(best):
                best = eligible
            validation_feedback.append(
                f"search_tracks produced {len(eligible)} eligible external "
                f"tracks for requested top_k={request.top_k}"
            )
            if len(eligible) >= request.top_k:
                decisions.append(
                    {
                        "kind": "program_finish",
                        "summary": (
                            "program validation accepted enough eligible "
                            "tracks after provider search"
                        ),
                        "decision_index": decision_index + 1,
                    }
                )
                return (
                    request,
                    eligible[: request.top_k],
                    seed_song_ids,
                    missing_seed_song_ids,
                    rank_attempts,
                    "goal_satisfied",
                    None,
                    decisions,
                )
        if observation.tool in {"L4.rank_candidates", "rank_candidates"}:
            rank_attempts += 1
            last_ranked_request = request
            seed_song_ids = [
                str(value) for value in observation.data.get("seed_song_ids", [])
            ]
            missing_seed_song_ids = [
                str(value)
                for value in observation.data.get("missing_seed_song_ids", [])
            ]
            eligible = apply_query_filters(
                [dict(song) for song in observation.data.get("ranked_songs", [])],
                request,
                session=session,
            )
            if len(eligible) > len(best):
                best = eligible
            validation_feedback.append(
                f"L4 produced {len(eligible)} eligible songs for "
                f"requested top_k={request.top_k}"
            )
            if len(eligible) >= request.top_k:
                decisions.append(
                    {
                        "kind": "program_finish",
                        "summary": (
                            "program validation accepted enough eligible "
                            "songs after L4 ranking"
                        ),
                        "decision_index": decision_index + 1,
                    }
                )
                return (
                    request,
                    eligible[: request.top_k],
                    seed_song_ids,
                    missing_seed_song_ids,
                    rank_attempts,
                    "goal_satisfied",
                    None,
                    decisions,
                )

    eligible = _filter_ranked_candidates(
        best,
        request=request,
        session=session,
        provider_search_available=provider_search_available,
        apply_query_filters=apply_query_filters,
    )
    if (
        not provider_search_available
        and
        not profile_empty
        and (last_ranked_request is None or last_ranked_request != request)
    ):
        (
            eligible,
            guard_seed_song_ids,
            guard_missing_seed_song_ids,
        ) = guarded_model_ranking(
            user_id=user_id,
            request=request,
            session=session,
            steps=steps,
            tool_registry=tool_registry,
            apply_query_filters=apply_query_filters,
        )
        seed_song_ids = guard_seed_song_ids
        missing_seed_song_ids = guard_missing_seed_song_ids
        rank_attempts += 1
        decisions.append(
            {
                "kind": "program_guard",
                "summary": (
                    "model exhausted its decision budget without a ranking "
                    "for the final request; program executed one validated "
                    "L4 ranking call"
                ),
                "decision_index": model_step_limit + 1,
            }
        )
    if len(eligible) >= request.top_k:
        stop_reason = "goal_satisfied"
    elif profile_empty:
        stop_reason = "empty_profile"
    return (
        request,
        eligible[: request.top_k],
        seed_song_ids,
        missing_seed_song_ids,
        rank_attempts,
        stop_reason,
        response_text,
        decisions,
    )


def _stable_arguments(arguments: dict[str, Any]) -> str:
    import json

    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _run_provider_search(
    *,
    user_id: str,
    request: AgentRequest,
    session: Any,
    steps: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    apply_query_filters: QueryFilter,
    decision_source: str,
) -> list[dict[str, Any]]:
    memory = _read_user_memory(
        user_id=user_id,
        steps=steps,
        tool_registry=tool_registry,
        decision_source=decision_source,
    )
    arguments = {
        "query": _provider_search_query(request),
        "limit": min(max(request.top_k * 2, request.top_k), 10),
    }
    observation = tool_registry.call("search_tracks", **arguments)
    steps.append(
        {
            "step": len(steps) + 1,
            "tool": observation.tool,
            "loop_contract": LOOP_CONTRACT_VERSION,
            "loop_phase": loop_phase_for_tool(observation.tool),
            "arguments": dict(arguments),
            "observation": observation.to_dict(),
            "decision": "search external music provider before local fallback",
            "decision_source": decision_source,
        }
    )
    provider_ranked = _rank_provider_tracks(
        observation.data.get("tracks", []),
        request=request,
        user_memory=memory,
    )
    return _filter_external_candidates(
        provider_ranked,
        request,
        session=session,
    )


def _filter_ranked_candidates(
    ranked_songs: list[dict[str, Any]],
    *,
    request: AgentRequest,
    session: Any,
    provider_search_available: bool,
    apply_query_filters: QueryFilter,
) -> list[dict[str, Any]]:
    if provider_search_available:
        return _filter_external_candidates(
            ranked_songs,
            request,
            session=session,
        )
    return apply_query_filters(
        ranked_songs,
        request,
        session=session,
    )


def _filter_external_candidates(
    ranked_songs: list[dict[str, Any]],
    request: AgentRequest,
    *,
    session: Any,
) -> list[dict[str, Any]]:
    from .session_ranking import build_session_ranking_context

    context = build_session_ranking_context(session, request)
    eligible: list[dict[str, Any]] = []
    artist_counts: dict[str, int] = {}
    for song in ranked_songs:
        song_id = str(song.get("song_id") or "")
        if not song_id or song_id in context.excluded_song_ids:
            continue
        if any(
            _external_song_matches(song, term)
            for term in context.exclude_terms
        ):
            continue
        artist = str(song.get("artist") or "").casefold()
        if (
            artist
            and request.max_per_artist > 0
            and artist_counts.get(artist, 0) >= request.max_per_artist
        ):
            continue
        if artist:
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
        enriched = dict(song)
        evidence = dict(enriched.get("evidence") or {})
        evidence.update(context.evidence())
        enriched["evidence"] = evidence
        eligible.append(enriched)
    return eligible


def _external_song_matches(song: dict[str, Any], term: str) -> bool:
    normalized_term = " ".join(str(term).casefold().split())
    if not normalized_term:
        return False
    labels = [
        song.get("title"),
        song.get("artist"),
        song.get("album"),
        *list(song.get("genres") or []),
        *list(song.get("tags") or []),
    ]
    haystack = " ".join(str(label) for label in labels if label).casefold()
    return normalized_term in haystack


def _read_user_memory(
    *,
    user_id: str,
    steps: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    decision_source: str,
) -> dict[str, Any]:
    tool_name = (
        "get_user_memory"
        if "get_user_memory" in set(tool_registry.names())
        else "L1.inspect_user_profile"
    )
    observation = tool_registry.call(tool_name, user_id=user_id)
    steps.append(
        {
            "step": len(steps) + 1,
            "tool": observation.tool,
            "loop_contract": LOOP_CONTRACT_VERSION,
            "loop_phase": loop_phase_for_tool(observation.tool),
            "arguments": {"user_id": user_id},
            "observation": observation.to_dict(),
            "decision": "read user memory before scoring external candidates",
            "decision_source": decision_source,
        }
    )
    return dict(observation.data)


def _provider_search_query(request: AgentRequest) -> str:
    terms: list[str] = []
    minimum_year = _minimum_release_year(request)
    if minimum_year is not None:
        terms.append(str(minimum_year))
    lowered_query = request.query.casefold()
    if any(marker in lowered_query for marker in ("英伦", "british", "uk ")):
        terms.append("british")
    if any(marker in lowered_query for marker in ("独立", "indie")):
        terms.append("indie")
    if any(marker in lowered_query for marker in ("摇滚", "rock")):
        terms.append("rock")
    for term in request.preference_terms:
        if term and term not in {"post-2020", "pre-2020"}:
            terms.extend(str(term).split())
    terms = _unique_terms(terms)
    if terms:
        return " ".join(terms)
    return request.query


def _rank_provider_tracks(
    tracks: Any,
    *,
    request: AgentRequest,
    user_memory: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(tracks, list):
        return []
    ranked: list[dict[str, Any]] = []
    total = max(len(tracks), 1)
    preference_terms = [term.casefold() for term in request.preference_terms]
    memory = user_memory or {}
    minimum_year = _minimum_release_year(request)
    for index, track in enumerate(tracks, start=1):
        if not isinstance(track, dict):
            continue
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        track_id = str(track.get("track_id") or "").strip()
        if not title or not artist or not track_id:
            continue
        release_year = _release_year(track.get("release_year"))
        if minimum_year is not None and (
            release_year is None or release_year < minimum_year
        ):
            continue
        tags = _weighted_keys(track.get("tags"))
        genres = _weighted_keys(track.get("genres"))
        labels = " ".join(
            [
                title,
                artist,
                str(track.get("album") or ""),
                *tags,
                *genres,
            ]
        ).casefold()
        query_match = any(term in labels for term in preference_terms)
        profile_score, profile_matches = _profile_match_score(
            artist=artist,
            tags=tags,
            genres=genres,
            labels=labels,
            user_memory=memory,
        )
        provider_rank_score = max(0.1, 1 - ((index - 1) / total))
        score_breakdown = {
            "provider_rank": round(provider_rank_score * 0.35, 3),
            "query_match": 0.25 if query_match else 0.0,
            "profile_match": round(profile_score * 0.25, 3),
            "metadata_quality": 0.15 if track.get("album") else 0.08,
        }
        final_score = min(1.0, round(sum(score_breakdown.values()), 3))
        ranked.append(
            {
                "rank": len(ranked) + 1,
                "song_id": track_id,
                "title": title,
                "artist": artist,
                "album": track.get("album"),
                "release_year": release_year,
                "duration_ms": track.get("duration_ms"),
                "genres": genres,
                "tags": tags,
                "final_score": final_score,
                "base_score": final_score,
                "score_breakdown": score_breakdown,
                "diversity_penalty": 0.0,
                "ranking_reasons": _provider_reasons(
                    track,
                    query_match,
                    profile_matches=profile_matches,
                ),
                "best_seed_song_id": None,
                "retrieval_sources": [
                    f"{track.get('provider') or 'provider'}_search"
                ],
                "provider": track.get("provider"),
                "spotify_track_id": _spotify_track_id(track),
                "spotify_url": _external_url(track, "spotify"),
                "preview_url": track.get("preview_url"),
                "image_url": track.get("image_url"),
                "preview_available": bool(_spotify_track_id(track)),
            }
        )
    return ranked


def _minimum_release_year(request: AgentRequest) -> int | None:
    query = request.query.casefold()
    match = re.search(
        r"((?:19|20)\d{2})\s*(?:年)?\s*(?:之后|以后|后|以来|onwards?|after|since|\\+)",
        query,
        re.I,
    )
    if match:
        return int(match.group(1))
    for term in request.preference_terms:
        term_match = re.fullmatch(r"post[- ]?((?:19|20)\d{2})", term, re.I)
        if term_match:
            return int(term_match.group(1))
    return None


def _release_year(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def _unique_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        term = str(value).strip()
        if term and term not in result:
            result.append(term)
    return result


def _weighted_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value if key]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _profile_match_score(
    *,
    artist: str,
    tags: list[str],
    genres: list[str],
    labels: str,
    user_memory: dict[str, Any],
) -> tuple[float, list[str]]:
    matches: list[str] = []
    score = 0.0
    artist_preferences = _preference_mapping(user_memory, "artist_preferences")
    genre_preferences = _preference_mapping(user_memory, "genre_preferences")
    tag_preferences = _preference_mapping(user_memory, "tag_preferences")

    artist_label = artist.casefold()
    for name, weight in artist_preferences.items():
        if name.casefold() in artist_label or name.casefold() in labels:
            score += weight
            matches.append(f"artist:{name}")

    genre_labels = {value.casefold() for value in genres}
    for name, weight in genre_preferences.items():
        lowered = name.casefold()
        if lowered in genre_labels or lowered in labels:
            score += weight
            matches.append(f"genre:{name}")

    tag_labels = {value.casefold() for value in tags}
    for name, weight in tag_preferences.items():
        lowered = name.casefold()
        if lowered in tag_labels or lowered in labels:
            score += weight
            matches.append(f"tag:{name}")

    return min(score, 1.0), matches[:3]


def _preference_mapping(
    user_memory: dict[str, Any],
    field_name: str,
) -> dict[str, float]:
    value = user_memory.get(field_name)
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for name, weight in value.items():
        if not isinstance(name, str) or not isinstance(weight, (int, float)):
            continue
        result[name] = max(0.0, min(float(weight), 1.0))
    return result


def _provider_reasons(
    track: dict[str, Any],
    query_match: bool,
    *,
    profile_matches: list[str],
) -> list[str]:
    reasons = ["found by external music provider search"]
    if query_match:
        reasons.append("matches the current listening request")
    if profile_matches:
        reasons.append("matches user music profile")
    if track.get("album") or track.get("release_year"):
        reasons.append("has enough metadata for explanation")
    return reasons


def _spotify_track_id(track: dict[str, Any]) -> str | None:
    track_id = str(track.get("track_id") or "")
    if track_id.startswith("spotify:track:"):
        return track_id.rsplit(":", 1)[-1] or None
    external_urls = track.get("external_urls")
    if isinstance(external_urls, dict):
        spotify = str(external_urls.get("spotify") or "")
        marker = "/track/"
        if marker in spotify:
            return spotify.split(marker, 1)[1].split("?", 1)[0] or None
    return None


def _external_url(track: dict[str, Any], provider: str) -> str | None:
    external_urls = track.get("external_urls")
    if not isinstance(external_urls, dict):
        return None
    value = external_urls.get(provider)
    return str(value) if value else None
