from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


AgentDecisionKind = Literal["tool", "update", "finish"]


class LLMProviderError(RuntimeError):
    """Raised when a configured model provider cannot produce a decision."""


class LLMResponseError(LLMProviderError):
    """Raised when a provider response is reachable but structurally invalid."""


@dataclass(frozen=True, slots=True)
class AgentDecision:
    kind: AgentDecisionKind
    summary: str
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    request_patch: dict[str, Any] = field(default_factory=dict)
    response_text: str | None = None
    thought: str = ""

    def __post_init__(self) -> None:
        if self.kind == "tool" and not self.tool_name:
            raise ValueError("tool decisions require tool_name")
        if self.kind in {"update", "finish"} and self.tool_name is not None:
            raise ValueError(f"{self.kind} decisions cannot include tool_name")
        if self.kind == "update" and not self.request_patch:
            raise ValueError("update decisions require request_patch")
        if not self.summary.strip():
            raise ValueError("decision summary must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "kind": self.kind,
            "summary": self.summary,
            "request_patch": dict(self.request_patch),
        }
        if self.thought:
            value["thought"] = self.thought
        if self.tool_name is not None:
            value["tool_name"] = self.tool_name
            value["arguments"] = dict(self.arguments)
        if self.response_text is not None:
            value["response_text"] = self.response_text
        return value


@dataclass(frozen=True, slots=True)
class AgentTurn:
    user_id: str
    query: str
    request: dict[str, Any]
    session: dict[str, Any]
    tool_schemas: list[dict[str, Any]]
    tool_history: list[dict[str, Any]]
    validation_feedback: list[str]
    remaining_steps: int


class LLMProvider(Protocol):
    @property
    def name(self) -> str:
        ...

    def next_decision(self, turn: AgentTurn) -> AgentDecision:
        ...


class MockLLMProvider:
    """Deterministic provider used by tests without network or API costs."""

    def __init__(
        self,
        decisions: list[AgentDecision | BaseException],
        *,
        name: str = "mock",
    ) -> None:
        self._decisions = list(decisions)
        self._name = name
        self.turns: list[AgentTurn] = []

    @property
    def name(self) -> str:
        return self._name

    def next_decision(self, turn: AgentTurn) -> AgentDecision:
        self.turns.append(turn)
        if not self._decisions:
            raise LLMProviderError("mock provider has no remaining decisions")
        decision = self._decisions.pop(0)
        if isinstance(decision, BaseException):
            raise decision
        return decision
