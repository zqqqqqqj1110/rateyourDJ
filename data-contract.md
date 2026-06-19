# rateyourDJ Data Contract

This document defines the target data structures for the agent refactor. The goal is to make local data serve four clear roles:

```text
memory: what the agent knows about the user
cache: reusable external music provider results
trajectory: what happened during each agent run
collection: tracks the user explicitly saved
```

Local storage should no longer be treated as the only recommendation candidate database.

## Target Data Layout

```text
data/
  memory/
    users/
      <user_id>.json
    sessions/
      <session_id>.json
  cache/
    tracks/
      <provider>/<track_id>.json
    artists/
      <provider>/<artist_id>.json
    albums/
      <provider>/<album_id>.json
    searches/
      <cache_key>.json
  trajectories/
    <user_id>/
      <run_id>.json
  collection/
    <user_id>.json
```

Existing `data/user_profiles`, `data/sessions`, `data/trajectories`, and `data/song_profiles` can remain during migration. New code should target the layout above.

## 1. User Memory

User memory stores long-term understanding about a user's taste. It should only contain durable preferences, not temporary requests.

Path:

```text
data/memory/users/<user_id>.json
```

Schema:

```json
{
  "schema_version": 1,
  "user_id": "demo-user",
  "long_term": {
    "preferred_artists": {
      "Pink Floyd": {
        "weight": 0.92,
        "evidence_count": 6,
        "last_seen_at": "2026-06-16T00:00:00Z"
      }
    },
    "preferred_genres": {
      "progressive rock": {
        "weight": 0.87,
        "evidence_count": 12,
        "last_seen_at": "2026-06-16T00:00:00Z"
      }
    },
    "preferred_tags": {},
    "preferred_eras": {},
    "preferred_moods": {},
    "negative_preferences": {
      "artists": {},
      "genres": {},
      "tags": {}
    },
    "explanation_preferences": {
      "prefers_historical_context": false,
      "prefers_similarity_reasoning": true,
      "prefers_short_explanations": false
    }
  },
  "feedback_summary": {
    "liked_count": 0,
    "skipped_count": 0,
    "saved_count": 0,
    "last_feedback_at": null
  },
  "updated_at": "2026-06-16T00:00:00Z"
}
```

Rules:

- Long-term memory is updated only from explicit user statements or repeated behavioral evidence.
- One skip should not become a permanent negative preference.
- Likes and saves are positive signals, but should still be weighted by confidence and repetition.
- Temporary phrases such as "today", "this time", or "now" should not be written to long-term memory.
- Every durable memory update should be referenced by a feedback event or trajectory ID.

## 2. Session Memory

Session memory stores short-term conversation state and temporary constraints.

Path:

```text
data/memory/sessions/<session_id>.json
```

Schema:

```json
{
  "schema_version": 1,
  "session_id": "session_123",
  "user_id": "demo-user",
  "turn_count": 3,
  "current_intent": "recommend",
  "active_constraints": {
    "limit": 10,
    "exclude_seen": true,
    "max_per_artist": 2,
    "temporary_exclusions": [
      {
        "type": "artist",
        "value": "Pink Floyd",
        "source": "user_request",
        "created_at": "2026-06-16T00:00:00Z"
      }
    ],
    "market": "AU"
  },
  "preference_terms": ["british rock"],
  "seen_track_ids": ["spotify:track:..."],
  "last_run_id": "run_123",
  "last_request": {
    "message": "换一批，不要刚才推荐过的",
    "parsed_at": "2026-06-16T00:00:00Z"
  },
  "created_at": "2026-06-16T00:00:00Z",
  "updated_at": "2026-06-16T00:00:00Z"
}
```

Rules:

- `seen_track_ids` is used to support "换一批" and `exclude_seen`.
- Temporary exclusions expire with the session unless the user explicitly makes them permanent.
- Session memory should not directly mutate long-term memory.
- Session ownership must be enforced by `user_id`.

