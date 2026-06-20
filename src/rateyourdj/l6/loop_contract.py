from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LOOP_CONTRACT_VERSION = "recommendation_loop_v1"


@dataclass(frozen=True, slots=True)
class LoopPhase:
    name: str
    goal: str
    allowed_tools: tuple[str, ...] = ()
    required: bool = False

    def to_plan_item(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "phase": self.name,
            "goal": self.goal,
            "allowed_tools": list(self.allowed_tools),
            "required": self.required,
        }
        return value


RECOMMENDATION_LOOP_PHASES: tuple[LoopPhase, ...] = (
    LoopPhase(
        name="memory_read",
        goal="read durable and session-scoped user context before scoring",
        allowed_tools=(
            "get_user_memory",
            "get_session_memory",
            "L1.inspect_user_profile",
        ),
        required=True,
    ),
    LoopPhase(
        name="query_understanding",
        goal="parse the request into structured recommendation constraints",
        required=True,
    ),
    LoopPhase(
        name="external_search",
        goal="search external music providers when configured",
        allowed_tools=("get_similar_artists", "discover_tracks", "search_tracks"),
    ),
    LoopPhase(
        name="candidate_enrichment",
        goal="read normalized metadata for candidate tracks when needed",
        allowed_tools=(
            "get_track_metadata",
            "get_artist_profile",
            "L2.inspect_song_profile",
        ),
    ),
    LoopPhase(
        name="candidate_ranking",
        goal="score candidates against memory, request intent, and constraints",
        allowed_tools=("rank_candidates", "L4.rank_candidates"),
        required=True,
    ),
    LoopPhase(
        name="retrieval_diagnostics",
        goal="record candidate retrieval limits when ranking is insufficient",
        allowed_tools=("get_similar_tracks", "L3.retrieve_candidates"),
    ),
    LoopPhase(
        name="explanation",
        goal="generate user-facing recommendation explanations from evidence",
        allowed_tools=("explain_recommendations",),
        required=True,
    ),
    LoopPhase(
        name="trajectory_write",
        goal="persist the run, tool calls, decisions, and recommendations",
        required=True,
    ),
    LoopPhase(
        name="feedback_write",
        goal="record explicit feedback and collection effects after the run",
        allowed_tools=(
            "record_feedback",
            "save_to_collection",
            "propose_memory_update",
            "commit_memory_update",
            "update_session_memory",
        ),
    ),
)

_PHASE_BY_TOOL = {
    tool: phase.name
    for phase in RECOMMENDATION_LOOP_PHASES
    for tool in phase.allowed_tools
}


def recommendation_loop_plan() -> list[dict[str, Any]]:
    return [phase.to_plan_item() for phase in RECOMMENDATION_LOOP_PHASES]


def loop_phase_for_tool(tool_name: str) -> str | None:
    return _PHASE_BY_TOOL.get(tool_name)


def loop_contract_tool_names() -> set[str]:
    return set(_PHASE_BY_TOOL)


def validate_tool_allowed_in_recommendation_loop(tool_name: str) -> None:
    if tool_name not in _PHASE_BY_TOOL:
        raise ValueError(
            "tool is not part of recommendation_loop_v1: " + tool_name
        )
