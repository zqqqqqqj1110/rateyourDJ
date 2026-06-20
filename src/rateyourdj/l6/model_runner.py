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
        executed_calls.add(
            (
                "search_tracks",
                _stable_arguments(
                    {
                        "query": _provider_search_query(request),
                        "limit": min(max(request.top_k * 2, request.top_k), 10),
                    }
                ),
            )
        )
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
            user_memory = _latest_user_memory_from_steps(steps)
            similar_artist_candidates = _similar_artist_items_from_steps(
                steps=steps,
                request=request,
            )
            provider_ranked = _rank_provider_tracks(
                observation.data.get("tracks", []),
                request=request,
                user_memory=user_memory,
                similar_artist_candidates=similar_artist_candidates,
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
    elif _external_search_failed(steps):
        stop_reason = "external_search_failed"
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
    similar_artist_candidates = _run_similar_artist_expansion(
        request=request,
        steps=steps,
        tool_registry=tool_registry,
        decision_source=decision_source,
    )
    search_entries = _provider_search_entries(
        request,
        similar_artist_candidates=similar_artist_candidates,
    )
    observations = _execute_provider_search_entries(
        search_entries=search_entries,
        steps=steps,
        tool_registry=tool_registry,
        decision_source=decision_source,
    )
    provider_tracks = _merge_provider_search_tracks(observations)
    provider_ranked = _rank_provider_tracks(
        provider_tracks,
        request=request,
        user_memory=memory,
        similar_artist_candidates=similar_artist_candidates,
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
    from .session_ranking import (
        build_session_ranking_context,
        track_signature_from_ranked_song,
    )

    context = build_session_ranking_context(session, request)
    eligible: list[dict[str, Any]] = []
    artist_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    seen_signatures: set[str] = set(context.excluded_track_signatures)
    family_limit = _external_family_limit(request)
    for song in ranked_songs:
        song_id = str(song.get("song_id") or "")
        if not song_id or song_id in context.excluded_song_ids:
            continue
        if _exclude_reference_artist_track(song, request):
            continue
        if not _external_candidate_is_relevant(song, request):
            continue
        if any(
            _external_song_matches(song, term)
            for term in context.exclude_terms
        ):
            continue
        signature = track_signature_from_ranked_song(song)
        if signature and signature in seen_signatures:
            continue
        artist = str(song.get("artist") or "").casefold()
        if (
            artist
            and request.max_per_artist > 0
            and artist_counts.get(artist, 0) >= request.max_per_artist
        ):
            continue
        family_key = _artist_family_key(str(song.get("artist") or ""))
        if (
            family_key
            and family_limit > 0
            and family_counts.get(family_key, 0) >= family_limit
        ):
            continue
        if artist:
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
        if family_key:
            family_counts[family_key] = family_counts.get(family_key, 0) + 1
        enriched = dict(song)
        evidence = dict(enriched.get("evidence") or {})
        evidence.update(context.evidence())
        enriched["evidence"] = evidence
        if signature:
            seen_signatures.add(signature)
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


def _external_candidate_is_relevant(
    song: dict[str, Any],
    request: AgentRequest,
) -> bool:
    score_breakdown = song.get("score_breakdown")
    if not isinstance(score_breakdown, dict):
        return True
    semantic_match = any(
        float(score_breakdown.get(key) or 0.0) > 0.0
        for key in (
            "query_match",
            "reference_match",
            "expanded_reference_match",
            "profile_match",
        )
    )
    if semantic_match:
        return True
    if request.reference_artists:
        return False
    return float(song.get("final_score") or 0.0) >= 0.25


def _exclude_reference_artist_track(
    song: dict[str, Any],
    request: AgentRequest,
) -> bool:
    if not _query_implies_similarity_reference(request.query):
        return False
    artist = str(song.get("artist") or "").casefold().strip()
    if not artist:
        return False
    return artist in {
        str(reference).casefold().strip()
        for reference in request.reference_artists
        if str(reference).strip()
    }


def _query_implies_similarity_reference(query: str) -> bool:
    lowered = str(query or "").casefold()
    return any(
        marker in lowered
        for marker in (
            "像",
            "更像",
            "类似",
            "差不多",
            "same vibe",
            "similar to",
            "more like",
            "like ",
        )
    )


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


def _latest_user_memory_from_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    for step in reversed(steps):
        if step.get("tool") not in {"get_user_memory", "L1.inspect_user_profile"}:
            continue
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        data = observation.get("data")
        if isinstance(data, dict):
            return dict(data)
    return {}


def _provider_search_query(request: AgentRequest) -> str:
    entries = _provider_search_entries(request, similar_artist_candidates=[])
    if not entries:
        return request.query
    return str(entries[0].get("query") or request.query)


def _provider_query_terms(
    request: AgentRequest,
    banned_terms: set[str] | None = None,
) -> list[str]:
    blocked = banned_terms or _provider_query_banned_terms(request)
    terms: list[str] = []
    for value in request.preference_terms:
        terms.extend(_normalized_provider_terms(value, blocked))
    for value in request.refinement_notes:
        terms.extend(_normalized_provider_terms(value, blocked))
    terms.extend(_normalized_provider_terms(request.query, blocked))
    return _unique_terms(terms)


def _provider_search_entries(
    request: AgentRequest,
    *,
    similar_artist_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    base_terms = _provider_query_terms(request)
    limit = min(max(request.top_k * 2, request.top_k), 10)
    if not _query_implies_similarity_reference(request.query):
        query = _join_search_terms(
            [*_year_terms(request), *request.reference_artists, *base_terms]
        ) or request.query
        return [
            {
                "query": query,
                "limit": limit,
                "tier": "broad_query",
                "anchor_artists": list(request.reference_artists),
                "expanded_artists": [],
            }
        ]

    entries: list[dict[str, Any]] = []
    grouped = _group_similar_artists_by_source(similar_artist_candidates)
    for anchor in request.reference_artists:
        cleaned_anchor = str(anchor).strip()
        if not cleaned_anchor:
            continue
        entries.append(
            {
                "query": _join_search_terms(
                    [*_year_terms(request), cleaned_anchor, *base_terms]
                )
                or request.query,
                "limit": limit,
                "tier": "anchor_artist",
                "anchor_artists": [cleaned_anchor],
                "expanded_artists": [],
            }
        )
        expanded_for_anchor = grouped.get(cleaned_anchor.casefold(), [])
        for batch in _chunked(expanded_for_anchor[:6], size=3):
            expanded_names = [str(item.get("name") or "").strip() for item in batch]
            query = _join_search_terms(
                [*_year_terms(request), *expanded_names, *base_terms]
            )
            if not query:
                continue
            entries.append(
                {
                    "query": query,
                    "limit": limit,
                    "tier": "expanded_artist_batch",
                    "anchor_artists": [cleaned_anchor],
                    "expanded_artists": expanded_names,
                }
            )

    blended_candidates = [
        str(item.get("name") or "").strip()
        for item in similar_artist_candidates
        if str(item.get("name") or "").strip()
    ]
    blended_candidates = _unique_terms(blended_candidates)[:6]
    if len(blended_candidates) > 1:
        blended_query = _join_search_terms(
            [*_year_terms(request), *blended_candidates[:4], *base_terms]
        )
        if blended_query:
            entries.append(
                {
                    "query": blended_query,
                    "limit": limit,
                    "tier": "blended_expansion",
                    "anchor_artists": list(request.reference_artists),
                    "expanded_artists": blended_candidates[:4],
                }
            )

    style_query = _join_search_terms([*_year_terms(request), *base_terms])
    if style_query:
        entries.append(
            {
                "query": style_query,
                "limit": limit,
                "tier": "style_fallback",
                "anchor_artists": list(request.reference_artists),
                "expanded_artists": [],
            }
        )

    return _dedupe_search_entries(entries)


def _provider_query_banned_terms(request: AgentRequest) -> set[str]:
    blocked: set[str] = set()
    for value in [*request.avoid_artists, *request.exclude_terms]:
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold()):
            if token:
                blocked.add(token)
    if _query_implies_similarity_reference(request.query):
        for value in request.reference_artists:
            for token in re.findall(r"[a-z0-9]+", str(value or "").casefold()):
                if token:
                    blocked.add(token)
    return blocked


def _year_terms(request: AgentRequest) -> list[str]:
    minimum_year = _minimum_release_year(request)
    return [str(minimum_year)] if minimum_year is not None else []


def _normalized_provider_terms(
    value: Any,
    banned_terms: set[str] | None = None,
) -> list[str]:
    text = str(value or "").strip().casefold()
    if not text:
        return []
    blocked = banned_terms or set()
    terms: list[str] = []
    if any(marker in text for marker in ("英伦", "british", "uk ")):
        terms.extend(["britpop", "british"])
    if any(marker in text for marker in ("独立", "indie")):
        terms.append("indie")
    if any(marker in text for marker in ("摇滚", "rock")):
        terms.append("rock")
    if any(marker in text for marker in ("旋律", "melodic")):
        terms.append("melodic")
    if "shoegaze" in text:
        terms.append("shoegaze")
    if "psychedelic" in text:
        terms.append("psychedelic")

    stopwords = {
        "a",
        "an",
        "and",
        "as",
        "for",
        "give",
        "kind",
        "like",
        "less",
        "more",
        "not",
        "of",
        "or",
        "similar",
        "song",
        "songs",
        "than",
        "the",
        "to",
        "track",
        "tracks",
        "with",
    }
    for token in re.findall(r"[a-z0-9]+", text):
        if token.isdigit():
            continue
        if token in blocked:
            continue
        if token in stopwords:
            continue
        if token.startswith("post") and token[4:].isdigit():
            continue
        if token.startswith("pre") and token[3:].isdigit():
            continue
        if token in {"oasis", "blur", "british", "britpop", "indie", "rock", "melodic", "shoegaze", "psychedelic"}:
            terms.append(token)
    return _unique_terms(terms)


def _query_artist_hints(
    query: str,
    banned_terms: set[str] | None = None,
) -> list[str]:
    blocked = banned_terms or set()
    hints: list[str] = []
    for fragment in re.findall(
        r"[A-Za-z][A-Za-z0-9'.-]*(?:\s+[A-Za-z][A-Za-z0-9'.-]*)*",
        str(query or ""),
    ):
        normalized = " ".join(fragment.split())
        tokens = re.findall(r"[a-z0-9]+", normalized.casefold())
        if not tokens:
            continue
        if all(token in blocked for token in tokens):
            continue
        if len(tokens) == 1 and tokens[0] in {
            "after",
            "and",
            "before",
            "british",
            "indie",
            "like",
            "melodic",
            "more",
            "not",
            "or",
            "rock",
            "similar",
        }:
            continue
        hints.append(normalized)
    return _unique_terms(hints)


def _chunked(values: list[Any], *, size: int) -> list[list[Any]]:
    return [
        values[index:index + size]
        for index in range(0, len(values), size)
        if values[index:index + size]
    ]


def _join_search_terms(
    values: list[str],
    *,
    max_chars: int = 180,
) -> str:
    terms = _unique_terms([value for value in values if str(value).strip()])
    result: list[str] = []
    current_length = 0
    for term in terms:
        addition = len(term) if not result else len(term) + 1
        if result and current_length + addition > max_chars:
            break
        result.append(term)
        current_length += addition
    return " ".join(result)


def _dedupe_search_entries(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for entry in entries:
        query = str(entry.get("query") or "").strip()
        if not query:
            continue
        normalized_query = query.casefold()
        if normalized_query in seen_queries:
            continue
        deduped.append(
            {
                "query": query,
                "limit": int(entry.get("limit") or 10),
                "tier": str(entry.get("tier") or "search"),
                "anchor_artists": _unique_terms(
                    [str(item) for item in entry.get("anchor_artists", [])]
                ),
                "expanded_artists": _unique_terms(
                    [str(item) for item in entry.get("expanded_artists", [])]
                ),
            }
        )
        seen_queries.add(normalized_query)
    return deduped


def _group_similar_artists_by_source(
    candidates: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        source_artist = str(item.get("source_artist") or "").strip()
        name = str(item.get("name") or "").strip()
        if not source_artist or not name:
            continue
        key = source_artist.casefold()
        grouped.setdefault(key, [])
        grouped[key].append(item)
    for key, values in grouped.items():
        values.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("name") or "").casefold(),
            )
        )
        grouped[key] = _dedupe_similar_artist_items(values)
    return grouped


def _dedupe_similar_artist_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("name") or "").strip().casefold()
        if not key or key in seen:
            continue
        result.append(dict(item))
        seen.add(key)
    return result


