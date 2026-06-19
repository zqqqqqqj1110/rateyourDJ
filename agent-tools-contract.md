# rateyourDJ Agent Tools Contract

This document defines the target tool schema for the DJ Agent refactor. The goal is to replace internal L1-L7 tool names with stable agent-facing tools.

## Tool Design Rules

- Tool names describe product capabilities, not internal layers.
- Every tool has a strict JSON schema.
- Tools must be scoped by `user_id` when they touch user data.
- Tools return structured observations, not free-form text.
- Tools should expose evidence that can be used for ranking and explanations.
- External provider tools should hide provider-specific API details behind normalized fields.
- Write tools should return memory or collection effects.

## Standard Observation Envelope

All tools should return this shape:

```json
{
  "tool": "search_tracks",
  "status": "ok",
  "data": {},
  "diagnostics": [],
  "retryable": false,
  "suggested_actions": []
}
```

Status values:

```text
ok       tool completed and returned usable data
partial  tool completed but result is incomplete
empty    tool completed but found no useful data
error    tool failed in a recoverable or reportable way
```

Suggested action shape:

```json
{
  "tool": "search_tracks",
  "reason": "not enough candidates",
  "arguments": {
    "limit": 50
  }
}
```

## Core Tools

```text
get_user_memory
get_session_memory
update_session_memory
propose_memory_update
commit_memory_update
search_tracks
get_track_metadata
get_artist_profile
get_similar_tracks
rank_candidates
explain_recommendations
record_feedback
save_to_collection
```

## 1. get_user_memory

Reads long-term user memory.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" }
  },
  "required": ["user_id"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "user_id": "demo-user",
  "preferred_artists": [],
  "preferred_genres": [],
  "preferred_tags": [],
  "negative_preferences": [],
  "feedback_summary": {
    "liked_count": 0,
    "skipped_count": 0,
    "saved_count": 0
  },
  "explanation_preferences": {}
}
```

## 2. get_session_memory

Reads active session state.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "session_id": { "type": "string" }
  },
  "required": ["user_id", "session_id"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "session_id": "session_123",
  "user_id": "demo-user",
  "turn_count": 3,
  "current_intent": "recommend",
  "active_constraints": {},
  "preference_terms": [],
  "seen_track_ids": [],
  "last_run_id": "run_123"
}
```

## 3. update_session_memory

Updates short-term session state. This must not write long-term memory.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "session_id": { "type": "string" },
    "patch": {
      "type": "object",
      "properties": {
        "current_intent": { "type": "string" },
        "active_constraints": { "type": "object" },
        "preference_terms": {
          "type": "array",
          "items": { "type": "string" }
        },
        "seen_track_ids": {
          "type": "array",
          "items": { "type": "string" }
        },
        "last_run_id": { "type": "string" }
      },
      "additionalProperties": false
    }
  },
  "required": ["user_id", "session_id", "patch"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "session_id": "session_123",
  "updated_fields": ["seen_track_ids"],
  "memory_updates": [
    {
      "scope": "session",
      "type": "seen_tracks",
      "summary": "Added 10 tracks to the session seen list."
    }
  ]
}
```

## 4. propose_memory_update

Creates a proposed long-term memory update. It does not commit the change.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "source": {
      "type": "string",
      "enum": ["user_statement", "feedback_pattern", "collection_import"]
    },
    "proposal": {
      "type": "object",
      "properties": {
        "field": { "type": "string" },
        "value": { "type": "string" },
        "delta": { "type": "number" },
        "confidence": { "type": "number" },
        "reason": { "type": "string" }
      },
      "required": ["field", "value", "confidence", "reason"],
      "additionalProperties": false
    }
  },
  "required": ["user_id", "source", "proposal"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "proposal_id": "memory_proposal_123",
  "accepted_by_policy": true,
  "requires_user_confirmation": false,
  "reason": "Repeated positive feedback supports a durable preference."
}
```

## 5. commit_memory_update

