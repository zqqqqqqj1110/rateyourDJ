# rateyourDJ Data Contract

This document defines the target data structures for the agent refactor. The goal is to make local data serve four clear roles:

```text
memory: what the agent knows about the user
cache: reusable external music provider results
trajectory: what happened during each agent run
collection: tracks the user explicitly saved
```

Local storage should no longer be treated as the only recommendation candidate database.

> **长尾推荐重构（2026-09）**：新的 V2 数据契约见文末“V2 契约：长尾推荐重构”一节，新代码以该节为准。

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
  "last_user_query": "换一批，不要刚才推荐过的",
  "preference_terms": ["british rock"],
  "exclude_terms": ["pink floyd"],
  "seen_track_ids": ["spotify:track:..."],
  "seed_track_ids": ["spotify:track:seed_1"],
  "active_constraints": {
    "limit": 10,
    "exclude_seen": true,
    "max_per_artist": 1,
    "market": "AU",
    "year_range": {
      "min": 1990,
      "max": 2005
    }
  },
  "last_run_id": "run_123",
  "last_recommendation_ids": ["spotify:track:..."],
  "temporary_feedback": [
    {
      "track_id": "spotify:track:...",
      "event": "skipped",
      "created_at": "2026-06-16T00:00:00Z"
    }
  ],
  "created_at": "2026-06-16T00:00:00Z",
  "updated_at": "2026-06-16T00:00:00Z"
}
```

Rules:

- `seen_track_ids` is used to support "换一批" and `exclude_seen`.
- `exclude_terms`, `seed_track_ids`, and `temporary_feedback` are session-scoped and do not directly mutate long-term memory.
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
    "provider": "deepseek:deepseek-chat",
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

---

# V2 契约：长尾推荐重构

> 本节是 TODO.md 阶段 0 的产物，定义阶段 1–6 共享的数据格式。以上 1–8 节是 v1 设计，迁移期间继续可读；新代码以本节为准。
> 所有 V2 记录都带 `schema_version`，格式为 `<名称>/v2`。

## 9.0 数据流与目录

```text
MusicBrainz + ListenBrainz ──> catalog/processed/songs.jsonl (SongProfileV2)
                                        │
UserContextV2 + 文本请求 ──> 多路召回 ──> RetrievalCandidateV2[]
                                        │
                        排序 / 模型选择（只能在候选集内）
                                        │
                           ImpressionV2（曝光） ──> FeedbackV2（反馈）
                                        │
                     SFT / GRPO 样本（训练） + runs/<run_id>/manifest.json
```

```text
data/
  users/<user_id>/
    context.json            UserContextV2
    impressions.jsonl       ImpressionV2（追加写）
    feedback.jsonl          FeedbackV2（追加写）
  catalog/
    raw/                    MusicBrainz / ListenBrainz 原始下载（不进 Git）
    processed/songs.jsonl   SongProfileV2，一行一首
    manifest.json           曲库版本、来源、下载日期、许可
  index/<index_version>/    向量索引与其 manifest（不进 Git）
  training/
    sft/{train,val,test}.jsonl
    grpo/{train,val,test}.jsonl