def _execute_provider_search_entries(
    *,
    search_entries: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    decision_source: str,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for entry in search_entries:
        arguments = {
            "query": str(entry["query"]),
            "limit": int(entry["limit"]),
            "search_tier": entry.get("tier"),
            "anchor_artists": list(entry.get("anchor_artists", [])),
            "expanded_artists": list(entry.get("expanded_artists", [])),
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
        observations.append(
            {
                "entry": dict(entry),
                "observation": observation.to_dict(),
            }
        )
    return observations


def _merge_provider_search_tracks(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    insertion_order: list[str] = []
    for item in observations:
        entry = dict(item.get("entry") or {})
        observation = dict(item.get("observation") or {})
        data = observation.get("data")
        if not isinstance(data, dict):
            continue
        tracks = data.get("tracks")
        if not isinstance(tracks, list):
            continue
        for track in tracks:
            if not isinstance(track, dict):
                continue
            track_id = str(track.get("track_id") or "").strip()
            title = str(track.get("title") or "").strip()
            artist = str(track.get("artist") or "").strip()
            if track_id:
                key = track_id
            elif title and artist:
                key = f"{artist.casefold()}::{title.casefold()}"
            else:
                continue
            if key not in merged:
                merged_track = dict(track)
                merged_track["search_queries"] = [str(entry.get("query") or "")]
                merged_track["search_tiers"] = [str(entry.get("tier") or "search")]
                merged_track["search_anchor_artists"] = list(
                    entry.get("anchor_artists", [])
                )
                merged_track["search_expanded_artists"] = list(
                    entry.get("expanded_artists", [])
                )
                insertion_order.append(key)
                merged[key] = merged_track
                continue
            existing = merged[key]
            existing["search_queries"] = _unique_terms(
                [
                    *list(existing.get("search_queries", [])),
                    str(entry.get("query") or ""),
                ]
            )
            existing["search_tiers"] = _unique_terms(
                [
                    *list(existing.get("search_tiers", [])),
                    str(entry.get("tier") or "search"),
                ]
            )
            existing["search_anchor_artists"] = _unique_terms(
                [
                    *list(existing.get("search_anchor_artists", [])),
                    *list(entry.get("anchor_artists", [])),
                ]
            )
            existing["search_expanded_artists"] = _unique_terms(
                [
                    *list(existing.get("search_expanded_artists", [])),
                    *list(entry.get("expanded_artists", [])),
                ]
            )
    return [merged[key] for key in insertion_order]


def _external_search_failed(steps: list[dict[str, Any]]) -> bool:
    search_steps = [
        step for step in steps if step.get("tool") == "search_tracks"
    ]
    if not search_steps:
        return False
    for step in search_steps:
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        data = observation.get("data")
        if not isinstance(data, dict):
            continue
        tracks = data.get("tracks")
        provider_results = data.get("provider_results")
        if tracks or provider_results:
            return False
    for step in search_steps:
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        diagnostics = observation.get("diagnostics")
        retryable = bool(observation.get("retryable"))
        if retryable and isinstance(diagnostics, list) and diagnostics:
            return True
    return False


def _rank_provider_tracks(
    tracks: Any,
    *,
    request: AgentRequest,
    user_memory: dict[str, Any] | None = None,
    similar_artist_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(tracks, list):
        return []
    ranked: list[dict[str, Any]] = []
    total = max(len(tracks), 1)
    preference_terms = [term.casefold() for term in request.preference_terms]
    reference_artists = [
        artist.casefold() for artist in request.reference_artists
    ]
    expanded_reference_artists = {
        str(item.get("name") or "").strip().casefold()
        for item in list(similar_artist_candidates or [])
        if str(item.get("name") or "").strip()
    }
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
        artist_label = artist.casefold()
        reference_match = any(anchor == artist_label for anchor in reference_artists)
        expanded_reference_match = artist_label in expanded_reference_artists
        profile_score, profile_matches = _profile_match_score(
            artist=artist,
            tags=tags,
            genres=genres,
            labels=labels,
            user_memory=memory,
        )
        provider_rank_score = max(0.1, 1 - ((index - 1) / total))
        multi_query_boost = 0.03 * max(
            0,
            len(_weighted_keys(track.get("search_queries"))) - 1,
        )
        score_breakdown = {
            "provider_rank": round(provider_rank_score * 0.30, 3),
            "query_match": 0.20 if query_match else 0.0,
            "reference_match": 0.10 if reference_match else 0.0,
            "expanded_reference_match": 0.08 if expanded_reference_match else 0.0,
            "profile_match": round(profile_score * 0.25, 3),
            "metadata_quality": 0.15 if track.get("album") else 0.08,
            "multi_query_match": round(multi_query_boost, 3),
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
                    reference_match=reference_match,
                    expanded_reference_match=expanded_reference_match,
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
                "search_queries": list(track.get("search_queries", [])),
                "search_tiers": list(track.get("search_tiers", [])),
                "search_anchor_artists": list(
                    track.get("search_anchor_artists", [])
                ),
                "search_expanded_artists": list(
                    track.get("search_expanded_artists", [])
                ),
                "artist_family_key": _artist_family_key(artist),
            }
        )
    ranked.sort(
        key=lambda song: (
            -float(song.get("final_score") or 0.0),
            str(song.get("artist") or "").casefold(),
            str(song.get("title") or "").casefold(),
        )
    )
    return _apply_external_diversity_penalties(ranked, request=request)


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
    seen: set[str] = set()
    for value in values:
        term = str(value).strip()
        normalized = term.casefold()
        if term and normalized not in seen:
            result.append(term)
            seen.add(normalized)
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
    reference_match: bool,
    expanded_reference_match: bool,
    profile_matches: list[str],
) -> list[str]:
    reasons = ["found by external music provider search"]
    if reference_match:
        reasons.append("matches the requested reference artist")
    elif expanded_reference_match:
        reasons.append("matches an expanded similar-artist seed")
    if query_match:
        reasons.append("matches the current listening request")
    if profile_matches:
        reasons.append("matches user music profile")
    if track.get("album") or track.get("release_year"):
        reasons.append("has enough metadata for explanation")
    return reasons


def _run_similar_artist_expansion(
    *,
    request: AgentRequest,
    steps: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    decision_source: str,
) -> list[dict[str, Any]]:
    if not request.reference_artists:
        return []
    if not _query_implies_similarity_reference(request.query):
        return []
    if "get_similar_artists" not in set(tool_registry.names()):
        return []
    arguments = {
        "artist_names": list(request.reference_artists),
        "limit": min(max(request.top_k * 3, 6), 12),
    }
    observation = tool_registry.call("get_similar_artists", **arguments)
    steps.append(
        {
            "step": len(steps) + 1,
            "tool": observation.tool,
            "loop_contract": LOOP_CONTRACT_VERSION,
            "loop_phase": loop_phase_for_tool(observation.tool),
            "arguments": dict(arguments),
            "observation": observation.to_dict(),
            "decision": "expand similar artists before provider search",
            "decision_source": decision_source,
        }
    )
    return _similar_artist_items_from_observation(observation.data)


def _similar_artist_names_from_steps(
    *,
    steps: list[dict[str, Any]],
    request: AgentRequest,
) -> list[str]:
    if not request.reference_artists:
        return []
    for step in reversed(steps):
        if step.get("tool") != "get_similar_artists":
            continue
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        data = observation.get("data")
        if not isinstance(data, dict):
            continue
        names = [
            str(item.get("name") or "").strip()
            for item in _similar_artist_items_from_observation(data)
            if str(item.get("name") or "").strip()
        ]
        if names:
            return names
    return []


def _similar_artist_items_from_steps(
    *,
    steps: list[dict[str, Any]],
    request: AgentRequest,
) -> list[dict[str, Any]]:
    if not request.reference_artists:
        return []
    for step in reversed(steps):
        if step.get("tool") != "get_similar_artists":
            continue
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        data = observation.get("data")
        if not isinstance(data, dict):
            continue
        items = _similar_artist_items_from_observation(data)
        if items:
            return items
    return []


def _similar_artist_items_from_observation(
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    artists = data.get("artists", [])
    if not isinstance(artists, list):
        return []
    result: list[dict[str, Any]] = []
    for item in artists:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if name:
            result.append(
                {
                    "name": name,
                    "source_artist": str(
                        item.get("source_artist", "")
                    ).strip(),
                    "score": float(item.get("score") or 0.0),
                }
            )
    result.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("name") or "").casefold(),
        )
    )
    return _dedupe_similar_artist_items(result)


