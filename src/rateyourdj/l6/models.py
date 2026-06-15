from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class AgentRequest:
    query: str
    top_k: int = 10
    max_per_artist: int = 2
    min_retrieval_score: float = 0.0
    preference_terms: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    intent: str = "recommend"
    exclude_seen: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "top_k": self.top_k,
            "max_per_artist": self.max_per_artist,
            "min_retrieval_score": self.min_retrieval_score,
            "preference_terms": list(self.preference_terms),
            "exclude_terms": list(self.exclude_terms),
            "intent": self.intent,
            "exclude_seen": self.exclude_seen,
        }


@dataclass(frozen=True, slots=True)
class AgentResponse:
    trajectory_id: str
    session_id: str
    user_id: str
    query: str
    parsed_request: AgentRequest
    message: str
    ranked_songs: list[dict[str, Any]]
    seed_song_ids: list[str]
    missing_seed_song_ids: list[str]
    stop_reason: str
    attempts: int
    tool_calls: list[dict[str, Any]]
    agent_mode: str = "rules"
    provider: str | None = None
    fallback_reason: str | None = None
    agent_decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "query": self.query,
            "parsed_request": self.parsed_request.to_dict(),
            "message": self.message,
            "ranked_songs": [dict(song) for song in self.ranked_songs],
            "seed_song_ids": list(self.seed_song_ids),
            "missing_seed_song_ids": list(self.missing_seed_song_ids),
            "stop_reason": self.stop_reason,
            "attempts": self.attempts,
            "tool_calls": [dict(call) for call in self.tool_calls],
            "agent_mode": self.agent_mode,
            "provider": self.provider,
            "fallback_reason": self.fallback_reason,
            "agent_decisions": [
                dict(decision) for decision in self.agent_decisions
            ],
        }


@dataclass(frozen=True, slots=True)
class AgentTrajectory:
    trajectory_id: str
    user_id: str
    query: str
    parsed_request: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    response_text: str
    feedback_events: list[dict[str, Any]] = field(default_factory=list)
    session_id: str | None = None
    turn_index: int = 1
    plan: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "unknown"
    agent_mode: str = "rules"
    provider: str | None = None
    fallback_reason: str | None = None
    agent_decisions: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "user_id": self.user_id,
            "query": self.query,
            "parsed_request": dict(self.parsed_request),
            "plan": [dict(item) for item in self.plan],
            "tool_calls": [dict(call) for call in self.tool_calls],
            "recommendations": [
                dict(recommendation)
                for recommendation in self.recommendations
            ],
            "response_text": self.response_text,
            "feedback_events": [
                dict(feedback) for feedback in self.feedback_events
            ],
            "stop_reason": self.stop_reason,
            "agent_mode": self.agent_mode,
            "provider": self.provider,
            "fallback_reason": self.fallback_reason,
            "agent_decisions": [
                dict(decision) for decision in self.agent_decisions
            ],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentTrajectory:
        required = {
            "trajectory_id",
            "user_id",
            "query",
            "parsed_request",
            "tool_calls",
            "recommendations",
            "response_text",
            "created_at",
        }
        missing = sorted(required - set(value))
        optional = {
            "feedback_events",
            "session_id",
            "turn_index",
            "plan",
            "stop_reason",
            "agent_mode",
            "provider",
            "fallback_reason",
            "agent_decisions",
        }
        unknown = sorted(set(value) - required - optional)
        if missing:
            raise ValueError(
                "trajectory is missing fields: " + ", ".join(missing)
            )
        if unknown:
            raise ValueError(
                "trajectory contains unknown fields: " + ", ".join(unknown)
            )
        return cls(
            trajectory_id=str(value["trajectory_id"]),
            session_id=(
                str(value["session_id"])
                if value.get("session_id") is not None
                else None
            ),
            turn_index=int(value.get("turn_index", 1)),
            user_id=str(value["user_id"]),
            query=str(value["query"]),
            parsed_request=dict(value["parsed_request"]),
            plan=[dict(item) for item in value.get("plan", [])],
            tool_calls=[dict(call) for call in value["tool_calls"]],
            recommendations=[
                dict(item) for item in value["recommendations"]
            ],
            response_text=str(value["response_text"]),
            feedback_events=[
                dict(item) for item in value.get("feedback_events", [])
            ],
            stop_reason=str(value.get("stop_reason", "unknown")),
            agent_mode=str(value.get("agent_mode", "rules")),
            provider=(
                str(value["provider"])
                if value.get("provider") is not None
                else None
            ),
            fallback_reason=(
                str(value["fallback_reason"])
                if value.get("fallback_reason") is not None
                else None
            ),
            agent_decisions=[
                dict(item) for item in value.get("agent_decisions", [])
            ],
            created_at=str(value["created_at"]),
        )


def agent_schema() -> dict[str, Any]:
    return {
        "query": "non-empty natural-language recommendation request",
        "parsed_request": {
            "top_k": "integer between 1 and 50",
            "max_per_artist": "integer between 1 and 10",
            "min_retrieval_score": "number between 0 and 1",
            "preference_terms": ["normalized genre or free-text term"],
            "exclude_terms": ["normalized excluded term"],
            "intent": "recommend or more",
            "exclude_seen": "whether to omit songs already returned in this session",
        },
        "trajectory_id": "request UUID used to link later feedback",
        "session_id": "conversation UUID used to preserve multi-turn state",
        "message": "traceable recommendation explanation",
        "ranked_songs": "L4 ranked songs after query-level filters",
        "stop_reason": "why the execution loop stopped",
    }
