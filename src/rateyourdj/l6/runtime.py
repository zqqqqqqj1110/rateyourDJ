from __future__ import annotations

from typing import Any

from rateyourdj.agent_tools import ToolObservation

from .models import AgentRequest


def record_step(
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


def select_suggested_action(
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
    resolved = validated_ranking_arguments(current_arguments, patch, request)
    if resolved is None or resolved == current_arguments:
        return None
    return {
        "tool": selected_tool,
        "arguments": patch,
        "resolved_arguments": resolved,
        "reasons": reasons,
    }


def validated_ranking_arguments(
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
        or not isinstance(resolved.get("min_retrieval_score"), (int, float))
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