def _apply_external_diversity_penalties(
    ranked: list[dict[str, Any]],
    *,
    request: AgentRequest,
) -> list[dict[str, Any]]:
    family_counts: dict[str, int] = {}
    signature_counts: dict[str, int] = {}
    adjusted: list[dict[str, Any]] = []
    for song in ranked:
        adjusted_song = dict(song)
        family_key = str(adjusted_song.get("artist_family_key") or "")
        signature = _external_track_signature(adjusted_song)
        family_penalty = 0.0
        signature_penalty = 0.0
        if family_key:
            family_penalty = 0.12 * family_counts.get(family_key, 0)
            family_counts[family_key] = family_counts.get(family_key, 0) + 1
        if signature:
            signature_penalty = 0.20 * signature_counts.get(signature, 0)
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
        penalty = round(family_penalty + signature_penalty, 3)
        adjusted_song["diversity_penalty"] = penalty
        adjusted_song["final_score"] = max(
            0.0,
            round(float(adjusted_song.get("base_score") or 0.0) - penalty, 3),
        )
        adjusted.append(adjusted_song)
    adjusted.sort(
        key=lambda song: (
            -float(song.get("final_score") or 0.0),
            str(song.get("artist") or "").casefold(),
            str(song.get("title") or "").casefold(),
        )
    )
    for index, song in enumerate(adjusted, start=1):
        song["rank"] = index
    return adjusted


def _external_track_signature(song: dict[str, Any]) -> str:
    from .session_ranking import track_signature_from_ranked_song

    return track_signature_from_ranked_song(song)


def _artist_family_key(artist: str) -> str:
    normalized_artist = str(artist or "").casefold()
    if not normalized_artist:
        return ""
    surname_patterns = (
        r"\b([a-z]+) gallagher\b",
        r"\b([a-z]+) ashcroft\b",
        r"\b([a-z]+) albarn\b",
    )
    for pattern in surname_patterns:
        match = re.search(pattern, normalized_artist)
        if match:
            return match.group(0).split()[-1]
    if "gallagher" in normalized_artist:
        return "gallagher"
    return ""


def _external_family_limit(request: AgentRequest) -> int:
    lowered = request.query.casefold()
    if any(
        marker in lowered
        for marker in ("重复", "不要重复", "不要重覆", "same song", "duplicate")
    ):
        return 1
    return max(1, request.max_per_artist)


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
