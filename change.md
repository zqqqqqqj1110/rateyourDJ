# rateyourDJ 重构修改点

当前项目需要从“本地音乐推荐管线”升级为“有记忆、有工具、有解释能力的 DJ Agent”。下面是后续需要改进的核心方向。

## 1. 从本地候选库改成 Agent 主导发现音乐

当前推荐逻辑主要是在本地数据库中检索和排序歌曲，这会让项目更像传统推荐系统，而不是标准 agent 项目。

后续应改成：

```text
用户请求
    ↓
Agent 理解意图和约束
    ↓
调用外部音乐源搜索 / 相似歌曲 / metadata 工具
    ↓
生成候选歌曲
    ↓
结合用户偏好、历史反馈和本轮约束排序
    ↓
返回推荐歌单和推荐理由
```

本地不再作为唯一候选歌曲库，而是作为记忆、缓存和运行记录。

## 2. 重新定义本地数据职责

本地数据库不应该完全删除，但需要改变定位。

应该保留：

- 用户长期偏好 memory
- 收藏、喜欢、跳过等反馈记录
- session 上下文
- agent trajectory / run history
- 外部 API metadata cache
- 歌曲、艺人、专辑的临时缓存

应该弱化或删除：

- 把本地 `song_profiles` 当成唯一推荐候选池的设计
- 只在本地数据中召回歌曲的推荐路径

目标是区分：

```text
memory: 用户状态和长期偏好
cache: 外部 API 查询结果缓存，可过期
trajectory: agent 每次运行的工具调用和结果
candidate source: 由外部音乐工具动态发现
```

## 3. 重构 Agent 架构边界

当前 `L6` 承担了太多职责，包括 query 解析、session、LLM 决策、tool 调用、参数校验、rule fallback、ranking retry 和 trajectory 持久化。

后续应拆成更标准的 agent 结构：

```text
agent/
  runtime.py        # agent loop 和 step execution
  planner.py        # model planner / rule planner
  tools.py          # agent-facing tool contract
  registry.py       # tool registry
  guards.py         # request patch 和 tool argument safety
  memory.py         # user memory / session memory
  trajectories.py   # run history
  explanations.py   # 推荐理由生成
  providers/        # DeepSeek / mock / future providers
domain/
  recommendation.py # ranking / scoring
  profiles.py       # user/song profile domain
  collectors.py     # data source integration
```

L1-L7 可以暂时作为内部 domain 能力保留，但不应该继续作为产品概念暴露给用户。

## 4. API 接口产品化

API 应该围绕 agent 能力设计，而不是暴露内部 L1-L7 层级。

建议接口：

```text
POST /api/agent/recommend
POST /api/agent/feedback
GET  /api/agent/session/:id
GET  /api/profile
POST /api/collection
DELETE /api/collection/:song_id
```

推荐请求示例：

```json
{
  "user_id": "demo-user",
  "message": "有没有和绿洲差不多的英伦摇滚",
  "session_id": "...",
  "constraints": {
    "limit": 10,
    "exclude_seen": true
  }
}
```

推荐响应示例：

```json
{
  "message": "...",
  "session_id": "...",
  "recommendations": [],
  "trace": {
    "tools": [],
    "reason": "..."
  }
}
```

`trace` 只作为开发或调试信息，前端默认不应该把内部 L4/L6 细节作为主体验。

## 5. 前端界面改成 DJ Agent 体验

当前界面更像数据展示页：画像、收藏、L6 agent、recommendations。

后续应改成以 agent 交互为中心：

- 用户告诉 DJ 当前想听什么
- agent 返回一组推荐
- 每首歌支持喜欢、跳过、收藏、加入歌单
- 支持“换一批”“推荐类似这首”“不要这个风格”
- 每首歌可以展开“为什么推荐”
- agent trace / tool calls 放在折叠的开发区域

界面文案不应继续暴露 `L4 分数拆解`、`L6 Recommendation Agent` 这类内部实现名。

