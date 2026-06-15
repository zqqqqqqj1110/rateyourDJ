from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from rateyourdj.agent_tools import ToolObservation
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l4 import RecommendationRankingService

from .models import AgentRequest, AgentResponse, AgentTrajectory
from .parser import parse_agent_request
from .provider import (
    AgentDecision,
    AgentTurn,
    LLMProvider,
    LLMProviderError,
    LLMResponseError,
)
from .sessions import AgentSession, JsonSessionStore
from .store import JsonTrajectoryStore
from .tool_registry import AgentToolRegistry


class AgentLoopError(RuntimeError):
    pass


class RecommendationAgentService:
    def __init__(
        self,
        ranking_service: RecommendationRankingService,
        song_store: JsonSongStore,
        trajectory_store: JsonTrajectoryStore,
        session_store: JsonSessionStore | None = None,
        tool_registry: AgentToolRegistry | None = None,
        llm_provider: LLMProvider | None = None,
        agent_mode: str = "auto",
    ) -> None:
        self.ranking_service = ranking_service
        self.song_store = song_store
        self.trajectory_store = trajectory_store
        self.session_store = session_store or JsonSessionStore(
            trajectory_store.root.parent / "sessions"
        )
        self.tool_registry = tool_registry or AgentToolRegistry.default(
            ranking_service.profile_store,
            song_store,
        )
        if agent_mode not in {"auto", "model", "rules"}:
            raise ValueError("agent_mode must be auto, model, or rules")
        self.llm_provider = llm_provider
        self.agent_mode = agent_mode

    def recommend(
        self,
        user_id: str,
        query: str,
        *,
        default_top_k: int = 10,
        session_id: str | None = None,
        max_steps: int = 5,
        agent_mode: str | None = None,
    ) -> AgentResponse:
        if max_steps < 2 or max_steps > 10:
            raise ValueError("max_steps must be between 2 and 10")
        resolved_mode = agent_mode or self.agent_mode
        if resolved_mode not in {"auto", "model", "rules"}:
            raise ValueError("agent_mode must be auto, model, or rules")

        parsed = parse_agent_request(query, default_top_k=default_top_k)
        session = self.session_store.load_or_create(user_id, session_id)
        request = self._request_with_session_context(parsed, session)
        plan = [
            {
                "goal": "inspect whether the user profile can seed recommendations",
                "tool": "L1.inspect_user_profile",
            },
            {
                "goal": "rank enough candidates to satisfy the request",
                "tool": "L4.rank_candidates",
                "retry_policy": "expand pool and relax retrieval constraints",
            },
            {
                "goal": "diagnose retrieval when ranking remains insufficient",
                "tool": "L3.retrieve_candidates",
                "condition": "remaining step budget",
            },
        ]
        steps: list[dict[str, Any]] = []
        agent_decisions: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []
        seed_song_ids: list[str] = []
        missing_seed_song_ids: list[str] = []
        rank_attempts = 0
        stop_reason = "empty_profile"
        response_text: str | None = None
        executed_mode = "rules"
        provider_name: str | None = None
        fallback_reason: str | None = None

        use_model = resolved_mode in {"auto", "model"} and self.llm_provider is not None
        if use_model:
            provider_name = self.llm_provider.name
            try:
                (
                    request,
                    recommendations,
                    seed_song_ids,
                    missing_seed_song_ids,
                    rank_attempts,
                    stop_reason,
                    response_text,
                    agent_decisions,
                ) = self._execute_model_loop(
                    user_id,
                    request,
                    session,
                    steps,
                    max_steps=max_steps,
                )
                executed_mode = "model"
            except (AgentLoopError, LLMProviderError) as error:
                fallback_reason = str(error)
                steps.clear()
                agent_decisions.append(
                    {
                        "kind": "fallback",
                        "summary": "model path failed; use deterministic rules",
                        "reason": fallback_reason,
                    }
                )
        elif resolved_mode == "model":
            fallback_reason = "model mode requested but no LLM provider is configured"
            agent_decisions.append(
                {
                    "kind": "fallback",
                    "summary": "no provider configured; use deterministic rules",
                    "reason": fallback_reason,
                }
            )

        if executed_mode == "rules":
            profile_arguments = {"user_id": user_id}
            profile_observation = self.tool_registry.call(
                "L1.inspect_user_profile",
                **profile_arguments,
            )
            self._record_step(
                steps,
                profile_arguments,
                profile_observation,
                (
                    "stop because the collection has no recommendation seeds"
                    if profile_observation.status == "empty"
                    else "continue to candidate ranking"
                ),
            )
            if profile_observation.status != "empty":
                (
                    recommendations,
                    seed_song_ids,
                    missing_seed_song_ids,
                    rank_attempts,
                    stop_reason,
                ) = self._execute_ranking_loop(
                    user_id,
                    request,
                    session,
                    steps,
                    max_steps=max_steps,
                )

        if executed_mode == "model" and stop_reason == "goal_satisfied":
            recommendations = self._apply_query_filters(
                recommendations,
                request,
                excluded_song_ids=(
                    set(session.seen_song_ids)
                    if request.exclude_seen
                    else set()
                ),
            )[:request.top_k]
            if len(recommendations) < request.top_k:
                stop_reason = "insufficient_candidates"

        for index, song in enumerate(recommendations, start=1):
            song["rank"] = index

        trajectory_id = str(uuid4())
        message = response_text or _response_message(
            request,
            recommendations,
            stop_reason=stop_reason,
            attempts=rank_attempts,
        )
        session.turn_count += 1
        session.last_trajectory_id = trajectory_id
        session.last_top_k = request.top_k
        session.last_max_per_artist = request.max_per_artist
        session.last_min_retrieval_score = request.min_retrieval_score
        if request.preference_terms:
            session.preference_terms = list(request.preference_terms)
        session.exclude_terms = _unique(
            [*session.exclude_terms, *request.exclude_terms]
        )
        session.seen_song_ids = _unique(
            [
                *session.seen_song_ids,
                *(str(song["song_id"]) for song in recommendations),
            ]
        )
        self.session_store.save(session)

        trajectory = AgentTrajectory(
            trajectory_id=trajectory_id,
            session_id=session.session_id,
            turn_index=session.turn_count,
            user_id=user_id,
            query=request.query,
            parsed_request=request.to_dict(),
            plan=plan,
            tool_calls=steps,
            recommendations=recommendations,
            response_text=message,
            stop_reason=stop_reason,
            agent_mode=executed_mode,
            provider=provider_name,
            fallback_reason=fallback_reason,
            agent_decisions=agent_decisions,
        )
        self.trajectory_store.save(trajectory)
        return AgentResponse(
            trajectory_id=trajectory_id,
            session_id=session.session_id,
            user_id=user_id,
            query=request.query,
            parsed_request=request,
            message=message,
            ranked_songs=recommendations,
            seed_song_ids=seed_song_ids,
            missing_seed_song_ids=missing_seed_song_ids,
            stop_reason=stop_reason,
            attempts=rank_attempts,
            tool_calls=steps,
            agent_mode=executed_mode,
            provider=provider_name,
            fallback_reason=fallback_reason,
            agent_decisions=agent_decisions,
        )

    def _execute_model_loop(
        self,
        user_id: str,
        request: AgentRequest,
        session: AgentSession,
        steps: list[dict[str, Any]],
        *,
        max_steps: int,
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
        if self.llm_provider is None:
            raise AgentLoopError("no LLM provider is configured")

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

        model_step_limit = min(max_steps, 5)
        for decision_index in range(model_step_limit):
            turn = AgentTurn(
                user_id=user_id,
                query=request.query,
                request=request.to_dict(),
                session={
                    "session_id": session.session_id,
                    "turn_count": session.turn_count,
                    "preference_terms": list(session.preference_terms),
                    "exclude_terms": list(session.exclude_terms),
                    "seen_song_ids": list(session.seen_song_ids),
                },
                tool_schemas=self.tool_registry.model_schemas(),
                tool_history=[dict(step) for step in steps],
                validation_feedback=list(validation_feedback),
                remaining_steps=model_step_limit - decision_index,
            )
            try:
                decision = self.llm_provider.next_decision(turn)
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
            except LLMProviderError:
                raise
            except Exception as error:
                raise LLMProviderError(
                    f"provider {self.llm_provider.name} failed: {error}"
                ) from error

            decision_record = decision.to_dict()
            decision_record["decision_index"] = decision_index + 1
            decisions.append(decision_record)

            if decision.kind == "update":
                updated_request = self._apply_request_patch(
                    request,
                    decision.request_patch,
                )
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
            request = self._apply_request_patch(
                request,
                decision.request_patch,
            )

            if decision.kind == "finish":
                eligible = self._apply_query_filters(
                    best,
                    request,
                    excluded_song_ids=(
                        set(session.seen_song_ids)
                        if request.exclude_seen
                        else set()
                    ),
                )
                if len(eligible) >= request.top_k:
                    return (
                        request,
                        eligible[:request.top_k],
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

            arguments = self._validated_model_tool_arguments(
                decision,
                user_id,
                request,
            )
            call_key = (
                str(decision.tool_name),
                _stable_arguments(arguments),
            )
            if call_key in executed_calls:
                validation_feedback.append(
                    f"duplicate {decision.tool_name} call ignored; choose a "
                    "different tool, normally L4.rank_candidates"
                )
                continue
            executed_calls.add(call_key)
            try:
                observation = self.tool_registry.call(
                    str(decision.tool_name),
                    **arguments,
                )
            except Exception as error:
                raise AgentLoopError(
                    f"tool {decision.tool_name} failed validation or execution: "
                    f"{error}"
                ) from error

            step = {
                "step": len(steps) + 1,
                "tool": observation.tool,
                "arguments": dict(arguments),
                "observation": observation.to_dict(),
                "decision": decision.summary,
                "decision_source": "model",
            }
            steps.append(step)

            if observation.tool == "L1.inspect_user_profile":
                profile_empty = observation.status == "empty"
            if observation.tool == "L4.rank_candidates":
                rank_attempts += 1
                last_ranked_request = request
                seed_song_ids = [
                    str(value)
                    for value in observation.data.get("seed_song_ids", [])
                ]
                missing_seed_song_ids = [
                    str(value)
                    for value in observation.data.get(
                        "missing_seed_song_ids",
                        [],
                    )
                ]
                eligible = self._apply_query_filters(
                    [
                        dict(song)
                        for song in observation.data.get("ranked_songs", [])
                    ],
                    request,
                    excluded_song_ids=(
                        set(session.seen_song_ids)
                        if request.exclude_seen
                        else set()
                    ),
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
                        eligible[:request.top_k],
                        seed_song_ids,
                        missing_seed_song_ids,
                        rank_attempts,
                        "goal_satisfied",
                        None,
                        decisions,
                    )

        eligible = self._apply_query_filters(
            best,
            request,
            excluded_song_ids=(
                set(session.seen_song_ids)
                if request.exclude_seen
                else set()
            ),
        )
        if (
            not profile_empty
            and (
                last_ranked_request is None
                or last_ranked_request != request
            )
        ):
            (
                eligible,
                guard_seed_song_ids,
                guard_missing_seed_song_ids,
            ) = self._guarded_model_ranking(
                user_id,
                request,
                session,
                steps,
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
            eligible[:request.top_k],
            seed_song_ids,
            missing_seed_song_ids,
            rank_attempts,
            stop_reason,
            response_text,
            decisions,
        )

    def _guarded_model_ranking(
        self,
        user_id: str,
        request: AgentRequest,
        session: AgentSession,
        steps: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        pool_size = min(max(request.top_k * 5, request.top_k), 1000)
        arguments = {
            "user_id": user_id,
            "top_k": pool_size,
            "candidate_pool_size": pool_size,
            "max_per_artist": request.max_per_artist,
            "min_retrieval_score": request.min_retrieval_score,
        }
        observation = self.tool_registry.call(
            "L4.rank_candidates",
            **arguments,
        )
        steps.append(
            {
                "step": len(steps) + 1,
                "tool": observation.tool,
                "arguments": dict(arguments),
                "observation": observation.to_dict(),
                "decision": (
                    "execute validated L4 fallback because the model did not rank"
                ),
                "decision_source": "program_guard",
            }
        )
        eligible = self._apply_query_filters(
            [
                dict(song)
                for song in observation.data.get("ranked_songs", [])
            ],
            request,
            excluded_song_ids=(
                set(session.seen_song_ids)
                if request.exclude_seen
                else set()
            ),
        )
        return (
            eligible,
            [
                str(value)
                for value in observation.data.get("seed_song_ids", [])
            ],
            [
                str(value)
                for value in observation.data.get(
                    "missing_seed_song_ids",
                    [],
                )
            ],
        )

    @staticmethod
    def _apply_request_patch(
        request: AgentRequest,
        patch: dict[str, Any],
    ) -> AgentRequest:
        if not patch:
            return request
        allowed = {
            "top_k",
            "max_per_artist",
            "min_retrieval_score",
            "preference_terms",
            "exclude_terms",
            "intent",
            "exclude_seen",
        }
        if not set(patch) <= allowed:
            raise AgentLoopError("model request patch contains unknown fields")
        if (
            "top_k" in patch
            and _query_has_explicit_count(request.query)
            and patch["top_k"] != request.top_k
        ):
            raise AgentLoopError("model cannot override an explicit song count")
        if (
            request.max_per_artist == 1
            and patch.get("max_per_artist", 1) != 1
        ):
            raise AgentLoopError(
                "model cannot relax requested artist diversity"
            )
        if (
            request.min_retrieval_score > 0
            and "min_retrieval_score" in patch
            and (
                not isinstance(patch["min_retrieval_score"], (int, float))
                or patch["min_retrieval_score"] < request.min_retrieval_score
            )
        ):
            raise AgentLoopError(
                "model cannot relax an explicit similarity requirement"
            )
        if request.intent == "more" and patch.get("intent", "more") != "more":
            raise AgentLoopError("model cannot cancel the requested more intent")
        if request.exclude_seen and patch.get("exclude_seen", True) is not True:
            raise AgentLoopError("model cannot include songs already shown")
        if "exclude_terms" in patch and not isinstance(
            patch["exclude_terms"],
            list,
        ):
            raise AgentLoopError("model exclude_terms must be a string list")
        if "preference_terms" in patch and not isinstance(
            patch["preference_terms"],
            list,
        ):
            raise AgentLoopError("model preference_terms must be a string list")
        values = request.to_dict()
        values.update(patch)
        values["preference_terms"] = _unique(
            [
                *request.preference_terms,
                *(
                    patch.get("preference_terms", [])
                    if isinstance(patch.get("preference_terms", []), list)
                    else []
                ),
            ]
        )
        values["exclude_terms"] = _unique(
            [
                *request.exclude_terms,
                *(
                    patch.get("exclude_terms", [])
                    if isinstance(patch.get("exclude_terms", []), list)
                    else []
                ),
            ]
        )
        if (
            isinstance(values["top_k"], bool)
            or not isinstance(values["top_k"], int)
            or not 1 <= values["top_k"] <= 50
        ):
            raise AgentLoopError("model top_k must be between 1 and 50")
        if (
            isinstance(values["max_per_artist"], bool)
            or not isinstance(values["max_per_artist"], int)
            or not 1 <= values["max_per_artist"] <= 10
        ):
            raise AgentLoopError("model max_per_artist must be between 1 and 10")
        min_score = values["min_retrieval_score"]
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not 0 <= float(min_score) <= 1
        ):
            raise AgentLoopError(
                "model min_retrieval_score must be between 0 and 1"
            )
        for field_name in ("preference_terms", "exclude_terms"):
            terms = values[field_name]
            if (
                not isinstance(terms, list)
                or not all(isinstance(term, str) and term.strip() for term in terms)
            ):
                raise AgentLoopError(f"model {field_name} must be a string list")
            values[field_name] = _unique(
                [_normalized(term) for term in terms if _normalized(term)]
            )
        values["preference_terms"] = _canonical_preference_terms(
            values["preference_terms"]
        )
        if values["intent"] not in {"recommend", "more"}:
            raise AgentLoopError("model intent must be recommend or more")
        if request.intent != "more" and values["intent"] == "more":
            raise AgentLoopError("model cannot invent a more intent")
        if values["intent"] != "more":
            values["exclude_seen"] = False
        if not isinstance(values["exclude_seen"], bool):
            raise AgentLoopError("model exclude_seen must be boolean")
        return AgentRequest(
            query=request.query,
            top_k=values["top_k"],
            max_per_artist=values["max_per_artist"],
            min_retrieval_score=float(min_score),
            preference_terms=values["preference_terms"],
            exclude_terms=values["exclude_terms"],
            intent=values["intent"],
            exclude_seen=values["exclude_seen"],
        )

    def _validated_model_tool_arguments(
        self,
        decision: AgentDecision,
        user_id: str,
        request: AgentRequest,
    ) -> dict[str, Any]:
        tool_name = str(decision.tool_name)
        if tool_name not in self.tool_registry.names():
            raise AgentLoopError(f"model selected unknown tool: {tool_name}")
        arguments = dict(decision.arguments)
        supplied_user_id = arguments.get("user_id")
        if supplied_user_id not in (None, user_id):
            raise AgentLoopError("model cannot access another user's data")

        if tool_name in {
            "L1.inspect_user_profile",
            "L3.retrieve_candidates",
            "L4.rank_candidates",
            "L5.inspect_feedback_state",
        }:
            arguments["user_id"] = user_id

        if tool_name in {"L1.inspect_user_profile", "L5.inspect_feedback_state"}:
            if set(arguments) != {"user_id"}:
                raise AgentLoopError(f"{tool_name} accepts only user_id")
            return arguments

        if tool_name == "L2.inspect_song_profile":
            if set(arguments) != {"song_id"}:
                raise AgentLoopError(
                    "L2.inspect_song_profile requires only song_id"
                )
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
            self._validate_retrieval_arguments(arguments, request)
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
            arguments.setdefault(
                "min_retrieval_score",
                request.min_retrieval_score,
            )
            resolved = self._validated_ranking_arguments(
                arguments,
                {},
                request,
            )
            if resolved is None:
                raise AgentLoopError("L4 arguments violate program constraints")
            return resolved

        raise AgentLoopError(f"tool is not allowed in recommendation loop: {tool_name}")

    @staticmethod
    def _validate_retrieval_arguments(
        arguments: dict[str, Any],
        request: AgentRequest,
    ) -> None:
        for name, maximum in (("top_k", 1000), ("max_per_artist", 10)):
            value = arguments[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise AgentLoopError(f"{name} is outside its allowed range")
        if request.max_per_artist == 1 and arguments["max_per_artist"] != 1:
            raise AgentLoopError("model cannot relax requested artist diversity")
        min_score = arguments["min_score"]
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not 0 <= float(min_score) <= 1
        ):
            raise AgentLoopError("min_score must be between 0 and 1")

    def _execute_ranking_loop(
        self,
        user_id: str,
        request: AgentRequest,
        session: AgentSession,
        steps: list[dict[str, Any]],
        *,
        max_steps: int,
    ) -> tuple[list[dict[str, Any]], list[str], list[str], int, str]:
        pool_size = max(request.top_k * 5, request.top_k)
        best: list[dict[str, Any]] = []
        seed_song_ids: list[str] = []
        missing_seed_song_ids: list[str] = []
        rank_attempts = 0
        arguments = {
            "user_id": user_id,
            "top_k": pool_size,
            "candidate_pool_size": pool_size,
            "max_per_artist": request.max_per_artist,
            "min_retrieval_score": request.min_retrieval_score,
        }

        while len(steps) < max_steps and rank_attempts < 3:
            rank_attempts += 1
            observation = self.tool_registry.call(
                "L4.rank_candidates",
                **arguments,
            )
            data = observation.data
            seed_song_ids = [
                str(value) for value in data.get("seed_song_ids", [])
            ]
            missing_seed_song_ids = [
                str(value)
                for value in data.get("missing_seed_song_ids", [])
            ]
            filtered = self._apply_query_filters(
                [
                    dict(song)
                    for song in data.get("ranked_songs", [])
                ],
                request,
                excluded_song_ids=(
                    set(session.seen_song_ids)
                    if request.exclude_seen
                    else set()
                ),
            )
            if len(filtered) > len(best):
                best = filtered

            if len(filtered) >= request.top_k:
                self._record_step(
                    steps,
                    arguments,
                    observation,
                    f"goal satisfied with {len(filtered)} eligible songs",
                )
                return (
                    filtered[:request.top_k],
                    seed_song_ids,
                    missing_seed_song_ids,
                    rank_attempts,
                    "goal_satisfied",
                )

            selected_action = self._select_suggested_action(
                observation,
                arguments,
                request,
                allowed_tools=(
                    {"L4.rank_candidates"}
                    & set(self.tool_registry.names())
                ),
            )
            can_retry = (
                rank_attempts < 3
                and len(steps) + 1 < max_steps
                and selected_action is not None
            )
            decision = (
                "execute the tool's validated suggested action"
                if can_retry
                else (
                    "stop because the tool marked the result as non-retryable"
                    if not observation.retryable
                    else "stop because no executable suggested action remains"
                )
            )
            self._record_step(
                steps,
                arguments,
                observation,
                decision,
                selected_action=selected_action if can_retry else None,
            )
            if not can_retry:
                break
            arguments = dict(selected_action["resolved_arguments"])

        if len(steps) < max_steps:
            max_per_artist = int(arguments["max_per_artist"])
            min_score = float(arguments["min_retrieval_score"])
            candidate_pool_size = int(arguments["candidate_pool_size"])
            arguments = {
                "user_id": user_id,
                "top_k": min(candidate_pool_size, 1000),
                "max_per_artist": max_per_artist,
                "min_score": min_score,
            }
            observation = self.tool_registry.call(
                "L3.retrieve_candidates",
                **arguments,
            )
            self._record_step(
                steps,
                arguments,
                observation,
                "stop after recording the limiting retrieval diagnostics",
            )

        return (
            best[:request.top_k],
            seed_song_ids,
            missing_seed_song_ids,
            rank_attempts,
            "insufficient_candidates",
        )

    def _request_with_session_context(
        self,
        request: AgentRequest,
        session: AgentSession,
    ) -> AgentRequest:
        if request.intent != "more":
            return request
        top_k = (
            request.top_k
            if _query_has_explicit_count(request.query)
            else session.last_top_k or request.top_k
        )
        max_per_artist = (
            request.max_per_artist
            if _query_requests_artist_diversity(request.query)
            else session.last_max_per_artist or request.max_per_artist
        )
        return AgentRequest(
            query=request.query,
            top_k=top_k,
            max_per_artist=max_per_artist,
            min_retrieval_score=(
                request.min_retrieval_score
                if request.min_retrieval_score > 0
                else session.last_min_retrieval_score
                or request.min_retrieval_score
            ),
            preference_terms=(
                _canonical_preference_terms(request.preference_terms)
                or _canonical_preference_terms(session.preference_terms)
            ),
            exclude_terms=_unique(
                [*session.exclude_terms, *request.exclude_terms]
            ),
            intent=request.intent,
            exclude_seen=True,
        )

    def _apply_query_filters(
        self,
        ranked_songs: list[dict[str, Any]],
        request: AgentRequest,
        *,
        excluded_song_ids: set[str],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        artist_counts: dict[str, int] = {}
        for ranked_song in ranked_songs:
            song_id = str(ranked_song["song_id"])
            if song_id in excluded_song_ids or not self.song_store.exists(song_id):
                continue
            profile = self.song_store.load(song_id)
            if request.exclude_terms and any(
                _song_matches(profile, term)
                for term in request.exclude_terms
            ):
                continue
            if request.preference_terms and not any(
                _song_matches(profile, term)
                for term in _effective_preference_terms(
                    request.preference_terms
                )
            ):
                continue
            artist = _normalized(str(profile.metadata.get("artist") or ""))
            if artist and artist_counts.get(artist, 0) >= request.max_per_artist:
                continue
            if artist:
                artist_counts[artist] = artist_counts.get(artist, 0) + 1
            result.append(ranked_song)
        return result

    @staticmethod
    def _select_suggested_action(
        observation: ToolObservation,
        current_arguments: dict[str, Any],
        request: AgentRequest,
        *,
        allowed_tools: set[str],
    ) -> dict[str, Any] | None:
        if not observation.retryable:
            return None

        selected_tool: str | None = None
        patch: dict[str, Any] = {}
        reasons: list[str] = []
        for action in observation.suggested_actions:
            tool = action.get("tool")
            arguments = action.get("arguments")
            if (
                not isinstance(tool, str)
                or tool not in allowed_tools
                or not isinstance(arguments, dict)
            ):
                continue
            if selected_tool is None:
                selected_tool = tool
            if tool != selected_tool:
                continue
            patch.update(arguments)
            reason = action.get("reason")
            if isinstance(reason, str) and reason:
                reasons.append(reason)

        if selected_tool is None or not patch:
            return None
        resolved = RecommendationAgentService._validated_ranking_arguments(
            current_arguments,
            patch,
            request,
        )
        if resolved is None or resolved == current_arguments:
            return None
        return {
            "tool": selected_tool,
            "arguments": patch,
            "resolved_arguments": resolved,
            "reasons": reasons,
        }

    @staticmethod
    def _validated_ranking_arguments(
        current: dict[str, Any],
        patch: dict[str, Any],
        request: AgentRequest,
    ) -> dict[str, Any] | None:
        allowed = {
            "top_k",
            "candidate_pool_size",
            "max_per_artist",
            "min_retrieval_score",
        }
        if not set(patch) <= allowed:
            return None

        resolved = dict(current)
        resolved.update(patch)
        if any(
            isinstance(resolved.get(name), bool)
            or not isinstance(resolved.get(name), int)
            for name in ("top_k", "candidate_pool_size", "max_per_artist")
        ):
            return None
        if (
            isinstance(resolved.get("min_retrieval_score"), bool)
            or not isinstance(
                resolved.get("min_retrieval_score"),
                (int, float),
            )
        ):
            return None

        top_k = resolved["top_k"]
        pool_size = resolved["candidate_pool_size"]
        artist_limit = resolved["max_per_artist"]
        min_score = float(resolved["min_retrieval_score"])
        if not 1 <= top_k <= 1000:
            return None
        if not top_k <= pool_size <= 1000:
            return None
        if not 1 <= artist_limit <= 10:
            return None
        if request.max_per_artist == 1 and artist_limit != 1:
            return None
        if not 0.0 <= min_score <= 1.0:
            return None

        resolved["top_k"] = top_k
        resolved["candidate_pool_size"] = pool_size
        resolved["max_per_artist"] = artist_limit
        resolved["min_retrieval_score"] = min_score
        return resolved

    @staticmethod
    def _record_step(
        steps: list[dict[str, Any]],
        arguments: dict[str, Any],
        observation: ToolObservation,
        decision: str,
        *,
        selected_action: dict[str, Any] | None = None,
    ) -> None:
        step = {
            "step": len(steps) + 1,
            "tool": observation.tool,
            "arguments": dict(arguments),
            "observation": observation.to_dict(),
            "decision": decision,
        }
        if selected_action is not None:
            step["selected_action"] = dict(selected_action)
        steps.append(step)


def _song_matches(song: SongProfile, term: str) -> bool:
    normalized_term = _normalized(term)
    if not normalized_term:
        return False
    compact_term = _compact(normalized_term)
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
        normalized_label = _normalized(str(label))
        if normalized_term in normalized_label:
            return True
        if compact_term and compact_term in _compact(normalized_label):
            return True
    return False


def _effective_preference_terms(terms: list[str]) -> list[str]:
    aliases = {
        "british rock": "british",
        "uk rock": "british",
        "english rock": "british",
        "britpop": "british",
    }
    expanded = _unique(
        [
            normalized
            for term in terms
            for normalized in (
                _normalized(term),
                aliases.get(_normalized(term), ""),
            )
            if normalized
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


def _canonical_preference_terms(terms: list[str]) -> list[str]:
    aliases = {
        "british rock": "british",
        "uk rock": "british",
        "english rock": "british",
        "britpop": "british",
        "英伦摇滚": "british",
    }
    return _unique(
        [
            aliases.get(normalized, normalized)
            for term in terms
            if (normalized := _normalized(term))
        ]
    )


def _normalized(value: str) -> str:
    return " ".join(value.replace("_", " ").strip().casefold().split())


def _compact(value: str) -> str:
    return "".join(character for character in value if character.isalnum())


def _query_has_explicit_count(query: str) -> bool:
    return bool(
        re.search(r"\d{1,2}\s*(?:首|首歌|songs?)", query, re.I)
        or re.search(
            r"(?:二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*首",
            query,
        )
    )


def _stable_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _response_message(
    request: AgentRequest,
    songs: list[dict[str, Any]],
    *,
    stop_reason: str,
    attempts: int,
) -> str:
    if stop_reason == "empty_profile":
        return "收藏中还没有可用的种子歌曲，暂时无法生成推荐。"
    if not songs:
        return "执行了候选扩展和条件放宽，但当前曲库仍没有符合条件的歌曲。"

    details = [f"根据你的收藏画像和反馈，为你选出 {len(songs)} 首歌"]
    if request.intent == "more":
        details.append("已避开本次会话中展示过的歌曲")
    if request.preference_terms:
        details.append("偏好：" + "、".join(request.preference_terms))
    if request.exclude_terms:
        details.append("已排除：" + "、".join(request.exclude_terms))
    if request.max_per_artist == 1:
        details.append("已增强歌手多样性")
    if attempts > 1:
        details.append(f"经过 {attempts} 次候选调整")
    return "；".join(details) + "。每首歌都保留了 L4 分数拆解和推荐原因。"


def _query_requests_artist_diversity(query: str) -> bool:
    lowered = query.casefold()
    return any(
        marker in lowered
        for marker in (
            "多样",
            "不同歌手",
            "不要重复歌手",
            "每位歌手",
            "每个歌手",
            "diverse",
            "different artists",
            "per artist",
        )
    )