runs/<run_id>/manifest.json 每次实验的 run manifest（见 9.10）
```

`data/users/`、`data/catalog/`、`data/index/`、`data/training/` 全部不进 Git；需要复现时由脚本重新生成。

## 9.1 ID 规则

- **内部 song ID**：`s_` + 16 位十六进制，只含 `[A-Za-z0-9_.-]`，可直接当文件名。
  - 有 MusicBrainz recording MBID 时：`s_` + `sha1("mb:" + recording_mbid)[:16]`；
  - 没有 MBID 时：`s_` + `sha1("na:" + 规范化艺人 + "|" + 规范化歌名)[:16]`，并标记 `id_basis: "name"`，以后拿到 MBID 再迁移。
- **外部 ID** 只放在 `external_ids` 里，绝不当文件名或主键：`musicbrainz_recording`、`musicbrainz_artists[]`、`isrc[]`、`spotify_track`、`youtube_video`。
- 旧数据里的 `spotify:track:...` 这类带冒号的 ID 迁移时放进 `external_ids.spotify_track`，重新生成内部 ID（见 9.11）。
- 用户 ID 沿用 `[A-Za-z0-9_.-]+`，第一阶段只有 `participant_001`。

## 9.2 UserContextV2

轻量用户上下文，不再维护手工的重型画像。偏好（流派、艺人、标签）按需从种子和反馈算出来，不存。

```json
{
  "schema_version": "user-context/v2",
  "user_id": "participant_001",
  "seed_branches": [
    {
      "branch_id": "pink_floyd",
      "label": "迷幻 / 前卫 / 艺术摇滚 / 氛围化",
      "seed_song_ids": ["s_1a2b3c4d5e6f7a8b"],
      "seed_artists_mbid": ["83d91898-7763-47d7-b03b-b92132375c47"]
    },
    {
      "branch_id": "oasis",
      "label": "Britpop / 另类摇滚 / 旋律型吉他摇滚",
      "seed_song_ids": ["s_9f8e7d6c5b4a3210"],
      "seed_artists_mbid": ["39ab1aed-75e0-4140-bd47-540276886b60"]
    }
  ],
  "exploration_level": 0.5,
  "exclusions": {"song_ids": [], "artists_mbid": [], "tags": []},
  "heard_song_ids": [],
  "recommended_song_ids": [],
  "recent_feedback_ids": [],
  "created_at": "2026-09-26T00:00:00Z",
  "updated_at": "2026-09-26T00:00:00Z"
}
```

| 字段 | 说明 |
|---|---|
| `seed_branches` | 每条兴趣分支独立保存，不合并成“英伦摇滚”；算法不得硬编码分支名 |
| `exploration_level` | 0–1，0 = 只要熟悉相关，1 = 尽量探索 |
| `heard_song_ids` | 用户明确听过的歌（来自反馈里的 `heard_before`） |
| `recommended_song_ids` | 曾经曝光过的歌，用于去重 |
| `recent_feedback_ids` | 最近 N 条反馈的引用，原始记录在 `feedback.jsonl` |

## 9.3 SongProfileV2

```json
{
  "schema_version": "song-profile/v2",
  "song_id": "s_1a2b3c4d5e6f7a8b",
  "id_basis": "mbid",
  "title": "Time",
  "artists": [{"name": "Pink Floyd", "mbid": "83d91898-7763-47d7-b03b-b92132375c47"}],
  "release": {"title": "The Dark Side of the Moon", "year": 1973, "country": "GB"},
  "duration_ms": 413000,
  "external_ids": {
    "musicbrainz_recording": "…",
    "isrc": ["…"],
    "spotify_track": "…",
    "youtube_video": null
  },
  "tags": [{"name": "progressive rock", "weight": 0.92, "source": "musicbrainz"}],
  "genres": ["progressive rock", "psychedelic rock"],
  "popularity": {
    "listen_count": 1234567,
    "listener_count": 45678,
    "source": "listenbrainz",
    "metric": "listener_count",
    "global_percentile": 0.998,
    "genre_percentile": {"progressive rock": 0.999},
    "bucket": "head",
    "bucket_version": "bucket-v2",
    "computed_at": "2026-09-27T00:00:00Z"
  },
  "playback": {
    "source": "spotify",
    "url": "https://open.spotify.com/track/…",
    "verified": true,
    "verification_method": "spotify_api",
    "verified_at": "2026-09-27T00:00:00Z"
  },
  "rag_doc_id": "doc_s_1a2b3c4d5e6f7a8b",
  "provenance": [
    {"source": "musicbrainz", "license": "CC0", "fetched_at": "2026-09-27T00:00:00Z"},
    {"source": "listenbrainz", "license": "CC0", "fetched_at": "2026-09-27T00:00:00Z"}
  ]
}
```

**冷门程度分档（`bucket-v2`）**

- 热度指标：ListenBrainz **听众数** `listener_count`（不用收听次数，避免被少数人循环播放放大）；
- 参照范围：**ListenBrainz 全网**。ListenBrainz 不直接提供全网百分位，所以用抽样估计：从 MusicBrainz 核心导出 `mbdump.tar.bz2` 的录音表中均匀抽约 20 万个录音（排除视频），查它们的听众数，取**至少 1 个听众**的录音作为总体，得到经验分布，存为 `data/catalog/popularity_reference.json`；
- `global_percentile` = 该歌听众数在总体中的中位秩（1.0 = 最热）；0 个听众记 0.0；
- `head`：全网前 1%；`mid`：全网前 1%–10%；`tail`：其余（全网后 90%）；
- 实测参照（MusicBrainz 导出 2026-09-23，抽样 20 万条中 93,234 条有听众）：head ≥ 3,735 个听众，mid ≥ 189，tail < 189；
- 版本历史：`bucket-v1` 用全网前 10% / 50%，但全网一半有听众的录音只有 ≤ 5 个听众，曲库只剩 0.3% 为 tail，已弃用；
- 没有 ListenBrainz 数据的歌记为 `unknown`，不算进长尾指标，单独报告数量；
- `genre_percentile` 是在曲库内、同流派歌曲之间算的补充指标（同流派不足 20 首不算）；
- 阈值、参照分布都属于分档版本，改任何一个都必须升版本号；原始听众数始终保留，换参照只需重算，不用重新采集。

**播放来源规则**

| bucket | 首选 | 备选 |
|---|---|---|
| head / mid | Spotify（有 `spotify_track`） | 已校验的 YouTube |
| tail | 已校验的 YouTube | 无 |
| 任何 | 校验失败 → `source: "none"`，前端显示“暂无可播放链接”，不凑数 |

YouTube 链接由 LLM API 联网搜索得到，必须用 YouTube oEmbed 或 Data API 确认视频存在，而且标题包含歌名、频道或标题包含艺人名，才能 `verified: true`。

## 9.4 RetrievalCandidateV2

召回阶段的输出。排序和模型选择只能从这里选歌。

```json
{
  "schema_version": "retrieval-candidate/v2",
  "request_id": "req_…",
  "candidate_set_id": "cs_…",
  "song_id": "s_…",
  "bucket": "tail",
  "channels": [
    {"channel": "semantic", "rank": 3, "raw_score": 0.71},
    {"channel": "tail", "rank": 1, "raw_score": 0.64}
  ],
  "fused_score": 0.68,
  "relevance": 0.66,
  "tail_score": 0.85,
  "evidence": [
    {"type": "seed_similarity", "detail": "与种子 Time 的 embedding 相似度 0.71", "ref": "s_1a2b3c4d5e6f7a8b"},
    {"type": "shared_tag", "detail": "progressive rock", "ref": "musicbrainz"}
  ],
  "index_version": "idx-…",
  "branch_affinity": {"pink_floyd": 0.74, "oasis": 0.12}
}
```

- `channel` 取值：`rule`（原标签召回）、`semantic`（文本语义）、`tail`（长尾专用）、`explore`（探索）；
- `evidence` 每条都必须能回溯到曲库事实或种子歌，不能是模型自己编的；
- 同一 `candidate_set_id` 固定后可复现（用于离线评估和训练样本）。

## 9.5 ImpressionV2（曝光）

每首展示给用户的歌写一条。**没有曝光记录的歌不能成为负反馈。**

```json
{
  "schema_version": "impression/v2",
  "impression_id": "imp_…",
  "user_id": "participant_001",
  "run_id": "run_…",
  "request_id": "req_…",
  "candidate_set_id": "cs_…",
  "song_id": "s_…",
  "rank": 4,
  "bucket": "tail",
  "channel": "tail",
  "strategy_version": "rag-tailmix-v1",
  "model_version": null,
  "interleaving": {"pair_id": "pair_…", "arm": "B", "arms": {"A": "rag-rel-v1", "B": "rag-tailmix-v1"}},
  "playback_source": "youtube",
  "shown_at": "2026-10-01T10:00:00Z"
}
```

- `strategy_version` 例：`legacy-deepseek-v0`（旧路径，只在 baseline 里出现）、`rag-rel-v1`、`rag-tailmix-v1`、`sft-lora-v1`、`grpo-v1`；
- `interleaving` 只在两两交错对比时出现：同一请求混排两个策略的结果，`arm` 标记这首歌来自哪个策略，反馈据此归因。

## 9.6 FeedbackV2（反馈）

```json
{
  "schema_version": "feedback/v2",
  "feedback_id": "fb_…",
  "impression_id": "imp_…",
  "user_id": "participant_001",
  "song_id": "s_…",
  "events": [
    {"type": "play_start", "at": "2026-10-01T10:00:05Z"},
    {"type": "play_progress", "seconds": 142, "fraction": 0.61, "at": "2026-10-01T10:02:27Z"},
    {"type": "liked", "at": "2026-10-01T10:02:30Z"}
  ],
  "survey": {
    "heard_before": "no",
    "relevance": 4,
    "discovery_value": 5,
    "too_unfamiliar": false,
    "would_save": true,
    "reject_reason": null
  },
  "phase": "dev",
  "created_at": "2026-10-01T10:02:40Z"
}
```

- `events.type`：`play_start`、`play_progress`、`completed`、`quick_skip`（30 秒内跳过）、`liked`、`saved`、`hide`；
- `survey.heard_before`：`yes` / `no` / `unsure`；`relevance`、`discovery_value`：1–5；
- `reject_reason`：`dislike_song`（不喜欢这首）/ `not_in_mood_to_explore`（当前不想探索）/ `other`，两者必须区分；
- `phase`：`dev`（开发轮次，可以用来检查 reward 是否合理）或 `final`（最终 case study，**永远不进任何训练或调参**）。

## 9.7 SFT 样本

OpenAI chat / tool-call 格式（vLLM 与 Qwen 的聊天模板直接可用），**不包含隐藏思维过程**（没有 `thought` 字段）。一条样本就是一次完整执行过的 agent 轨迹，system / user 消息由 `agent.loop.build_messages` 生成，与推理时逐字相同；工具返回是真实工具的输出。

```json
{
  "schema_version": "sft-sample/v2",
  "sample_id": "sft_…",
  "tools": ["…Toolbox.schemas()，与推理时相同…"],
  "messages": [
    {"role": "system", "content": "你是 rateyourDJ……"},
    {"role": "user", "content": "用户请求：来点像《Time》那样的歌\n需要 10 首；探索强度 0.5；兴趣分支：pink_floyd, oasis"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function",
      "function": {"name": "retrieve_candidates", "arguments": "{\"summary\": \"请求点名了《Time》……\", \"query\": \"像 Time 那样的歌\", \"branch_hint\": \"pink_floyd\", \"exploration_level\": 0.5}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "name": "retrieve_candidates", "content": "{…真实工具输出…}"},
    {"role": "assistant", "content": "", "weight": 0, "tool_calls": ["…故意出错的提交（只在 repair 样本中）…"]},
    {"role": "tool", "tool_call_id": "call_2", "name": "submit_recommendations", "content": "{…校验器的修改要求…}"},
    {"role": "assistant", "content": "", "tool_calls": ["…submit_recommendations：picks / message / summary…"]}
  ],
  "meta": {
    "template_id": "S01", "mode": "single", "lang": "zh", "kind": "direct | ranked | repair",
    "branch_hint": "pink_floyd", "exploration_level": 0.5, "count": 10, "exclude_artists": [],
    "candidate_set_id": "cs_…", "min_tail": 3, "tail_picks": 4,
    "oracle_version": "oracle-v1", "data_version": "sft-data-v1", "split_version": "split-v1",
    "split": "train"
  }
}
```

- 最后一条消息永远是 `submit_recommendations`（校验通过即结束，与推理循环一致）；
- `weight: 0` 的 assistant 消息不计入训练损失，只作为上下文（repair 样本里故意出错的那次提交）；
- 标准答案由确定性 oracle（`rag-tailmix-v1`）给出，理由只用证据模板生成，理由里的数字必须出现在所引用的证据中；
- 生成代码：`src/rateyourdj/training/sft_data.py`、`sft_scenarios.py`。

## 9.8 GRPO 样本

```json
{
  "schema_version": "grpo-sample/v2",
  "sample_id": "grpo_…",
  "prompt": [{"role": "system", "content": "…"}, {"role": "user", "content": "…"}],
  "candidates": ["…20–30 条 RetrievalCandidateV2…"],
  "constraints": {"count": 5, "max_per_artist": 1, "exclude_song_ids": [], "min_tail": 1},
  "reward_spec_version": "reward-v1",
  "meta": {"scenario": "…", "branch": "oasis", "exploration_level": 0.3, "split": "train"},
  "eval_only": {
    "hidden_positives": ["s_…"],
    "source": "listenbrainz_similar_users",
    "similar_user_ids": ["lb_user_hash_…"]
  }
}
```

- prompt 里不含唯一标准答案；
- **`eval_only` 严格不进 reward**：训练数据加载器必须在读入时删掉整个 `eval_only` 字段，由测试强制检查；它只用于验证集、测试集和 checkpoint 选择；
- `hidden_positives` 来自 ListenBrainz / MSD 中与 `participant_001` 口味相近用户的真实收听日志；用户 ID 只存哈希。

## 9.9 数据集划分

- train / val / test 按 **说法 + 候选艺人** 隔离（`split-v1`，阶段 4 修订：只有一个用户，种子艺人在所有 split 中都相同，无法按种子隔离）：
  - 说法：部分请求模板（及其改写）只用于 val / test；
  - 候选艺人：约 15% 的非种子艺人被保留，从所有 train 样本的召回里移除，训练时模型从未见过、也从未选过它们；
  - 阶段 3 的 13 条固定查询只进 test；
- 按 `candidate_set_id` 固定候选集，同一候选集只属于一个 split；
- 固定 seed，划分结果写入 `data/training/<kind>/split_manifest.json`；
- `phase: "final"` 的反馈永远不进任何 split；
- 冻结测试集后不再修改，改动只能新建版本。

## 9.10 Run manifest

每次 baseline、评估、训练、消融都在 `runs/<run_id>/manifest.json` 写一份（实现：`src/rateyourdj/experiment.py`）：

```json
{
  "schema_version": "run-manifest/v1",
  "run_id": "baseline-v0",
  "kind": "baseline",
  "created_at": "2026-09-26T13:00:00+00:00",
  "git": {"commit": "520e7d1…", "dirty": false},
  "environment": {"python": "3.12.4", "platform": "macOS-…"},
  "data": {"eval/queries_v1.jsonl": "<sha256>"},
  "model": {"provider": "deepseek", "name": "deepseek-chat", "adapter": null},
  "seed": null,
  "config": {"…": "…"},
  "metrics": {"…": "…"},
  "notes": ""
}
```

`runs/**/raw/` 不进 Git，manifest 和汇总结果可以提交。

## 9.11 从 v1 迁移

| v1 数据 | V2 数据 | 规则 |
|---|---|---|
| `data/user_profiles/<id>.json` | `data/users/<id>/context.json` | `collection_song_ids` → 候选种子（需人工确认后才写入 `seed_branches`）；偏好权重不迁移，按需重算 |
| `data/song_profiles/*.json` | `catalog/processed/songs.jsonl` | 重新生成内部 ID；原 ID 放进 `external_ids`；`popularity.bucket` 先记 `unknown`，拿到 ListenBrainz 数据后再算 |
| `data/trajectories/**` | 不迁移 | 只读保留，作为 `legacy-deepseek-v0` 的历史记录 |
| `data/sft.jsonl`、`grpo.jsonl`、`dataset.jsonl` | 废弃 | 不迁移，新训练数据从头生成 |

旧 JSON 必须仍能被读取：V2 读取器遇到没有 `schema_version` 或版本为 v1 的记录时走迁移适配器，而不是报错。