更合适的产品语言：

- DJ Agent
- Music Discovery Agent
- Preference Memory
- Recommendation Run
- Tool Calls
- Feedback Events

## 6. 反馈系统升级为长期 / 短期 Memory

反馈不应该只影响下一次排序，而应该进入 agent memory。

需要区分：

```text
长期偏好: 用户长期喜欢的艺人、风格、年代、标签
短期意图: 当前 session 想听什么
硬性约束: 不要某个艺人、不要重复歌手、不要已推荐过的歌
反馈事件: 喜欢、跳过、收藏、加入歌单
```

重要原则：

- “今天不要 Pink Floyd” 是 session constraint，不应永久写入长期不喜欢。
- “我不喜欢 Pink Floyd” 才应该进入长期 negative preference。
- “换一批” 应该避开当前 session 已推荐过的歌曲。

## 7. Agent Memory 设计

Agent Memory 应该成为 rateyourDJ 的核心能力之一。它不只是保存用户收藏，而是保存 agent 对用户音乐偏好的长期理解、当前会话状态和反馈变化。

建议拆成几类 memory：

```text
Long-term Memory: 长期音乐偏好
Session Memory: 当前对话和本轮约束
Feedback Memory: 喜欢、跳过、收藏、加入歌单等行为
Collection Memory: 用户明确收藏的歌曲和歌单
Discovery Memory: 已推荐、已看过、已拒绝的歌曲
```

长期 memory 应该记录：

- 偏好的艺人、流派、标签、年代、场景和情绪
- 长期不喜欢的艺人、风格或推荐方向
- 用户对推荐解释的偏好，例如更想看音乐史背景还是相似性理由
- 用户常见请求模式，例如“换一批”“不要重复歌手”“更冷门一点”

Session memory 应该记录：

- 当前请求意图
- 本轮硬性约束
- 本轮临时排除项
- 已推荐过的歌曲
- 用户在本轮里点击喜欢、跳过、收藏的内容

Memory 更新需要有明确规则：

- 只有明确表达长期偏好时，才写入 long-term memory。
- 临时约束只写入 session memory。
- 单次跳过不一定代表长期讨厌，需要多次反馈或明确表达后再升级。
- 收藏和喜欢可以作为正向长期信号，但仍需要按强度加权。
- agent 每次更新 memory 时应该产生可审计记录。

建议 memory schema：

```json
{
  "user_id": "demo-user",
  "long_term": {
    "preferred_artists": {},
    "preferred_genres": {},
    "preferred_tags": {},
    "negative_preferences": {},
    "explanation_preferences": {}
  },
  "session": {
    "session_id": "...",
    "current_intent": "recommend",
    "constraints": {},
    "seen_track_ids": [],
    "temporary_exclusions": []
  },
  "feedback": [
    {
      "track_id": "...",
      "event": "liked",
      "created_at": "..."
    }
  ]
}
```

Agent tools 应该提供显式 memory 操作：

```text
get_user_memory
get_session_memory
update_session_memory
propose_memory_update
commit_memory_update
record_feedback
```

其中 `propose_memory_update` 和 `commit_memory_update` 应该分开。agent 可以先提出“我认为用户可能喜欢 70s progressive rock”，但只有在规则或用户反馈足够明确时才提交到长期 memory。

## 8. 增加多维推荐理由

每首歌不只返回分数，还应该返回可解释的推荐理由。

推荐理由可以来自：

- 历史收藏
- 长期偏好
- 本轮请求
- 之前反馈
- 相似歌曲
- 风格、年代、场景、情绪
- 音乐史或文化背景
- 探索价值和多样性

推荐结果结构可以包含：

