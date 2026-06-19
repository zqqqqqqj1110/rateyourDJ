from __future__ import annotations

from typing import Any


AGENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_user_memory",
        "description": (
            "Read durable user music memory, including collection-derived "
            "preferences and aggregate feedback state."
        ),
        "parameters": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_session_memory",
        "description": "Read active short-term recommendation session state.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["user_id", "session_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_tracks",
        "description": (
            "Search music providers for recommendation candidates. This is the "
            "target replacement for local-only candidate lookup."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "market": {"type": "string"},
                "providers": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "spotify",
                            "lastfm",
                            "musicbrainz",
                            "local_cache",
                        ],
                    },
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_track_metadata",
        "description": (
            "Read normalized track metadata, tags, genres, and data quality for "
            "one or more tracks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "track_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 50,
                },
                "queries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "artist": {"type": "string"},
                            "album": {"type": "string"},
                        },
                        "required": ["title", "artist"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 50,
                },
                "include_raw": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_similar_tracks",
        "description": (
            "Retrieve similar candidate tracks from current providers or "
            "transition-period local similarity logic."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "seed_track_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                },
                "seed_artists": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                },
                "seed_genres": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 10,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "market": {"type": "string"},
                "max_per_artist": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
                "min_score": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "rank_candidates",
        "description": (
            "Rank candidate tracks against user memory, session constraints, and "
            "request intent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
                "message": {"type": "string"},
                "candidate_track_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
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
                "constraints": {"type": "object"},
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "explain_recommendations",
        "description": (
            "Turn structured ranking evidence into user-facing recommendation "
            "reasons."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
                "message": {"type": "string"},
                "ranked_tracks": {
                    "type": "array",
                    "items": {"type": "object"},
                    "minItems": 1,
                    "maxItems": 50,
                },
                "style": {
                    "type": "string",
                    "enum": ["short", "balanced", "historical"],
                },
            },
            "required": ["user_id", "message", "ranked_tracks"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_feedback",
        "description": (
            "Record user feedback and return any memory or trajectory effects."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
                "run_id": {"type": "string"},
                "track_id": {"type": "string"},
                "event": {
                    "type": "string",
                    "enum": [
                        "liked",
                        "skipped",
                        "saved",
                        "playlist_add",
                        "request_similar",
                        "hide_artist",
                        "hide_track",
                    ],
                },
                "context": {"type": "object"},
            },
            "required": ["user_id", "track_id", "event"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "save_to_collection",
        "description": "Save a track to the user's explicit collection.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "track_id": {"type": "string"},
                "source": {
                    "type": "string",
                    "enum": ["agent_recommendation", "manual", "import"],
                },
                "run_id": {"type": "string"},
            },
            "required": ["user_id", "track_id", "source"],
            "additionalProperties": False,
        },
    },
]


def agent_tool_schemas() -> list[dict[str, Any]]:
    return [dict(schema) for schema in AGENT_TOOL_SCHEMAS]
