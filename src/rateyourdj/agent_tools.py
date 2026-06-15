from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ToolStatus = Literal["ok", "partial", "empty"]


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """Structured result returned by L1-L5 Agent-facing tools."""

    tool: str
    status: ToolStatus
    data: dict[str, Any]
    diagnostics: list[str] = field(default_factory=list)
    retryable: bool = False
    suggested_actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "data": dict(self.data),
            "diagnostics": list(self.diagnostics),
            "retryable": self.retryable,
            "suggested_actions": [
                dict(action) for action in self.suggested_actions
            ],
        }
