# rateyourDJ Agent API Contract

This document defines the target API contract for the next refactor. The goal is to move the product API away from internal L1-L7 concepts and toward a stable DJ Agent interface.

## Design Goals

- Expose agent capabilities, not internal layers.
- Treat local data as memory, cache, collection, and trajectory, not as the only recommendation source.
- Keep `trace` optional and developer-facing.
- Make feedback and memory updates explicit.
- Support future external music providers without changing the frontend contract.

## API Version

Initial target version:

```text
/api/v1
```

Existing endpoints can remain during migration, but new UI work should target `/api/v1`.

## Core Endpoints

```text
POST   /api/v1/agent/recommend
POST   /api/v1/agent/feedback
GET    /api/v1/agent/session/:session_id
GET    /api/v1/profile/:user_id
GET    /api/v1/collection/:user_id
POST   /api/v1/collection/:user_id
DELETE /api/v1/collection/:user_id/:track_id
GET    /api/v1/agent/status
```

## 1. Recommend

```text
POST /api/v1/agent/recommend
```

Runs one DJ Agent recommendation turn.

### Request

```json
{
  "user_id": "demo-user",
  "message": "有没有和绿洲差不多的英伦摇滚，但不要太慢",
  "session_id": "optional-session-id",
  "constraints": {
    "limit": 10,
    "exclude_seen": true,
    "max_per_artist": 2,
    "exclude_artists": ["Oasis"],
    "exclude_tracks": [],
    "market": "AU"
  },
  "mode": "auto",
  "include_trace": false
}
```

### Request Fields

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `user_id` | string | yes | Current user scope. The agent must not access another user's memory. |
| `message` | string | yes | Natural-language DJ request. |
| `session_id` | string/null | no | If omitted, the server creates a new session. |
| `constraints.limit` | integer | no | Default 10, allowed 1-50. |
| `constraints.exclude_seen` | boolean | no | Avoid tracks already shown in the session. |
| `constraints.max_per_artist` | integer | no | Default 2, allowed 1-10. |
| `constraints.exclude_artists` | string[] | no | Hard exclusions for this request/session. |
| `constraints.exclude_tracks` | string[] | no | Track IDs to exclude. |
| `constraints.market` | string/null | no | Optional provider market, for example `AU` or `US`. |
| `mode` | string | no | `auto`, `model`, or `rules`. |
| `include_trace` | boolean | no | Include developer trace in response. |

### Response

```json
{
  "run_id": "run_123",
  "session_id": "session_123",
  "user_id": "demo-user",
  "message": "我按英伦摇滚方向挑了一组更有吉他旋律感的歌，并避开了太慢的选择。",
  "recommendations": [
    {
      "rank": 1,
      "track": {
        "track_id": "spotify:track:...",
        "title": "Song Title",
        "artist": "Artist Name",
        "album": "Album Name",
        "release_year": 1995,
        "duration_ms": 230000,
        "external_urls": {
          "spotify": "https://open.spotify.com/track/..."
        },
        "preview_url": null,
        "image_url": "https://..."
      },
      "score": 0.87,
      "evidence": {
        "matched_preferences": ["british rock", "guitar-led", "1990s"],
        "similar_collection_items": [
          {
            "track_id": "local-or-provider-id",
            "title": "Wonderwall",
            "artist": "Oasis"
          }
        ],
        "feedback_signals": ["liked similar guitar-led tracks"],
        "historical_context": ["connected to 1990s Britpop"],
        "diversity_reason": "adds a different artist while staying in the requested style"
      },
      "reasons": [
        {
          "type": "session_intent",
          "label": "符合本次请求",
          "text": "这首歌保留了英伦摇滚的吉他旋律感，同时节奏不算太慢。"
        },
        {
          "type": "listening_history",
          "label": "基于你的历史收藏",
          "text": "它和你收藏中过的 90s 吉他摇滚方向接近。"
        }
      ],
      "actions": {
        "can_like": true,
        "can_skip": true,
        "can_save": true,
        "can_request_similar": true
      }
    }
  ],
  "memory_updates": [
    {
      "scope": "session",
      "type": "constraint",
      "summary": "Temporarily avoid slow tracks in this session."
    }
  ],
  "trace": null
}
```

### Response Fields