## 3. Feedback Events

Feedback events should be append-only records. They can update user memory, collection, and trajectories, but the raw event should remain auditable.

Feedback can be stored in a future event log:

```text
data/memory/feedback/<user_id>.jsonl
```

Initial implementations may keep feedback inside user memory or profile files, but the target model should be event-based.

Event schema:

```json
{
  "schema_version": 1,
  "feedback_id": "feedback_123",
  "user_id": "demo-user",
  "session_id": "session_123",
  "run_id": "run_123",
  "track_id": "spotify:track:...",
  "event": "liked",
  "context": {
    "rank": 1,
    "reason_type": "session_intent",
    "provider": "spotify"
  },
  "memory_effects": [
    {
      "scope": "long_term",
      "field": "preferred_genres.progressive rock",
      "delta": 0.03,
      "reason": "liked recommended track with matched genre"
    }
  ],
  "created_at": "2026-06-16T00:00:00Z"
}
```

Supported events:

```text
liked
skipped
saved
playlist_add
request_similar
hide_artist
hide_track
```

## 4. Collection

Collection stores tracks the user explicitly saved or imported. It is user-owned memory, not the full recommendation source.

Path:

```text
data/collection/<user_id>.json
```

Schema:

```json
{
  "schema_version": 1,
  "user_id": "demo-user",
  "items": [
    {
      "track_id": "spotify:track:...",
      "provider": "spotify",
      "title": "Comfortably Numb",
      "artist": "Pink Floyd",
      "album": "The Wall",
      "image_url": "https://...",
      "added_at": "2026-06-16T00:00:00Z",
      "added_via": "agent_recommendation",
      "source_run_id": "run_123"
    }
  ],
  "updated_at": "2026-06-16T00:00:00Z"
}
```

Rules:

- Collection writes should be explicit: save, playlist add, import, or user-confirmed action.
- Collection can seed user memory, but it should not be the only candidate pool.
- Duplicate provider IDs should be deduplicated.

## 5. Provider Cache

Provider cache stores external API responses and normalized metadata. It is disposable and can expire.

Track cache path:

```text
data/cache/tracks/<provider>/<safe_track_id>.json
```

Track cache schema:

```json
{
  "schema_version": 1,
  "provider": "spotify",
  "provider_track_id": "spotify:track:...",
  "canonical_track_id": "spotify:track:...",
  "title": "Song Title",
  "artist": {
    "name": "Artist Name",
    "provider_artist_id": "spotify:artist:..."
  },
  "album": {
    "title": "Album Name",
    "provider_album_id": "spotify:album:...",
    "release_year": 1995,
    "image_url": "https://..."
  },
  "duration_ms": 230000,
  "external_urls": {
    "spotify": "https://open.spotify.com/track/..."
  },
  "preview_url": null,
  "tags": {},
  "genres": {},
  "raw": {},
  "fetched_at": "2026-06-16T00:00:00Z",
  "expires_at": "2026-06-23T00:00:00Z"
}
```

Search cache path:

```text
data/cache/searches/<cache_key>.json
```

Search cache schema:

```json
{
  "schema_version": 1,
  "cache_key": "sha256-of-provider-query-market",
  "provider": "spotify",
  "query": "british rock similar to oasis",
  "market": "AU",
  "results": [
    {
      "track_id": "spotify:track:...",
      "title": "Song Title",
      "artist": "Artist Name",
      "score": null
    }
  ],
  "fetched_at": "2026-06-16T00:00:00Z",
  "expires_at": "2026-06-17T00:00:00Z"
}
```

Rules:

- Cache must never be the source of truth for user preference.
- Cache entries may expire and be refreshed.
- Cache should store provider raw payloads only under `raw` so normalized fields stay stable.
- Provider IDs must be preserved to avoid mismatching tracks across services.

## 6. Trajectory