Commits a durable long-term memory update after policy validation.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "proposal_id": { "type": "string" },
    "run_id": { "type": "string" }
  },
  "required": ["user_id", "proposal_id", "run_id"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "user_id": "demo-user",
  "committed": true,
  "memory_updates": [
    {
      "scope": "long_term",
      "field": "preferred_genres.progressive rock",
      "delta": 0.03,
      "summary": "Increased preference for progressive rock."
    }
  ]
}
```

## 6. search_tracks

Searches external music providers for candidate tracks.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "query": { "type": "string" },
    "limit": { "type": "integer", "minimum": 1, "maximum": 50 },
    "market": { "type": "string" },
    "providers": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": ["spotify", "lastfm", "musicbrainz", "local_cache"]
      }
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "query": "british rock similar to oasis",
  "provider_results": [
    {
      "provider": "spotify",
      "result_count": 25,
      "cache_hit": false
    }
  ],
  "tracks": [
    {
      "track_id": "spotify:track:...",
      "provider": "spotify",
      "title": "Song Title",
      "artist": "Artist Name",
      "album": "Album Name",
      "release_year": 1995,
      "image_url": "https://...",
      "preview_url": null,
      "external_urls": {
        "spotify": "https://open.spotify.com/track/..."
      }
    }
  ]
}
```

## 7. get_track_metadata

Fetches or reads normalized metadata for one or more tracks.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "track_ids": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1,
      "maxItems": 50
    },
    "include_raw": { "type": "boolean" }
  },
  "required": ["track_ids"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "tracks": [
    {
      "track_id": "spotify:track:...",
      "title": "Song Title",
      "artist": "Artist Name",
      "album": "Album Name",
      "release_year": 1995,
      "duration_ms": 230000,
      "genres": {},
      "tags": {},
      "provider": "spotify",
      "cache_hit": true
    }
  ],
  "missing_track_ids": []
}
```

## 8. get_artist_profile

Fetches artist-level context for ranking and explanation.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "artist_ids": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1,
      "maxItems": 25
    },
    "artist_names": {
      "type": "array",
      "items": { "type": "string" },
      "maxItems": 25
    }
  },
  "additionalProperties": false
}
```

At least one of `artist_ids` or `artist_names` is required.

### Observation Data

```json
{
  "artists": [
    {
      "artist_id": "spotify:artist:...",
      "name": "Artist Name",
      "genres": ["britpop"],
      "tags": {},
      "historical_context": [
        "associated with 1990s Britpop"
      ],
      "provider": "spotify"
    }
  ],
  "missing": []
}
```

## 9. get_similar_tracks

Gets similar tracks from providers or local similarity logic.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "seed_track_ids": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1,
      "maxItems": 10
    },
    "seed_artists": {
      "type": "array",
      "items": { "type": "string" },
      "maxItems": 10
    },
    "seed_genres": {
      "type": "array",
      "items": { "type": "string" },
      "maxItems": 10
    },
    "limit": { "type": "integer", "minimum": 1, "maximum": 50 },
    "market": { "type": "string" }
  },
  "additionalProperties": false
}
```

At least one seed field is required.

### Observation Data

```json
{
  "seeds": {
    "track_ids": ["spotify:track:..."],
    "artists": [],
    "genres": ["british rock"]
  },
  "tracks": [],
  "provider_results": []
}
```

## 10. rank_candidates

Ranks candidate tracks against user memory, session constraints, and request intent.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "session_id": { "type": "string" },
    "message": { "type": "string" },
    "candidate_track_ids": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1,
      "maxItems": 200
    },
    "limit": { "type": "integer", "minimum": 1, "maximum": 50 },
    "constraints": { "type": "object" }
  },
  "required": ["user_id", "message", "candidate_track_ids"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "ranked_tracks": [
    {
      "track_id": "spotify:track:...",
      "rank": 1,
      "score": 0.87,
      "evidence": {
        "matched_preferences": ["british rock"],
        "matched_request_terms": ["英伦摇滚"],
        "similar_collection_items": [],
        "feedback_signals": [],
        "diversity_reason": "different artist from previous result"
      }
    }
  ],
  "filtered_out": [
    {
      "track_id": "spotify:track:...",
      "reason": "excluded_artist"
    }
  ]
}
```

