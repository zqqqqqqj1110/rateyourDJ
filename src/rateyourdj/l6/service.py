from __future__ import annotations

from typing import Any
from uuid import uuid4

from rateyourdj.l1 import ProfileNotFoundError
from rateyourdj.l2 import JsonSongStore
from rateyourdj.l4 import RecommendationRankingService

from .agent_tool_registry import AgentToolRegistryV1
from .agent_tool_schemas import AGENT_TOOL_SCHEMA_VERSION
from .errors import AgentLoopError
from .guards import unique
from .loop_contract import LOOP_CONTRACT_VERSION, recommendation_loop_plan
from .model_runner import execute_model_loop
from .models import (
    TRAJECTORY_SCHEMA_VERSION,
    AgentRequest,
    AgentResponse,
    AgentTrajectory,
)
from .parser import parse_agent_request
from .provider import (
    LLMProvider,
    LLMProviderError,
)
from .query_filters import apply_query_filters
from .ranking_runner import execute_ranking_loop
from .runtime import record_step
from .session_context import request_with_session_context
from .session_ranking import (
    SESSION_MEMORY_RANKING_FIELDS,
    build_session_ranking_context,
)
from .sessions import AgentSession, JsonSessionStore
from .store import JsonTrajectoryStore
from .tool_registry import AgentToolRegistry
from .tool_guards import validated_model_tool_arguments


