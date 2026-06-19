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
from .errors import AgentLoopError
from .service import RecommendationAgentService
from .sessions import AgentSession, JsonSessionStore
from .store import JsonTrajectoryStore, TrajectoryNotFoundError
from .agent_tool_registry import AgentToolRegistryV1
from .agent_tool_schemas import AGENT_TOOL_SCHEMAS, agent_tool_schemas
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
    "AgentToolRegistryV1",
    "AGENT_TOOL_SCHEMAS",
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
    "agent_tool_schemas",
    "configured_llm_provider",
    "parse_agent_request",
    "request_recommendations",
]
