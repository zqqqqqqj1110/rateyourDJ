from __future__ import annotations

from time import perf_counter
from typing import Any
from uuid import uuid4

from rateyourdj.l1 import ProfileNotFoundError, UserProfileService
from rateyourdj.l2 import JsonSongStore
from rateyourdj.l4 import RecommendationRankingService
from rateyourdj.domain import GeneratedCandidate

from .agent_tool_registry import (
    AgentToolRegistryV1,
    discovered_track_to_ranked_song,
)
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
    track_signature_from_ranked_song,
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
        discovery_service: Any | None = None,
    ) -> None:
        self.ranking_service = ranking_service
        self.song_store = song_store
        self.trajectory_store = trajectory_store
        self.profile_service = UserProfileService(
            ranking_service.profile_store
        )
        # Used to ground tracks the chat answer proposes (Q&A turns).
        self.discovery_service = discovery_service
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
        started_at = perf_counter()
        resolved_mode = agent_mode or self.agent_mode
        if resolved_mode not in {"auto", "model", "rules"}:
            raise ValueError("agent_mode must be auto, model, or rules")

        parsed = parse_agent_request(query, default_top_k=default_top_k)
        session = self.session_store.load_or_create(user_id, session_id)
        if parsed.intent == "question":
            return self._answer_question_turn(
                user_id=user_id,
                request=parsed,
                session=session,
                resolved_mode=resolved_mode,
                started_at=started_at,
            )
        request = request_with_session_context(parsed, session)
        session_request = request
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
        session.current_intent = session_request.intent
        session.last_user_query = session_request.query
        session.last_trajectory_id = trajectory_id
        session.last_top_k = session_request.top_k
        session.last_max_per_artist = session_request.max_per_artist
        session.last_min_retrieval_score = session_request.min_retrieval_score
        session.active_constraints["exclude_seen"] = session_request.exclude_seen
        if session_request.preference_terms:
            session.preference_terms = list(session_request.preference_terms)
        session.exclude_terms = unique(
            [*session.exclude_terms, *session_request.exclude_terms]
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
        session.append_message("user", session_request.query)
        session.append_message("dj", message)
        session.seen_song_ids = unique(
            [
                *session.seen_song_ids,
                *(str(song["song_id"]) for song in recommendations),
            ]
        )
        session.seen_track_signatures = unique(
            [
                *session.seen_track_signatures,
                *[
                    signature
                    for song in recommendations
                    for signature in [track_signature_from_ranked_song(song)]
                    if signature
                ],
            ]
        )
        self.session_store.save(session)
        # 轻量长期学习：把本轮对话表达的偏好沉淀到画像的独立字段
        # (conversation_affinity)，不影响收藏重算。best-effort，失败不阻断返回。
        if session_request.preference_terms:
            try:
                self.profile_service.learn_from_conversation(
                    user_id,
                    list(session_request.preference_terms),
                )
            except Exception:  # noqa: BLE001 - learning is best-effort
                pass
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
                "seen_track_signatures",
                "last_run_id",
                "messages",
            ],
        )

        latency_ms = round((perf_counter() - started_at) * 1000, 3)
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
            latency_ms=latency_ms,
            agent_decisions=agent_decisions,
            trajectory_schema_version=TRAJECTORY_SCHEMA_VERSION,
            loop_contract_version=LOOP_CONTRACT_VERSION,
            tool_schema_version=AGENT_TOOL_SCHEMA_VERSION,
            user_memory_snapshot=_user_memory_snapshot(
                self.ranking_service,
                user_id,
            ),
            session_memory_snapshot=_session_memory_snapshot(session),
            artist_expansion_snapshot=_artist_expansion_snapshot(
                request=request,
                steps=steps,
            ),
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
            latency_ms=latency_ms,
            agent_decisions=agent_decisions,
        )

    def _answer_question_turn(
        self,
        *,
        user_id: str,
        request: AgentRequest,
        session: AgentSession,
        resolved_mode: str,
        started_at: float | None = None,
    ) -> AgentResponse:
        """Answer a conversational music question instead of recommending.

        Uses the DeepSeek provider's chat path (with recent conversation
        history for reference resolution). Falls back to a friendly nudge when
        no model is configured or the call fails. Returns an AgentResponse with
        an empty song list so the frontend renders it as a plain DJ reply.
        """
        provider_name: str | None = None
        fallback_reason: str | None = None
        answer: str | None = None
        suggested_tracks: list[dict[str, str]] = []
        use_model = (
            resolved_mode in {"auto", "model"}
            and self.llm_provider is not None
            and hasattr(self.llm_provider, "answer_question")
        )
        if use_model:
            provider_name = self.llm_provider.name
            try:
                answer, suggested_tracks = self.llm_provider.answer_question(
                    request.query,
                    history=[dict(item) for item in session.messages],
                )
            except Exception as error:  # noqa: BLE001 - degrade gracefully
                fallback_reason = str(error)
                answer = None
        elif resolved_mode == "model":
            fallback_reason = (
                "model mode requested but no LLM provider is configured"
            )

        executed_mode = "model" if answer is not None else "rules"
        message = answer or (
            "这个问题我来聊聊——不过当前没有配置对话模型（DeepSeek），"
            "暂时只能帮你推荐歌曲。配置 DEEPSEEK_API_KEY 后我就能回答这类"
            "音乐问题啦。想听歌的话，直接说想要的风格或心情就行。"
        )

        # Ground the tracks the answer proposed so each becomes a real,
        # playable card (drops anything the model hallucinated).
        ranked_songs = self._ground_suggested_tracks(suggested_tracks)

        session.turn_count += 1
        session.current_intent = "question"
        session.last_user_query = request.query
        session.append_message("user", request.query)
        session.append_message("dj", message)
        self.session_store.save(session)

        trajectory_id = str(uuid4())
        latency_ms = (
            round((perf_counter() - started_at) * 1000, 3)
            if started_at is not None
            else None
        )
        return AgentResponse(
            trajectory_id=trajectory_id,
            session_id=session.session_id,
            user_id=user_id,
            query=request.query,
            parsed_request=request,
            message=message,
            ranked_songs=ranked_songs,
            seed_song_ids=[],
            missing_seed_song_ids=[],
            stop_reason="answered_question",
            attempts=0,
            tool_calls=[],
            agent_mode=executed_mode,
            provider=provider_name,
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
            agent_decisions=[
                {
                    "kind": "question_answer",
                    "summary": (
                        "answered a music question"
                        + (
                            f" and suggested {len(ranked_songs)} tracks"
                            if ranked_songs
                            else ""
                        )
                    ),
                }
            ],
        )

    def _ground_suggested_tracks(
        self,
        suggested_tracks: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Confirm chat-proposed tracks exist and shape them as ranked songs."""
        if not suggested_tracks or self.discovery_service is None:
            return []
        candidates = [
            GeneratedCandidate(
                title=str(item.get("title") or "").strip(),
                artist=str(item.get("artist") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
            )
            for item in suggested_tracks
            if str(item.get("title") or "").strip()
            and str(item.get("artist") or "").strip()
        ]
        if not candidates:
            return []
        try:
            result = self.discovery_service.ground_candidates(
                candidates,
                intent="question_followup",
                count=len(candidates),
            )
        except Exception:  # noqa: BLE001 - grounding is best-effort
            return []
        return [
            discovered_track_to_ranked_song(track, index)
            for index, track in enumerate(result.tracks, start=1)
        ]

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
    if stop_reason == "external_search_failed":
        return (
            "外部音乐搜索执行失败，暂时无法拿到候选歌曲。"
            "请稍后重试；如果持续失败，检查 Spotify 凭证或网络连接。"
        )
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
    exclude_summary = _exclude_terms_summary(request)
    if exclude_summary:
        details.extend(exclude_summary)
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


def _exclude_terms_summary(request: AgentRequest) -> list[str]:
    terms = [str(term).strip() for term in request.exclude_terms if str(term).strip()]
    if not terms:
        return []
    lowered = [term.casefold() for term in terms]
    details: list[str] = []
    explicit_artists = [
        term
        for term in terms
        if term.casefold() not in {"重复的", "重复", "punk", "太朋克", "朋克"}
        and "这种" not in term
    ]
    if any(term in {"punk", "太朋克", "朋克"} for term in lowered):
        details.append("已避开太朋克的结果")
    if any("重复" in term for term in lowered):
        details.append("已避开重复结果")
    if any("gallagher" in term or "noel" in term or "liam" in term for term in lowered):
        details.append("已避开 Gallagher 支线结果")
    if explicit_artists:
        details.append("已避开用户明确排除的艺人")
    return details


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
        "seen_track_signatures": list(session.seen_track_signatures),
        "seed_track_ids": list(session.seed_track_ids),
        "active_constraints": dict(session.active_constraints),
        "last_run_id": session.last_run_id,
        "last_recommendation_ids": list(session.last_recommendation_ids),
        "temporary_feedback": [
            dict(item) for item in session.temporary_feedback
        ],
        "messages": [dict(item) for item in session.messages],
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


def _artist_expansion_snapshot(
    *,
    request: AgentRequest,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    for step in reversed(steps):
        if step.get("tool") != "get_similar_artists":
            continue
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        data = observation.get("data")
        if not isinstance(data, dict):
            continue
        artists = data.get("artists")
        provider_results = data.get("provider_results")
        if not isinstance(artists, list):
            artists = []
        if not isinstance(provider_results, list):
            provider_results = []
        return {
            "reference_artists": list(request.reference_artists),
            "expanded_artists": [
                str(item.get("name", "")).strip()
                for item in artists
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            ],
            "provider_results": [dict(item) for item in provider_results if isinstance(item, dict)],
            "search_queries": _artist_expansion_search_queries(steps),
            "tool_status": str(observation.get("status") or ""),
            "diagnostics": [
                str(item) for item in observation.get("diagnostics", [])
            ]
            if isinstance(observation.get("diagnostics"), list)
            else [],
        }
    return {
        "reference_artists": list(request.reference_artists),
        "expanded_artists": [],
        "provider_results": [],
        "search_queries": _artist_expansion_search_queries(steps),
        "tool_status": "not_run",
        "diagnostics": [],
    }


def _artist_expansion_search_queries(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for step in steps:
        if step.get("tool") != "search_tracks":
            continue
        arguments = step.get("arguments")
        observation = step.get("observation")
        if not isinstance(arguments, dict):
            continue
        status = ""
        result_count = 0
        diagnostics: list[str] = []
        if isinstance(observation, dict):
            status = str(observation.get("status") or "")
            data = observation.get("data")
            if isinstance(data, dict) and isinstance(data.get("tracks"), list):
                result_count = len(data.get("tracks") or [])
            diagnostics = [
                str(item)
                for item in observation.get("diagnostics", [])
                if isinstance(item, str)
            ] if isinstance(observation.get("diagnostics"), list) else []
        queries.append(
            {
                "query": str(arguments.get("query") or ""),
                "tier": str(arguments.get("search_tier") or ""),
                "anchor_artists": [
                    str(item)
                    for item in arguments.get("anchor_artists", [])
                    if str(item).strip()
                ] if isinstance(arguments.get("anchor_artists"), list) else [],
                "expanded_artists": [
                    str(item)
                    for item in arguments.get("expanded_artists", [])
                    if str(item).strip()
                ] if isinstance(arguments.get("expanded_artists"), list) else [],
                "status": status,
                "result_count": result_count,
                "diagnostics": diagnostics,
            }
        )
    return queries


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
