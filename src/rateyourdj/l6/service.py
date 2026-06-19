from __future__ import annotations

from typing import Any
from uuid import uuid4

from rateyourdj.l2 import JsonSongStore
from rateyourdj.l4 import RecommendationRankingService

from .agent_tool_registry import AgentToolRegistryV1
from .errors import AgentLoopError
from .guards import unique
from .model_runner import execute_model_loop
from .models import AgentRequest, AgentResponse, AgentTrajectory
from .parser import parse_agent_request
from .provider import (
    LLMProvider,
    LLMProviderError,
)
from .query_filters import apply_query_filters
from .ranking_runner import execute_ranking_loop
from .runtime import record_step
from .session_context import request_with_session_context
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
        session.turn_count += 1
        session.last_trajectory_id = trajectory_id
        session.last_top_k = request.top_k
        session.last_max_per_artist = request.max_per_artist
        session.last_min_retrieval_score = request.min_retrieval_score
        if request.preference_terms:
            session.preference_terms = list(request.preference_terms)
        session.exclude_terms = unique(
            [*session.exclude_terms, *request.exclude_terms]
        )
        session.seen_song_ids = unique(
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
        excluded_song_ids: set[str],
    ) -> list[dict[str, Any]]:
        return apply_query_filters(
            ranked_songs,
            request,
            song_store=self.song_store,
            excluded_song_ids=excluded_song_ids,
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