class RecommendationAgentService:
    def __init__(
        self,
        ranking_service: RecommendationRankingService,
        song_store: JsonSongStore,
        trajectory_store: JsonTrajectoryStore,
        session_store: JsonSessionStore | None = None,
        tool_registry: AgentToolRegistry | None = None,
        model_tool_registry: AgentToolRegistryV1 | None = None,
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
        if model_tool_registry is not None:
            self.model_tool_registry = model_tool_registry
        elif tool_registry is not None:
            self.model_tool_registry = tool_registry
        else:
            self.model_tool_registry = AgentToolRegistryV1.default(
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
        request = request_with_session_context(parsed, session)
        plan = _initialized_loop_plan()
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
                ) = execute_model_loop(
                    user_id=user_id,
                    request=request,
                    session=session,
                    steps=steps,
                    max_steps=max_steps,
                    llm_provider=self.llm_provider,
                    tool_registry=self.model_tool_registry,
                    validate_tool_arguments=self._validate_model_tool_arguments,
                    apply_query_filters=self._apply_query_filters,
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
            record_step(
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
                ) = execute_ranking_loop(
                    user_id=user_id,
                    request=request,
                    session=session,
                    steps=steps,
                    max_steps=max_steps,
                    tool_registry=self.tool_registry,
                    apply_query_filters=self._apply_query_filters,
                )

        if (
            executed_mode == "model"
            and stop_reason == "goal_satisfied"
            and not _has_external_search_source(recommendations)
        ):
            recommendations = self._apply_query_filters(
                recommendations,
                request,
                session=session,
            )[:request.top_k]
            if len(recommendations) < request.top_k:
                stop_reason = "insufficient_candidates"

        for index, song in enumerate(recommendations, start=1):
            song["rank"] = index

        trajectory_id = str(uuid4())
        used_external_search = any(
            step.get("tool") == "search_tracks"
            for step in steps
        )
        message = response_text or _response_message(
            request,
            recommendations,
            stop_reason=stop_reason,
            attempts=rank_attempts,
            used_external_search=used_external_search,
        )
        _finalize_loop_plan(
            plan,
            steps=steps,
            request=request,
            recommendations=recommendations,
            stop_reason=stop_reason,
            message=message,
        )
        session.turn_count += 1
        session.current_intent = request.intent
        session.last_user_query = request.query
        session.last_trajectory_id = trajectory_id
        session.last_top_k = request.top_k
        session.last_max_per_artist = request.max_per_artist
        session.last_min_retrieval_score = request.min_retrieval_score
        session.active_constraints["exclude_seen"] = request.exclude_seen
        if request.preference_terms:
            session.preference_terms = list(request.preference_terms)
        session.exclude_terms = unique(
            [*session.exclude_terms, *request.exclude_terms]
        )
        session.seed_track_ids = unique(
            [
                *session.seed_track_ids,
                *seed_song_ids,
            ]
        )
        session.last_recommendation_ids = [
            str(song["song_id"]) for song in recommendations
        ]
        session.seen_song_ids = unique(
            [
                *session.seen_song_ids,
                *(str(song["song_id"]) for song in recommendations),
            ]
        )
        self.session_store.save(session)
        _mark_phase(
            plan,
            "trajectory_write",
            status="completed",
            summary="saved the trajectory and updated session short-term memory",
            session_writes=[
                "turn_count",
                "current_intent",
                "last_user_query",
                "active_constraints",
                "preference_terms",
                "exclude_terms",
                "seed_track_ids",
                "last_recommendation_ids",
                "seen_track_ids",
                "last_run_id",
            ],
        )

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
            trajectory_schema_version=TRAJECTORY_SCHEMA_VERSION,
            loop_contract_version=LOOP_CONTRACT_VERSION,
            tool_schema_version=AGENT_TOOL_SCHEMA_VERSION,
            user_memory_snapshot=_user_memory_snapshot(
                self.ranking_service,
                user_id,
            ),
            session_memory_snapshot=_session_memory_snapshot(session),
            retrieval_snapshot=_retrieval_snapshot(
                recommendations=recommendations,
                seed_song_ids=seed_song_ids,
                missing_seed_song_ids=missing_seed_song_ids,
                stop_reason=stop_reason,
                used_external_search=used_external_search,
            ),
            ranked_candidates=[dict(song) for song in recommendations],
            final_recommendations=[dict(song) for song in recommendations],
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

    def _validate_model_tool_arguments(
        self,
        decision: Any,
        user_id: str,
        request: AgentRequest,
    ) -> dict[str, Any]:
        return validated_model_tool_arguments(
            decision,
            user_id,
            request,
            available_tools=set(self.model_tool_registry.names()),
        )

    def _apply_query_filters(
        self,
        ranked_songs: list[dict[str, Any]],
        request: AgentRequest,
        *,
        session: AgentSession,
    ) -> list[dict[str, Any]]:
        context = build_session_ranking_context(session, request)
        return apply_query_filters(
            ranked_songs,
            request,
            song_store=self.song_store,
            context=context,
        )


def _response_message(
    request: AgentRequest,
    songs: list[dict[str, Any]],
    *,
    stop_reason: str,
    attempts: int,
    used_external_search: bool = False,
) -> str:
    if stop_reason == "empty_profile":
        return "收藏中还没有可用的种子歌曲，暂时无法生成推荐。"
    if not songs:
        if used_external_search:
            return (
                "外部音乐搜索已执行，但没有找到符合当前条件的歌曲。"
                "可以放宽年份、风格或数量要求后再试。"
            )
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
    source_labels = {
        str(source)
        for song in songs
        for source in song.get("retrieval_sources", [])
    }
    if any(source.endswith("_search") for source in source_labels):
        return "；".join(details) + "。每首歌都基于外部搜索结果和用户画像评分。"
    return "；".join(details) + "。每首歌都保留了 L4 分数拆解和推荐原因。"


def _has_external_search_source(songs: list[dict[str, Any]]) -> bool:
    return any(
        str(source).endswith("_search")
        for song in songs
        for source in song.get("retrieval_sources", [])
    )


def _user_memory_snapshot(
    ranking_service: RecommendationRankingService,
    user_id: str,
) -> dict[str, Any]:
    profile_store = ranking_service.profile_store
    if not profile_store.exists(user_id):
        return {
            "user_id": user_id,
            "exists": False,
            "collection_song_ids": [],
            "artist_preferences": {},
            "genre_preferences": {},
            "tag_preferences": {},
            "feedback_memory_count": 0,
        }
    try:
        profile = profile_store.load(user_id)
    except ProfileNotFoundError:
        return {
            "user_id": user_id,
            "exists": False,
            "collection_song_ids": [],
            "artist_preferences": {},
            "genre_preferences": {},
            "tag_preferences": {},
            "feedback_memory_count": 0,
        }
    return {
        "user_id": profile.user_id,
        "exists": True,
        "collection_song_ids": list(profile.collection_song_ids),
        "artist_preferences": dict(profile.artist_preferences),
        "genre_preferences": dict(profile.genre_preferences),
        "tag_preferences": dict(profile.tag_preferences),
        "feedback_memory_count": len(profile.feedback_memory),
    }


def _session_memory_snapshot(session: AgentSession) -> dict[str, Any]:
    return {
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
        "temporary_feedback": [
            dict(item) for item in session.temporary_feedback
        ],
    }


def _retrieval_snapshot(
    *,
    recommendations: list[dict[str, Any]],
    seed_song_ids: list[str],
    missing_seed_song_ids: list[str],
    stop_reason: str,
    used_external_search: bool,
) -> dict[str, Any]:
    retrieval_sources = sorted(
        {
            str(source)
            for song in recommendations
            for source in song.get("retrieval_sources", [])
        }
    )
    return {
        "seed_song_ids": list(seed_song_ids),
        "missing_seed_song_ids": list(missing_seed_song_ids),
        "retrieval_sources": retrieval_sources,
        "ranked_candidate_count": len(recommendations),
        "final_recommendation_count": len(recommendations),
        "used_external_search": used_external_search,
        "stop_reason": stop_reason,
    }


def _initialized_loop_plan() -> list[dict[str, Any]]:
    return [
        {
            **item,
            "status": "pending",
        }
        for item in recommendation_loop_plan()
    ]


def _mark_phase(
    plan: list[dict[str, Any]],
    phase_name: str,
    *,
    status: str,
    summary: str,
    session_reads: list[str] | None = None,
    session_writes: list[str] | None = None,
) -> None:
    for item in plan:
        if item.get("phase") != phase_name:
            continue
        item["status"] = status
        item["summary"] = summary
        if session_reads:
            item["session_memory_reads"] = list(session_reads)
        if session_writes:
            item["session_memory_writes"] = list(session_writes)
        return


def _finalize_loop_plan(
    plan: list[dict[str, Any]],
    *,
    steps: list[dict[str, Any]],
    request: AgentRequest,
    recommendations: list[dict[str, Any]],
    stop_reason: str,
    message: str,
) -> None:
    phase_to_tools: dict[str, list[str]] = {}
    for step in steps:
        phase = step.get("loop_phase")
        tool = step.get("tool")
        if not isinstance(phase, str) or not isinstance(tool, str):
            continue
        phase_to_tools.setdefault(phase, []).append(tool)

    _mark_phase(
        plan,
        "memory_read",
        status="completed",
        summary="loaded session context and read user memory before ranking",
        session_reads=["session_id", "turn_count", "current_intent"],
    )
    _mark_phase(
        plan,
        "query_understanding",
        status="completed",
        summary="parsed the query and applied session context to the request",
        session_reads=_query_understanding_reads(request),
    )

    for phase_name in (
        "external_search",
        "candidate_enrichment",
        "candidate_ranking",
        "retrieval_diagnostics",
    ):
        tools = phase_to_tools.get(phase_name, [])
        if tools:
            session_reads = (
                list(SESSION_MEMORY_RANKING_FIELDS)
                if phase_name == "candidate_ranking"
                else None
            )
            _mark_phase(
                plan,
                phase_name,
                status="completed",
                summary="executed tools: " + ", ".join(tools),
                session_reads=session_reads,
            )
        else:
            _mark_phase(
                plan,
                phase_name,
                status="skipped",
                summary="phase was not needed for this run",
            )

    explanation_status = "completed" if message else "skipped"
    explanation_summary = (
        f"generated explanation for {len(recommendations)} recommendations"
        if message
        else "no explanation was generated"
    )
    _mark_phase(
        plan,
        "explanation",
        status=explanation_status,
        summary=explanation_summary,
        session_reads=list(SESSION_MEMORY_RANKING_FIELDS),
    )
    _mark_phase(
        plan,
        "feedback_write",
        status="pending",
        summary=(
            "await explicit user feedback before writing temporary or durable memory"
            if stop_reason != "empty_profile"
            else "no feedback write is possible until recommendations exist"
        ),
    )


def _query_understanding_reads(request: AgentRequest) -> list[str]:
    fields = ["current_intent"]
    if request.intent == "more":
        fields.extend(
            [
                "active_constraints",
                "preference_terms",
                "exclude_terms",
                "seen_track_ids",
            ]
        )
    return fields