Trajectory stores a full agent run. It is the audit trail for tool calls, recommendations, evidence, memory changes, and errors.

Path:

```text
data/trajectories/<user_id>/<run_id>.json
```

Schema:

```json
{
  "schema_version": 1,
  "run_id": "run_123",
  "user_id": "demo-user",
  "session_id": "session_123",
  "turn_index": 3,
  "request": {
    "message": "有没有和绿洲差不多的英伦摇滚",
    "constraints": {
      "limit": 10,
      "exclude_seen": true,
      "max_per_artist": 2
    },
    "mode": "auto"
  },
  "plan": [
    {
      "step": 1,
      "goal": "read user memory",
      "tool": "get_user_memory"
    }
  ],
  "tool_calls": [
    {
      "step": 1,
      "tool": "search_tracks",
      "arguments": {
        "query": "british rock similar to oasis",
        "limit": 25,
        "market": "AU"
      },
      "status": "ok",
      "observation_ref": "inline",
      "observation": {
        "candidate_count": 25
      },
      "started_at": "2026-06-16T00:00:00Z",
      "completed_at": "2026-06-16T00:00:01Z"
    }
  ],
  "recommendations": [
    {
      "rank": 1,
      "track_id": "spotify:track:...",
      "score": 0.87,
      "evidence": {
        "matched_preferences": ["british rock"],
        "similar_collection_items": [],
        "feedback_signals": [],
        "historical_context": []
      },
      "reasons": [
        {
          "type": "session_intent",
          "label": "符合本次请求",
          "text": "这首歌符合你这次要的英伦摇滚方向。"
        }
      ]
    }
  ],
  "memory_updates": [
    {
      "scope": "session",
      "type": "seen_tracks",
      "summary": "Added 10 recommended tracks to session seen list."
    }
  ],
  "response": {
    "message": "我按英伦摇滚方向挑了一组歌。",
    "stop_reason": "goal_satisfied"
  },
  "agent": {
    "mode": "auto",
    "provider": "deepseek:deepseek-v4-flash",
    "fallback_reason": null
  },
  "created_at": "2026-06-16T00:00:00Z"
}
```

Rules:

- Trajectory is append-only at the run level. Feedback can append references, but should not rewrite the original tool history.
- User-facing API should call this `run_id`; internal storage can map old `trajectory_id` during migration.
- Tool observations can be summarized inline. Large raw provider payloads should live in cache and be referenced.
- Memory updates must be summarized so the user can understand what changed.

## 7. Migration From Existing Data

Existing structures can map into the new contract:

| Existing Data | Target Data |
| --- | --- |
| `data/user_profiles/<user_id>.json` | `data/memory/users/<user_id>.json` and `data/collection/<user_id>.json` |
| `data/sessions/<session_id>.json` | `data/memory/sessions/<session_id>.json` |
| `data/trajectories/<user_id>/<trajectory_id>.json` | `data/trajectories/<user_id>/<run_id>.json` |
| `data/song_profiles/*.json` | `data/cache/tracks/local/<track_id>.json` during transition |

Migration rules:

- `artist_preferences`, `genre_preferences`, and `tag_preferences` become `long_term` preference maps.
- `collection_song_ids` becomes collection items when metadata is available.
- `feedback_memory` becomes feedback events.
- Existing `trajectory_id` can be copied into `run_id` for backward compatibility.
- Existing local song profiles should be treated as cache or seed data, not the permanent recommendation pool.

## 8. Implementation Order

Recommended order:

1. Add target dataclasses or typed dictionaries for user memory, session memory, cache entries, and trajectories.
2. Add JSON stores for the new paths.
3. Add read adapters that can load existing L1/L6 files and convert to the new in-memory shape.
4. Update `/api/v1/agent/recommend` to return the new response shape.
5. Move feedback writes to append-only feedback events.
6. Stop relying on `data/song_profiles` as the only candidate source.