```json
{
  "track": {},
  "score": 0.87,
  "evidence": {
    "matched_preferences": ["progressive rock", "classic rock"],
    "similar_collection_items": ["Comfortably Numb"],
    "historical_context": ["1970s art rock influence"],
    "feedback_signals": ["liked similar guitar-led tracks"]
  },
  "reasons": [
    {
      "type": "listening_history",
      "label": "基于你的历史收藏",
      "text": "你收藏过多首 Pink Floyd 作品，这首歌在编曲层次和氛围上相近。"
    }
  ]
}
```

推荐理由不应完全依赖 LLM 临时编写。更好的方式是：

```text
ranking evidence
+ user memory
+ song metadata
+ artist / album / historical context
+ LLM explanation writer
= final reasons
```

也就是先生成结构化证据，再由模型写成人话。

## 9. 增加外部音乐 API Adapter 层

如果候选歌曲不再依赖本地数据库，就需要稳定的外部 provider 抽象。

建议抽象：

```text
MusicSearchProvider
MetadataProvider
SimilarityProvider
PlaybackProvider
```

底层可以逐步接入：

- Spotify
- Last.fm
- MusicBrainz
- YouTube Music
- Apple Music

Agent 不应该直接散落调用具体 API，而应该通过工具和 provider adapter 调用。

## 10. Agent Tools 重新设计

后续 agent-facing tools 应该围绕音乐发现和用户记忆，而不是围绕 L1-L7。

建议工具：

```text
get_user_memory
update_user_memory
search_tracks
get_track_metadata
get_artist_profile
get_similar_tracks
rank_candidates
explain_recommendation
record_feedback
save_to_collection
```

这些工具应有明确 schema、权限边界和可测试 mock。

## 11. 测试方向调整

测试不应只锁定 L1-L7 的内部实现，而应增加 agent contract 测试。

需要覆盖：

- agent 收到请求后选择正确工具
- 不访问其他用户数据
- 不把短期约束错误写入长期 memory
- 外部 API 失败时有 fallback
- feedback 会影响下一轮推荐
- mock provider 下完整推荐链路稳定
- 推荐理由基于 evidence，不是空泛生成

## 12. SFT / LoRA 作为后期优化

当前阶段不建议先做 SFT 或 LoRA。现在的核心问题是架构、工具边界、memory、API 和推荐证据链，而不是模型参数能力不足。

当前应优先完成：

- agent tools schema
- memory / cache / trajectory 分层
- 外部音乐 API adapter
- recommendation evidence
- explanation writer prompt
- feedback loop
- mock provider 测试

短期内更适合使用：

```text
Prompt Engineering
+ Tool Calling
+ User Memory
+ Metadata Cache
+ Recommendation Evidence
+ Explanation Writer
```

SFT 或 LoRA 适合在后期考虑，前提是：

- agent 架构已经稳定
- tools 和 API contract 已经稳定
- 已积累足够多真实或高质量合成的推荐轨迹
- 有明确的评估集和质量指标
- 已经知道需要优化的是 DJ 语气、JSON 稳定性、工具选择习惯，还是推荐解释风格

可能适合微调的场景：

- 固定 rateyourDJ 的专属 DJ 语气
- 让模型更稳定地输出指定 JSON schema
- 让模型更熟悉项目内的工具调用模式
- 基于真实反馈优化推荐解释的表达方式
- 降低复杂 prompt 的维护成本

不建议现在微调的原因：

- 产品逻辑还没有稳定，过早微调会固化错误结构
- 外部数据源和工具边界还未最终确定
- 推荐质量更依赖 evidence 和 memory，而不是模型死记音乐知识
- 没有足够数据和 eval 时，很难证明微调有效

结论：SFT / LoRA 是后期增强项，不是当前重构的前置条件。

## 建议优先级

1. 先定新架构和 API，不急着改代码。
2. 把本地数据重新定义为 memory / cache / trajectory。
3. 设计新的 agent tools 和外部 provider adapter。
4. 再拆 `L6`，隐藏 L1-L7 内部概念。
5. 最后重做前端交互和推荐理由展示。
