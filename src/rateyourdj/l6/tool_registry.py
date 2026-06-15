from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rateyourdj.agent_tools import ToolObservation
from rateyourdj.l1 import JsonProfileStore, inspect_user_profile
from rateyourdj.l2 import JsonSongStore, inspect_song_profile
from rateyourdj.l3 import retrieve_candidates_tool
from rateyourdj.l4 import rank_candidates_tool
from rateyourdj.l5 import inspect_feedback_state


AgentTool = Callable[..., ToolObservation]

MODEL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "L1.inspect_user_profile",
        "description": (
            "Inspect the current user's collection seeds, long-term preferences, "
            "feedback count, and profile health."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "L2.inspect_song_profile",
        "description": (
            "Inspect one song's metadata, tags, normalized genres, and data quality."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "song_id": {"type": "string"},
            },
            "required": ["song_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "L3.retrieve_candidates",
        "description": (
            "Retrieve songs similar to the user's collection and return retrieval "
            "diagnostics. Use when ranking cannot produce enough eligible songs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 1000},
                "max_per_artist": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
                "min_score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "L4.rank_candidates",
        "description": (
            "Rank recommendation candidates using collection preferences, profile "
            "quality, diversity, and prior feedback."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 1000},
                "candidate_pool_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_per_artist": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
                "min_retrieval_score": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "L5.inspect_feedback_state",
        "description": (
            "Inspect aggregate positive and negative recommendation feedback for "
            "the current user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
]


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, name: str, tool: AgentTool) -> None:
        self._tools[name] = tool

    def call(self, name: str, **arguments: Any) -> ToolObservation:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ValueError(f"unknown agent tool: {name}") from exc
        return tool(**arguments)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def model_schemas(self) -> list[dict[str, Any]]:
        available = set(self.names())
        return [
            dict(schema)
            for schema in MODEL_TOOL_SCHEMAS
            if schema["name"] in available
        ]

    @classmethod
    def default(
        cls,
        profile_store: JsonProfileStore,
        song_store: JsonSongStore,
    ) -> AgentToolRegistry:
        registry = cls()
        registry.register(
            "L1.inspect_user_profile",
            lambda **arguments: inspect_user_profile(
                data_dir=profile_store.root,
                **arguments,
            ),
        )
        registry.register(
            "L2.inspect_song_profile",
            lambda **arguments: inspect_song_profile(
                data_dir=song_store.root,
                **arguments,
            ),
        )
        registry.register(
            "L3.retrieve_candidates",
            lambda **arguments: retrieve_candidates_tool(
                profile_dir=profile_store.root,
                song_dir=song_store.root,
                **arguments,
            ),
        )
        registry.register(
            "L4.rank_candidates",
            lambda **arguments: rank_candidates_tool(
                profile_dir=profile_store.root,
                song_dir=song_store.root,
                **arguments,
            ),
        )
        registry.register(
            "L5.inspect_feedback_state",
            lambda **arguments: inspect_feedback_state(
                profile_dir=profile_store.root,
                song_dir=song_store.root,
                **arguments,
            ),
        )
        return registry
