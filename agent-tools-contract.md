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

---

# V2：候选集内选歌的工具契约（长尾推荐重构）

> TODO.md 阶段 0 产物。数据格式见 `data-contract.md` §9。上文 v1 工具在迁移期继续可用；新代码以本节为准。

## V2.1 范式变化

| | v1（下线） | V2 |
|---|---|---|
| 歌从哪来 | DeepSeek 凭记忆提名，再用 Spotify 确认存在 | 只来自曲库的多路召回（RetrievalCandidateV2） |
| 模型做什么 | 生成歌名 + 解释 | 解析意图、调用召回、**在候选集内**选歌并引用证据 |
| 幻觉防线 | 事后确认，确认不了就丢 | 事前约束：`song_id` 不在候选集内直接判违规 |
| 失败回退 | 规则排序 | 确定性排序（阶段 3 的固定策略） |

以下 v1 工具与路径在阶段 3 之前下线：`discover_tracks`、`DiscoveryService.ground_candidates`、统一“先回答”路径里的 `suggest_new`。

## V2.2 工具清单

模型可调用（出现在模型的工具列表里）：

```text
get_user_context       读取 UserContextV2
retrieve_candidates    多路召回，返回一个固定的候选集
get_track_facts        读取候选歌的曲库事实，用于写理由
rank_candidates        确定性排序（给模型参考，也是回退结果）
```

仅系统调用（模型看不到）：

```text
validate_selection     校验模型输出：候选集内、数量、约束、证据引用
record_impressions     为最终展示的每首歌写 ImpressionV2
record_feedback        写 FeedbackV2
```

所有工具沿用上文的 Standard Observation Envelope（`tool` / `status` / `data` / `diagnostics` / `retryable` / `suggested_actions`）。

## V2.3 get_user_context

参数：

```json
{"user_id": "participant_001"}
```

`data`：一条 UserContextV2，外加按需算出的摘要（不落盘）：

```json
{
  "context": {"schema_version": "user-context/v2", "…": "…"},
  "derived": {
    "branches": [
      {"branch_id": "pink_floyd", "top_tags": ["progressive rock", "psychedelic rock"], "seed_count": 3},
      {"branch_id": "oasis", "top_tags": ["britpop", "alternative rock"], "seed_count": 3}
    ]
  }
}
```

## V2.4 retrieve_candidates

参数：

```json
{
  "user_id": "participant_001",
  "query": "来点像 Wish You Were Here 但更冷门的",
  "branch_hint": "pink_floyd",
  "exploration_level": 0.7,
  "limit": 30,
  "exclude_song_ids": [],
  "channel_quota": {"rule": 6, "semantic": 10, "tail": 10, "explore": 4}
}
```

| 字段 | 说明 |
|---|---|
| `branch_hint` | 可选：`null` 表示按请求自动判断或两条分支都用；取值必须是用户上下文里存在的 `branch_id` |
| `exploration_level` | 可选，覆盖用户默认值 |
| `channel_quota` | 可选，默认值由召回配置决定 |
| `exclude_artists` | 可选（阶段 4 新增）：用户明确不要的艺人名，按曲库艺人名匹配，这些艺人的歌不进入候选。`query` 只写想要的内容，“不要某艺人”放在这里 |

`data`：

```json
{
  "candidate_set_id": "cs_…",
  "index_version": "idx-…",
  "candidates": ["…RetrievalCandidateV2…"],
  "channel_counts": {"rule": 6, "semantic": 10, "tail": 9, "explore": 4},
  "fallback": null
}
```

- 同一 `candidate_set_id` 可以复现，候选集会被缓存，供校验和训练样本引用；
- `candidate_set_id` 由请求参数、索引和配置决定；用户上下文里的已听、已推荐、排除歌曲不为空时也计入（阶段 4 修正：此前上下文变化后同一 ID 会指向不同候选集）；
- 没有向量索引时 `fallback: "rule_only"`，只返回标签召回结果，`status: "partial"`。

## V2.5 get_track_facts

参数：`{"song_ids": ["s_…"], "fields": ["title", "artists", "release", "tags", "popularity.bucket"]}`

- `song_ids` 必须属于本轮某个候选集，否则 `status: "error"`；
- 只返回曲库里有来源的事实，不做生成。

## V2.6 rank_candidates

参数：

```json
{"candidate_set_id": "cs_…", "strategy": "rag-tailmix-v1", "count": 10}
```

`strategy` 取值：`rag-rel-v1`（只看相关性）、`rag-tailmix-v1`（固定混排：6 相关 + 3 长尾 + 1 探索，按探索强度调整）。

`data.ranked[]` 每项含 `song_id`、`rank`、`score_breakdown`（relevance、tail_score、tail_relevance、novelty、diversity、exploration_fit、penalties、`weights_version`），结果完全可复算。

## V2.7 模型的最终输出（选择结果）

模型不再输出自由文本歌单，而是输出以下 JSON：

```json
{
  "message": "给用户看的一段简短说明",
  "picks": [
    {"song_id": "s_…", "reason": "一句话理由", "evidence_refs": [0, 1]}
  ]
}
```

- `evidence_refs` 是该候选 `evidence[]` 的下标，理由里的事实必须能在被引用的证据中找到；
- **不包含** `thought` 或任何隐藏推理字段。

## V2.8 validate_selection（系统）

按顺序检查，任何一条失败就整轮回退到 `rank_candidates` 的结果，并在轨迹里记录 `fallback_reason`：

1. JSON 合法、字段齐全；
2. 每个 `song_id` 都在 `candidate_set_id` 内（候选集外推荐率必须为 0）；
3. 数量、`max_per_artist`、排除项、`min_tail` 等约束满足；
4. `evidence_refs` 下标有效，且每首至少引用 1 条证据；
5. 没有重复歌曲。

## V2.9 record_impressions / record_feedback（系统）

- `record_impressions`：最终展示的每首歌写一条 ImpressionV2，带 `rank`、`bucket`、`channel`、`strategy_version`、`model_version`，两两交错实验时带 `interleaving`；
- `record_feedback`：写 FeedbackV2，必须带 `impression_id`，没有曝光的歌拒绝写入。

## V2.10 推荐轮次的工具流

```text
get_user_context
retrieve_candidates            (可调用多次，例如两条分支各一次)
get_track_facts                (写理由需要时)
rank_candidates                (可选，参考或回退)
→ 模型输出 picks
validate_selection             (系统)
record_impressions             (系统)
```

模型不可用（未配置、超时或输出不合法）时：`retrieve_candidates` → `rank_candidates` → `validate_selection` → `record_impressions`，全程确定性。

## V2.11 与 v1 工具的对应

| v1 工具 / 路径 | V2 |
|---|---|
| `get_user_memory`、`L1.inspect_user_profile` | `get_user_context` |
| `search_tracks`、`get_similar_tracks`、`L3.retrieve_candidates` | `retrieve_candidates` |
| `get_track_metadata`、`L2.inspect_song_profile` | `get_track_facts` |
| `rank_candidates`、`L4.rank_candidates` | `rank_candidates`（改为按 `candidate_set_id`） |
| `discover_tracks` + Spotify grounding | 下线 |
| `record_feedback` | `record_feedback`（改用 FeedbackV2，需要 `impression_id`） |
| `explain_recommendations` | 合并进模型输出的 `reason` + `evidence_refs` |
