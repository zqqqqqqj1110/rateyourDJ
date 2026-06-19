from __future__ import annotations

from typing import Any, Protocol

from .models import AgentRequest
from .runtime import record_step, select_suggested_action
from .sessions import AgentSession
from .loop_contract import LOOP_CONTRACT_VERSION, loop_phase_for_tool


class ToolRegistry(Protocol):
    def call(self, name: str, **arguments: Any) -> Any:
        ...

    def names(self) -> list[str]:
        ...


class QueryFilter(Protocol):
    def __call__(
        self,
        ranked_songs: list[dict[str, Any]],
        request: AgentRequest,
        *,
        session: AgentSession,
    ) -> list[dict[str, Any]]:
        ...


def guarded_model_ranking(
    *,
    user_id: str,
    request: AgentRequest,
    session: AgentSession,
    steps: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    apply_query_filters: QueryFilter,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    pool_size = min(max(request.top_k * 5, request.top_k), 1000)
    arguments = {
        "user_id": user_id,
        "top_k": pool_size,
        "candidate_pool_size": pool_size,
        "max_per_artist": request.max_per_artist,
        "min_retrieval_score": request.min_retrieval_score,
    }
    observation = tool_registry.call("L4.rank_candidates", **arguments)
    steps.append(
        {
            "step": len(steps) + 1,
            "tool": observation.tool,
            "loop_contract": LOOP_CONTRACT_VERSION,
            "loop_phase": loop_phase_for_tool(observation.tool),
            "arguments": dict(arguments),
            "observation": observation.to_dict(),
            "decision": (
                "execute validated L4 fallback because the model did not rank"
            ),
            "decision_source": "program_guard",
        }
    )
    eligible = apply_query_filters(
        [dict(song) for song in observation.data.get("ranked_songs", [])],
        request,
        session=session,
    )
    return (
        eligible,
        [str(value) for value in observation.data.get("seed_song_ids", [])],
        [
            str(value)
            for value in observation.data.get("missing_seed_song_ids", [])
        ],
    )


def execute_ranking_loop(
    *,
    user_id: str,
    request: AgentRequest,
    session: AgentSession,
    steps: list[dict[str, Any]],
    max_steps: int,
    tool_registry: ToolRegistry,
    apply_query_filters: QueryFilter,
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
        observation = tool_registry.call("L4.rank_candidates", **arguments)
        data = observation.data
        seed_song_ids = [str(value) for value in data.get("seed_song_ids", [])]
        missing_seed_song_ids = [
            str(value) for value in data.get("missing_seed_song_ids", [])
        ]
        filtered = apply_query_filters(
            [dict(song) for song in data.get("ranked_songs", [])],
            request,
            session=session,
        )
        if len(filtered) > len(best):
            best = filtered

        if len(filtered) >= request.top_k:
            record_step(
                steps,
                arguments,
                observation,
                f"goal satisfied with {len(filtered)} eligible songs",
            )
            return (
                filtered[: request.top_k],
                seed_song_ids,
                missing_seed_song_ids,
                rank_attempts,
                "goal_satisfied",
            )

        selected_action = select_suggested_action(
            observation,
            arguments,
            request,
            allowed_tools={"L4.rank_candidates"} & set(tool_registry.names()),
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
        record_step(
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
        observation = tool_registry.call("L3.retrieve_candidates", **arguments)
        record_step(
            steps,
            arguments,
            observation,
            "stop after recording the limiting retrieval diagnostics",
        )

    return (
        best[: request.top_k],
        seed_song_ids,
        missing_seed_song_ids,
        rank_attempts,
        "insufficient_candidates",
    )
