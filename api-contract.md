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

### Delete (implemented)

```text
DELETE /api/collection/:user_id/:track_id
```

Removes one track from the user's collection. Returns 404 if the user profile
does not exist; returns `removed: false` if the track was not in the collection.

```json
{
  "user_id": "demo-user",
  "track_id": "spotify:track:...",
  "removed": true,
  "collection_count": 41
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
  "provider": "deepseek:deepseek-chat",
  "music_providers": {
    "spotify": true,
    "lastfm": true,
    "musicbrainz": true
  }
}
```

## 7. V2 研究接口（阶段 6，2026-09-29）

V2 用户（`data/users/<user_id>/context.json`，默认 `participant_001`）使用的接口。字段细节见 `data-contract.md` 9.5、9.6，说明见 `stage-6.md`。

| 接口 | 说明 |
|---|---|
| `POST /api/v1/agent/recommend` | V2 用户可传 `exploration_level`（0–1）、`branch_hint`、`strategy`（服务端 `strategies` 之一）或 `interleave`（两个不同策略，二者不能同时传）。交错时响应的 `interleaving` 为 `{pair_id, method}`，每首歌不带策略名 |
| `POST /api/v1/agent/feedback` | V2：`{user_id, impression_id, event?, seconds?, fraction?, survey?}`；未知曝光返回 404，问卷字段校验失败返回 400 |
| `POST /api/v2/chat` | 对话层（ReAct）：`{user_id, message, session_id?, count?, exploration_level?, interleave?, include_trace?}`。返回推荐结果（`recommendations` 可能为空）、`message`、`session_id`、`action`（`recommend` / `answer`） |
| `GET /api/v2/users/<user_id>/sessions/<session_id>` | 恢复会话：每轮的用户消息、回复、动作和卡片 |
| `GET /api/v2/status?user_id=` | `phase`、`strategies`、`agent_model`、`conversation_model`、`playback_lookup`、`known_user`、`exploration_level`、`branches`、`counts` |
| `GET /api/v2/users/<user_id>/saved` | 有 `saved` 事件的歌，新的在前 |
| `DELETE /api/v2/users/<user_id>/data` | 请求体 `{"scope": "interactions" \| "all", "confirm": "<user_id>"}`；`confirm` 不等于 user_id 时返回 400 |

推荐结果里每首歌的 `playback`：`{source, url, verified, search_links}`。`source` 为 `none` 时 `search_links` 给出 YouTube / Spotify 搜索链接（未核验）。

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

---

# V2 字段（长尾推荐重构）

> TODO.md 阶段 0 产物。原则：**只加字段，不改、不删已有字段**，现有前端不改也能继续工作。数据格式见 `data-contract.md` §9，工具见 `agent-tools-contract.md` V2 节。

## V2.1 Recommend 请求新增字段

```json
{
  "user_id": "participant_001",
  "message": "来点像 Wish You Were Here 但更冷门的",
  "exploration_level": 0.7,
  "branch_hint": null,
  "strategy": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `exploration_level` | number/null | 0–1；不传则用用户上下文里的默认值 |
| `branch_hint` | string/null | 可选，指定兴趣分支（如 `pink_floyd`）；不传则自动判断 |
| `strategy` | string/null | 仅开发和实验用：强制使用某个策略版本；普通请求不传 |

`mode` 的含义变化：`model` = 模型在候选集内选歌；`rules` = 确定性排序。两者的歌都只来自曲库，不再有模型提名歌曲的路径。

## V2.2 Recommend 响应新增字段

每条 `recommendations[]` 新增：

```json
{
  "rank": 1,
  "impression_id": "imp_…",
  "song_id": "s_…",
  "bucket": "tail",
  "channel": "tail",
  "strategy_version": "rag-tailmix-v1",
  "model_version": "sft-lora-v1",
  "source_label": "长尾发现",
  "playback": {"source": "youtube", "url": "https://www.youtube.com/watch?v=…", "verified": true},
  "evidence_items": [
    {"type": "seed_similarity", "detail": "与你的种子 Time 风格接近", "ref": "s_…"}
  ],
  "track": {"track_id": "s_…", "external_urls": {"spotify": null, "youtube": "https://www.youtube.com/watch?v=…"}}
}
```

| 字段 | 说明 |
|---|---|
| `impression_id` | 这次展示的 ID，反馈必须带上它 |
| `song_id` | 内部 ID（`s_…`）；`track.track_id` 在 V2 下与它相同 |
| `bucket` | `head` / `mid` / `tail` / `unknown` |
| `channel` | 主要召回通道：`rule` / `semantic` / `tail` / `explore` |
| `source_label` | 前端展示用：`熟悉相关`（head/mid 且相关性高）、`长尾发现`（tail）、`探索`（explore 通道） |
| `playback` | head/mid 优先 Spotify，tail 用已校验的 YouTube；校验失败为 `{"source": "none"}`，前端显示“暂无可播放链接” |
| `evidence_items` | V2 证据列表，每条可回溯到曲库；原 `evidence` 对象保留以兼容旧前端 |
| `track.external_urls.youtube` | 新增键 |

响应顶层新增：

```json
{
  "candidate_set_id": "cs_…",
  "strategy_version": "rag-tailmix-v1",
  "fallback_reason": null,
  "interleaving": null
}
```

两两交错实验中 `interleaving` 为 `{"pair_id": "pair_…", "arms": {"A": "…", "B": "…"}}`，每条推荐的归属只记在服务端的 ImpressionV2 里，**不返回给前端**，避免影响用户判断。

## V2.3 Feedback 请求新增字段

```json
{
  "user_id": "participant_001",
  "impression_id": "imp_…",
  "event": "play_progress",
  "seconds": 142,
  "fraction": 0.61,
  "survey": {
    "heard_before": "no",
    "relevance": 4,
    "discovery_value": 5,
    "too_unfamiliar": false,
    "would_save": true,
    "reject_reason": null
  }
}
```

- `impression_id`：V2 反馈必填；没有它的请求返回 `invalid_request`（旧的 `run_id` + `track_id` 形式在迁移期继续接受，按 v1 处理）；
- `event` 新增：`play_start`、`play_progress`、`completed`、`quick_skip`、`hide`，原有事件保留；
- `survey` 可选，字段含义见 `data-contract.md` §9.6；`reject_reason` 区分 `dislike_song` 和 `not_in_mood_to_explore`；
- `phase`（`dev` / `final`）由服务端按实验时间表决定，客户端不能指定。

## V2.4 Agent Status 新增字段

```json
{
  "catalog_version": "catalog-…",
  "index_version": "idx-…",
  "bucket_version": "bucket-v1",
  "default_strategy": "rag-tailmix-v1",
  "model": {"provider": "self-hosted", "base": "…", "adapter": "sft-lora-v1"},
  "music_providers": {"spotify": true, "youtube_verification": true, "musicbrainz": true, "listenbrainz": true}
}
```

## V2.5 参与者数据删除（阶段 6）

```text
DELETE /api/v1/participant/:user_id
```

删除该用户的上下文、曝光和反馈（`data/users/<user_id>/`），返回删除的记录数。训练数据中如包含该用户的开发期反馈，需同时在对应 split manifest 中标记并重新生成。
