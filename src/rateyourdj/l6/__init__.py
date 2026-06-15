"""L6 natural-language recommendation orchestration and trajectories."""

from .models import (
    AgentRequest,
    AgentResponse,
    AgentTrajectory,
    agent_schema,
)
from .deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DeepSeekProvider,
    configured_llm_provider,
)
from .parser import GENRE_ALIASES, parse_agent_request
from .provider import (
    AgentDecision,
    AgentTurn,
    LLMProvider,
    LLMProviderError,
    LLMResponseError,
    MockLLMProvider,
)
from .service import AgentLoopError, RecommendationAgentService
from .sessions import AgentSession, JsonSessionStore
from .store import JsonTrajectoryStore, TrajectoryNotFoundError
from .tool_registry import AgentToolRegistry
from .tools import request_recommendations

__all__ = [
    "GENRE_ALIASES",
    "AgentRequest",
    "AgentResponse",
    "AgentTrajectory",
    "AgentDecision",
    "AgentTurn",
    "AgentLoopError",
    "AgentSession",
    "AgentToolRegistry",
    "DEFAULT_DEEPSEEK_BASE_URL",
    "DEFAULT_DEEPSEEK_MODEL",
    "DeepSeekProvider",
    "JsonSessionStore",
    "JsonTrajectoryStore",
    "LLMProvider",
    "LLMProviderError",
    "LLMResponseError",
    "MockLLMProvider",
    "RecommendationAgentService",
    "TrajectoryNotFoundError",
    "agent_schema",
    "configured_llm_provider",
    "parse_agent_request",
    "request_recommendations",
]