| Field | Type | Notes |
| --- | --- | --- |
| `run_id` | string | Unique recommendation run ID. Replaces user-facing trajectory language. |
| `session_id` | string | Current conversation/session. |
| `message` | string | User-facing DJ response. |
| `recommendations` | object[] | Ranked recommendations. |
| `recommendations[].track` | object | Provider-agnostic track payload. |
| `recommendations[].evidence` | object | Structured evidence used for ranking and explanation. |
| `recommendations[].reasons` | object[] | Human-readable explanation cards. |
| `memory_updates` | object[] | Summary of session or long-term memory changes. |
| `trace` | object/null | Developer trace, returned only when `include_trace=true`. |

## 2. Feedback

```text
POST /api/v1/agent/feedback
```

Records user feedback and updates memory according to explicit rules.

### Request

```json
{
  "user_id": "demo-user",
  "session_id": "session_123",
  "run_id": "run_123",
  "track_id": "spotify:track:...",
  "event": "liked",
  "context": {
    "rank": 1,
    "reason_type": "session_intent"
  }
}
```

### Events

```text
liked
skipped
saved
playlist_add
request_similar
hide_artist
hide_track
```

### Response

```json
{
  "feedback_id": "feedback_123",
  "user_id": "demo-user",
  "session_id": "session_123",
  "track_id": "spotify:track:...",
  "event": "liked",
  "memory_updates": [
    {
      "scope": "long_term",
      "type": "positive_signal",
      "summary": "Increased preference weight for british rock."
    }
  ]
}
```

## 3. Session

```text
GET /api/v1/agent/session/:session_id
```

Returns current session state for UI restoration and debugging.

### Response

```json
{
  "session_id": "session_123",
  "user_id": "demo-user",
  "turn_count": 3,
  "current_intent": "recommend",
  "constraints": {
    "exclude_seen": true,
    "temporary_exclusions": ["slow tracks"]
  },
  "seen_track_ids": ["spotify:track:..."],
  "last_run_id": "run_123"
}
```

## 4. Profile

```text
GET /api/v1/profile/:user_id
```

Returns user-facing preference memory summary.

### Response

```json
{
  "user_id": "demo-user",
  "collection_count": 42,
  "feedback_count": 18,
  "top_artists": [],
  "top_genres": [],
  "top_tags": [],
  "negative_preferences": [],
  "explanation_preferences": {}
}
```

## 5. Collection

```text
GET /api/v1/collection/:user_id
POST /api/v1/collection/:user_id
DELETE /api/v1/collection/:user_id/:track_id
```

Collection is explicit user-owned memory. It is not the full recommendation candidate database.

### Add Request

```json
{
  "track_id": "spotify:track:...",
  "source": "agent_recommendation",
  "run_id": "run_123"
}
```

### Collection Item

```json
{
  "track_id": "spotify:track:...",
  "title": "Song Title",
  "artist": "Artist Name",
  "album": "Album Name",
  "image_url": "https://...",
  "added_at": "...",
  "added_via": "agent_recommendation"
}
```

## 6. Agent Status

```text
GET /api/v1/agent/status
```

Returns backend capability status.

### Response

```json
{
  "agent_mode": "auto",
  "model_enabled": true,
  "provider": "deepseek:deepseek-v4-flash",
  "music_providers": {
    "spotify": true,
    "lastfm": true,
    "musicbrainz": true
  }
}
```

## Error Shape

All v1 endpoints should use a consistent error envelope:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "message is required",
    "details": {}
  }
}
```

Common codes:

```text
invalid_request
not_found
provider_unavailable
agent_failed
permission_denied
rate_limited
```

## Migration Notes

Current endpoints can map to the new contract during migration:

| Current Endpoint | Target Endpoint |
| --- | --- |
| `POST /api/chat/<user_id>` | `POST /api/v1/agent/recommend` |
| `POST /api/feedback/<user_id>` | `POST /api/v1/agent/feedback` |
| `GET /api/profile/<user_id>` | `GET /api/v1/profile/:user_id` |
| `GET /api/collection/<user_id>` | `GET /api/v1/collection/:user_id` |
| `GET /api/agent-status` | `GET /api/v1/agent/status` |

The frontend should migrate to `/api/v1` first. Internal modules can still call existing services until the agent runtime and provider adapters are split.