## 11. explain_recommendations

Turns structured ranking evidence into user-facing recommendation reasons.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "session_id": { "type": "string" },
    "message": { "type": "string" },
    "ranked_tracks": {
      "type": "array",
      "items": { "type": "object" },
      "minItems": 1,
      "maxItems": 50
    },
    "style": {
      "type": "string",
      "enum": ["short", "balanced", "historical"]
    }
  },
  "required": ["user_id", "message", "ranked_tracks"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "recommendations": [
    {
      "track_id": "spotify:track:...",
      "reasons": [
        {
          "type": "session_intent",
          "label": "符合本次请求",
          "text": "这首歌符合你这次想听的英伦摇滚方向。"
        },
        {
          "type": "historical_context",
          "label": "音乐背景",
          "text": "它和 1990s Britpop 的吉他流行传统有关。"
        }
      ]
    }
  ]
}
```

Rules:

- Reasons must be based on provided evidence or metadata.
- If evidence is weak, say so indirectly by using a simpler reason.
- Do not invent exact historical facts that were not supplied by metadata or provider context.

## 12. record_feedback

Records feedback and returns memory effects.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "session_id": { "type": "string" },
    "run_id": { "type": "string" },
    "track_id": { "type": "string" },
    "event": {
      "type": "string",
      "enum": [
        "liked",
        "skipped",
        "saved",
        "playlist_add",
        "request_similar",
        "hide_artist",
        "hide_track"
      ]
    },
    "context": { "type": "object" }
  },
  "required": ["user_id", "track_id", "event"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "feedback_id": "feedback_123",
  "event": "liked",
  "memory_effects": [
    {
      "scope": "long_term",
      "field": "preferred_genres.british rock",
      "delta": 0.03,
      "summary": "Increased preference for british rock."
    }
  ]
}
```

## 13. save_to_collection

Adds a track to the user's explicit collection.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "user_id": { "type": "string" },
    "track_id": { "type": "string" },
    "source": {
      "type": "string",
      "enum": ["agent_recommendation", "manual", "import"]
    },
    "run_id": { "type": "string" }
  },
  "required": ["user_id", "track_id", "source"],
  "additionalProperties": false
}
```

### Observation Data

```json
{
  "user_id": "demo-user",
  "track_id": "spotify:track:...",
  "saved": true,
  "collection_count": 43,
  "memory_effects": [
    {
      "scope": "collection",
      "type": "saved_track",
      "summary": "Saved track to user collection."
    }
  ]
}
```

## Recommended Tool Flow

For a normal recommendation turn:

```text
get_user_memory
get_session_memory
search_tracks and/or get_similar_tracks
get_track_metadata
get_artist_profile when explanation needs context
rank_candidates
explain_recommendations
update_session_memory
```

For feedback:

```text
record_feedback
propose_memory_update
commit_memory_update when policy allows
save_to_collection when event is saved or playlist_add
```

## Migration From Existing Tools

| Existing Tool | Target Tool |
| --- | --- |
| `L1.inspect_user_profile` | `get_user_memory` |
| `L2.inspect_song_profile` | `get_track_metadata` |
| `L3.retrieve_candidates` | `get_similar_tracks` or `search_tracks` |
| `L4.rank_candidates` | `rank_candidates` |
| `L5.inspect_feedback_state` | `get_user_memory` / `record_feedback` |
| `L5.record_feedback_tool` | `record_feedback` |

During migration, wrappers can expose the new names while calling the old implementation internally.

## Implementation Order

1. Add schemas as constants in the future `agent/tools.py` or `agent/schemas.py`.
2. Add wrapper tools with new names that call existing L1-L5 implementations.
3. Update agent prompts/providers to see only new tool names.
4. Add provider-backed implementations for `search_tracks`, `get_track_metadata`, and `get_similar_tracks`.
5. Replace local-only ranking with candidate IDs from external provider tools.
6. Move feedback writes to the new memory/event model.
