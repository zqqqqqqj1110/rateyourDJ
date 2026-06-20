# rateyourDJ

本项目当前目标是：读取用户收藏歌单，为收藏歌曲建立 metadata、tags 和 genres 画像，并推荐尚未收藏的相似歌曲。

## 1. 总体流程

```
读取用户收藏歌单
    ↓
获取 Spotify / MusicBrainz metadata
    ↓
获取 Last.fm track tags / artist tags
    ↓
Genre Normalizer 生成标准 genres
    ↓
建立收藏歌曲画像
    ↓
召回候选歌曲
    ↓
计算歌曲相似度并过滤已收藏歌曲
    ↓
返回推荐结果
    ↓
收集反馈并优化后续排序
```

## 1. L1 用户收藏画像模块

当前目标不是通用的自然语言场景推荐，而是：

> 根据用户收藏歌单中的歌曲，推荐风格和标签相似的新歌曲。

因此 L1 只保存用户收藏歌曲集合，以及由收藏歌曲聚合得到的偏好。

| 字段 | 类型 | 来源 | 当前状态 | 用途 |
| --- | --- | --- | --- | --- |
| user_id | string | 系统 | 已有框架 | 用户唯一标识 |
| collection_song_ids | string[] | 专辑采集或后续 Spotify 收藏导入 | **已支持写入，Spotify 收藏导入待接入** | 作为相似推荐的种子歌曲集合 |
| artist_preferences | `{artist: weight}` | 收藏歌曲 metadata 聚合 | **已实现** | 表示用户收藏歌手分布 |
| genre_preferences | `{genre: weight}` | 收藏歌曲 genres 聚合 | **已实现** | 表示用户收藏流派分布 |
| tag_preferences | `{tag: weight}` | 收藏歌曲 Last.fm tags 聚合 | **已实现** | 表示用户收藏歌曲的社区标签分布 |
| feedback_memory | object[] | L5 推荐反馈 | **已接入 L4** | 根据喜欢、收藏、跳过调整排序 |
| version | integer | 系统 | 已有框架 | 画像版本 |
| updated_at | string | 系统 | 已有框架 | 更新时间 |

目标 JSON 结构：

```json
{
  "user_id": "demo-user",
  "collection_song_ids": [],
  "artist_preferences": {},
  "genre_preferences": {},
  "tag_preferences": {},
  "feedback_memory": [],
  "version": 1,
  "updated_at": ""
}
```

说明：

- `collection_song_ids` 是 L1 最重要的原始输入。
- 三类 preference 不由 L1 自己推断，而是由收藏歌曲的 L2 画像聚合后迁入。
- 当前 L1 代码已按本表同步。

### L1 实现状态

| 能力 | 状态 |
| --- | --- |
| JSON 校验、迁入、合并和持久化 | 已实现 |
| 新版收藏画像字段 | 已同步到代码 |
| Spotify 收藏歌单采集 | 待实现 |
| 从收藏歌曲聚合 artist / genre / tag preferences | 已实现 |

## L2 歌曲相似度画像模块

L2 保存计算歌曲相似度所需的 metadata、Last.fm 原始标签和标准化 genres。

| 字段 | 类型 | 来源 | 当前状态 | 用途 |
| --- | --- | --- | --- | --- |
| song_id | string | 系统 | 已有框架 | 项目内部歌曲唯一标识 |
| spotify_track_id | string/null | Spotify | **已验证可采集** | Spotify 查询和歌单关联 |
| musicbrainz_recording_id | string/null | MusicBrainz | **已验证可采集** | MusicBrainz 查询和跨平台匹配 |
| title | string | Spotify、MusicBrainz | **已验证可采集** | 歌名匹配和展示 |
| artist | string | Spotify、MusicBrainz | **已验证可采集** | 歌手匹配和相似度特征 |
| album | string | Spotify、MusicBrainz | **已验证可采集** | 版本识别和展示 |
| release_year | integer/null | Spotify、MusicBrainz | **已验证可采集** | 发行年代相似度 |
| duration_ms | integer/null | Spotify、MusicBrainz | **已验证可采集** | 版本匹配和辅助校验 |
| version_type | string/null | 系统识别 | **已实现** | 标记 `remastered`、`original`、`live`、`cover` 或 `unknown` |
| track_tags | `{tag: weight}` | Last.fm `track.getTopTags` | **已验证可采集** | 最重要的歌曲级相似度特征 |
| artist_tags | `{tag: weight}` | Last.fm `artist.getTopTags` | **已验证可采集** | 歌曲标签不足时的补充特征 |
| genres | `{genre: weight}` | Last.fm tags 清洗分类 | **已实现** | 标准化流派相似度 |
| data_source | object | 系统记录 | **已实现** | 记录各字段来源 |
| confidence_score | number/null | 系统计算 | **已实现** | 表示跨源匹配、metadata 完整度和标签结果可信度 |
| version | integer | 系统 | 已有框架 | 歌曲画像版本 |
| updated_at | string | 系统 | 已有框架 | 更新时间 |

目标 JSON 结构：

```json
{
  "song_id": "song-001",
  "external_ids": {
    "spotify_track_id": null,
    "musicbrainz_recording_id": null
  },
  "metadata": {
    "title": "",
    "artist": "",
    "album": "",
    "release_year": null,
    "duration_ms": null,
    "version_type": null
  },
  "source_tags": {
    "lastfm_track_tags": {},
    "lastfm_artist_tags": {}
  },
  "genres": {},
  "data_source": {},
  "confidence_score": null,
  "version": 1,
  "updated_at": ""
}
```

数据处理流程：

```text
Spotify + MusicBrainz metadata
        ↓
歌曲版本匹配：重制版 > 原版 > 现场版/翻唱版
        ↓
Last.fm track tags + artist tags
        ↓
原始标签保存到 source_tags
        ↓
Genre Normalizer 清洗、分类、加权
        ↓
生成 genres
        ↓
计算 confidence_score
        ↓
写入 data/song_profiles/<song_id>.json
```

说明：

- 当前 Spotify、MusicBrainz 和 Last.fm 都已通过单曲 smoke test。
- L2 schema、跨源匹配、Genre Normalizer、三源合并、置信度计算和 JSON 落盘已实现。
- 正式批量采集器不属于本阶段范围；当前合并器接收采集结果字典或候选字典列表。
- `confidence_score` 衡量当前歌曲画像的数据质量，不是 L3 的歌曲相似度分数。

三源 JSON 合并并写入存储：

```bash
rateyourdj-l2 merge-sources wonderwall-oasis \
  --spotify spotify.json \
  --musicbrainz musicbrainz.json \
  --lastfm lastfm.json
```

输出文件：

```text
data/song_profiles/wonderwall-oasis.json
```

### L2 实现状态

| 能力 | 状态 |
| --- | --- |
| JSON 校验、迁入、合并和持久化 | 已实现 |
| Spotify metadata 单曲采集 | 已通过 smoke test |
| MusicBrainz metadata 单曲采集 | 已通过 smoke test |
| Last.fm track tags / artist tags 单曲采集 | 已通过 smoke test |
| 精简后的歌曲相似度 schema | 已实现 |
| 重制版 > 原版 > 现场版/翻唱版匹配 | 已实现 |
| Genre Normalizer | 已实现 |
| 三源数据合并 | 已实现 |
| 置信度计算 | 已实现 |
| 合并结果写入 L2 store | 已实现 |
| 《The Wall》正式批量采集器 | 已实现 |

### 《The Wall》本地数据集

项目提供正式批量采集命令，按专辑标准曲序采集 Pink Floyd 的
`The Wall` 共 26 首歌曲。原始输入清单中的重复曲目会被去重，并补全
`Another Brick in the Wall, Part 2` 和 `Part 3`。

先配置三个数据源：

```bash
export SPOTIFY_CLIENT_ID="..."
export SPOTIFY_CLIENT_SECRET="..."
export LASTFM_API_KEY="..."
```

重新安装命令入口并开始采集候选库：

```bash
python -m pip install -e .
rateyourdj-collect album pink-floyd-the-wall
```

默认只写入 L2 候选库。只有这些歌曲确实属于某个用户收藏时，才显式传入
`--user-id`：

```bash
rateyourdj-collect album pink-floyd-the-wall --user-id demo-user
```

当前支持的专辑 key：

```text
frank-sinatra-in-the-wee-small-hours
sly-and-the-family-stone-theres-a-riot-goin-on
elvis-costello-this-years-model-expanded
bob-dylan-1963
the-who-tommy
creedence-clearwater-revival-green-river
elton-john-goodbye-yellow-brick-road-expanded
pink-floyd-the-wall
```

第一批 8 张专辑、共 145 首：

```bash
rateyourdj-collect album batch-1
```

第二批 10 张专辑、共 139 首：

```bash
rateyourdj-collect album batch-2
```

目前 `all` 会采集全部 18 张专辑、共 284 首。

例如：

```bash
rateyourdj-collect album frank-sinatra-in-the-wee-small-hours
rateyourdj-collect album sly-and-the-family-stone-theres-a-riot-goin-on
rateyourdj-collect album elvis-costello-this-years-model-expanded
rateyourdj-collect album bob-dylan-1963
rateyourdj-collect album the-who-tommy
rateyourdj-collect album creedence-clearwater-revival-green-river
rateyourdj-collect album elton-john-goodbye-yellow-brick-road-expanded
```

采集结果：

```text
data/song_profiles/pink-floyd-the-wall-01-in-the-flesh-question.json
...
data/song_profiles/pink-floyd-the-wall-26-outside-the-wall.json
data/user_profiles/demo-user.json
```

批量任务会采集 Spotify metadata、MusicBrainz metadata 和 Last.fm tags，
调用 L2 完成版本匹配、genre 标准化、置信度计算与落盘。传入 `--user-id`
时，才会同时更新该用户 L1 的 `collection_song_ids` 及
artist、genre、tag preferences。

手工调整收藏 ID 后，使用以下命令从当前收藏重新构建偏好：

```bash
rateyourdj-collect rebuild-profile demo-user
```



## L3 相似歌曲召回模块

L3 从用户收藏歌曲出发寻找相似歌曲，并排除用户已经收藏的歌曲。

### L3 实现状态

- 已实现本地 L2 候选库扫描。
- 已实现 track tags、genres、artist tags 和 release year 加权相似度。
- 已实现收藏歌曲、external ID 和重复版本过滤。
- 已实现多种子结果合并、同歌手数量限制和 Top-K 截断。
- 缺少 L2 JSON 的收藏 song_id 会出现在 `missing_seed_song_ids`，不会中断召回。
- Last.fm similar tracks 在线召回尚未接入。

运行本地召回：

```bash
rateyourdj-l3 retrieve demo-user --top-k 20
```

未安装命令行入口时：

```bash
PYTHONPATH=src python3 -m rateyourdj.l3.cli retrieve demo-user --top-k 20
```

查看输出 schema：

```bash
rateyourdj-l3 schema
```

### 输入字段

| 字段 | 来源 | 当前状态 | 用途 |
| --- | --- | --- | --- |
| collection_song_ids | L1 | **已支持** | 相似推荐种子集合 |
| seed_song_profiles | L2 | **已支持** | 收藏歌曲的 metadata、tags 和 genres |
| candidate_song_profiles | L2 | **已支持本地候选库** | 被比较的候选歌曲画像 |

### 召回与过滤

| 步骤 | 使用字段 | 当前状态 | 说明 |
| --- | --- | --- | --- |
| Similar Track Retrieval | 本地候选歌曲库 | **已实现** | 扫描本地 L2 候选 |
| Track Tag Similarity | track_tags | **已实现** | 第一版主要相似度信号 |
| Genre Similarity | genres | **已实现** | 比较标准化流派 |
| Artist Tag Similarity | artist_tags | **已实现** | 作为歌曲级标签的补充 |
| Artist Limit | artist | **已实现** | 避免结果被同一歌手占满 |
| Release Era Similarity | release_year | 可选，已能采集 | 小权重比较发行年代 |
| Collection Filter | song_id / external IDs | **已实现** | 排除用户已经收藏的歌曲 |
| Duplicate Version Filter | title、artist、duration_ms | **已实现** | 合并原版、重制版和现场版等重复结果 |
| Last.fm Similar Tracks | 在线 API | 待实现 | 扩展本地候选库以外的召回 |

第一版相似度建议：

```text
Similarity =
0.55 * TrackTagSimilarity
+ 0.25 * GenreSimilarity
+ 0.15 * ArtistTagSimilarity
+ 0.05 * ReleaseEraSimilarity
```

输出字段：

```json
{
  "candidate_song_id": "song-002",
  "best_seed_song_id": "song-001",
  "matched_seed_song_ids": ["song-001", "song-003"],
  "best_seed_score": 0.86,
  "top_seed_average_score": 0.73,
  "similarity_score": 0.82,
  "score_breakdown": {
    "track_tags": 0.48,
    "genres": 0.21,
    "artist_tags": 0.10,
    "release_year": 0.03
  },
  "retrieval_sources": ["lastfm_similar_tracks"]
}
```

候选的最终分数用于衡量整体收藏偏好：

```text
Similarity =
0.70 * BestSeedScore
+ 0.30 * Top5SeedAverageScore
```

候选仍会与全部收藏种子逐一比较。`BestSeedScore` 保留强相似歌曲，
`Top5SeedAverageScore` 衡量候选是否同时符合多首收藏歌曲。种子不足 5 首时，
使用全部有效种子计算平均值。

## L4 推荐排序

L4 消费现有 L1、L2 和 L3 数据，不依赖当前 schema 中不存在的 short-term
intent 或 negative preference。它先让 L3 返回一个较大的候选池，再根据用户
收藏偏好、歌曲画像质量和列表多样性重排为最终 Top-K。

### L4 实现状态

- 已实现 L3 候选池自动召回。
- 已实现 artist、genre 和 tag 三类 L1 偏好匹配。
- 已实现 L2 `confidence_score` 质量分。
- 已实现基于 artist、genres 和 tags 的贪心多样性重排。
- 已实现每位歌手数量限制、Top-K 和最低 L3 分数参数。
- 已实现完整分数拆解、排序原因和缺失候选记录。
- 已接入 L5 feedback 正负调整项。

基础分：

```text
BaseScore =
0.50 * L3Similarity
+ 0.08 * ArtistPreference
+ 0.14 * GenrePreference
+ 0.18 * TagPreference
+ 0.10 * ProfileQuality
+ 0.15 * FeedbackScore
```

其中 genre 和 tag 偏好使用带权 Jaccard；artist 使用标准化后的精确匹配；
`ProfileQuality` 直接使用 L2 `confidence_score`，缺失时按 0 计算。
`FeedbackScore` 位于 `[-1, 1]`，因此可以提升或降低候选分数。

L4 按贪心方式逐首选择歌曲。每次选择时，候选会与已进入结果的歌曲比较，
使用 artist、genres 和 tags 计算最高重复度：

```text
DiversitySimilarity =
0.20 * SameArtist
+ 0.40 * GenreSimilarity
+ 0.40 * TagSimilarity

FinalScore =
clamp(BaseScore - 0.15 * MaxDiversitySimilarity, 0, 1)
```

运行排序：

```bash
rateyourdj-l4 rank demo-user --top-k 20
```

未安装命令入口时：

```bash
PYTHONPATH=src python3 -m rateyourdj.l4.cli rank demo-user --top-k 20
```

显式设置候选池和歌手上限：

```bash
PYTHONPATH=src python3 -m rateyourdj.l4.cli rank demo-user \
  --top-k 20 \
  --candidate-pool-size 100 \
  --max-per-artist 2 \
  --min-retrieval-score 0.05
```

默认候选池大小为 `top_k * 5`。查看输出 schema：

```bash
PYTHONPATH=src python3 -m rateyourdj.l4.cli schema
```

输出中的 `score_breakdown` 六项相加等于 `base_score`；
`final_score` 等于基础分减去当前列表产生的 `diversity_penalty`。

## L5 反馈闭环

L5 将用户行为转换为 `[-1, 1]` reward，写入 L1 `feedback_memory`，并为
L4 生成正负反馈调整分。

| 反馈类型 | 默认 reward |
| --- | ---: |
| `play` | 0.1 |
| `play_complete` | 0.4 |
| `replay` | 0.5 |
| `like` | 0.6 |
| `favorite` | 0.8 |
| `playlist_add` | 1.0 |
| `skip` | -0.4 |
| `quick_skip` | -0.8 |
| `dislike` | -1.0 |

记录反馈：

```bash
rateyourdj-l5 record demo-user <song-id> like
```

可通过 JSON 保存当时的推荐上下文：

```bash
rateyourdj-l5 record demo-user <song-id> skip \
  --context-json recommendation-context.json
```

查看反馈摘要和指定歌曲的反馈分：

```bash
rateyourdj-l5 summary demo-user
rateyourdj-l5 score demo-user <song-id>
```

未安装命令入口时：

```bash
PYTHONPATH=src python3 -m rateyourdj.l5.cli schema
PYTHONPATH=src python3 -m rateyourdj.l5.cli summary demo-user
```

L5 对同一候选的直接反馈使用最近一次记录。没有直接反馈时，根据历史反馈
歌曲与候选的 artist、genres 和 tags 相似度衰减传播 reward；相似度低于
`0.30` 时不传播。L4 将该结果乘以 `0.15` 写入
`score_breakdown.feedback_adjustment`。

`favorite` 和 `playlist_add` 会把歌曲加入 L1 收藏并重新聚合收藏偏好，因此
L3 下次会过滤这些歌曲。其他反馈保持为独立行为信号，避免一次 skip 永久
污染 L1 的长期收藏画像。

### L1-L5 Agent 工具接口

L1-L5 额外提供统一的 `ToolObservation`，包含 `status`、`data`、
`diagnostics`、`retryable` 和 `suggested_actions`。当前工具包括：

```text
L1.inspect_user_profile
L2.inspect_song_profile
L3.retrieve_candidates
L4.rank_candidates
L5.inspect_feedback_state
L5.record_feedback
```

L3/L4 候选不足时会返回扩大候选池、降低阈值或放宽歌手限制等建议，供后续
L6 执行循环判断是否重试。

网页层会把 L5 feedback/reward 同时写入 L1 `feedback_memory` 和对应 L6
trajectory 的 `feedback_events`。未知 trajectory ID 会在写入 L1 前被拒绝。

### 生成式发现工具 discover_tracks

`discover_tracks` 是把项目从"本地候选库检索"升级为"LLM-as-DJ 发现式推荐"的
核心工具。流程是:

```text
用户画像 + 本轮意图
    ↓
DeepSeek 用音乐知识生成候选（artist + title + 理由），通常多生成一倍
    ↓
对每个候选调用 Spotify 等 provider 做事实落地（grounding）
    ├─ 命中：保留，补全真实 track_id / 试听 / 专辑 / 年份
    └─ 未命中：丢弃（自动过滤模型幻觉）
    ↓
返回 grounded 候选，并附 generated / grounded / dropped / hallucination_rate
```

要点:

- 候选不再被锁死在本地 284 首,而是 DeepSeek 知识范围内、且 Spotify 可验证的
  任何歌曲。本地 `song_profiles` 降级为缓存。
- `dropped / generated` 即"幻觉率",是可观测、可进 eval 的指标。
- 未配置 `DEEPSEEK_API_KEY` 时,发现引擎自动降级为
  `TasteSeedTrackGenerator`(直接用用户收藏作为候选),保证无密钥也能跑通
  grounding 链路。
- 工具实现位于 `src/rateyourdj/domain/discovery.py` 和
  `domain/generators.py`,通过 `AgentToolRegistryV1.default(..., track_generator=...)`
  注册为模型可调用的 `discover_tracks` 工具。

运行测试:

```bash
PYTHONPATH=src python3 -m unittest tests.test_discovery -v
```

## L6 Agent 编排层规划


L6 定义为当前 L1-L5 之上的自然语言 Agent 编排层，不直接承担模型训练。
第一版已实现：

- 接收自然语言推荐请求。
- 解析推荐数量、流派偏好、排除词、相似度要求和歌手多样性。
- 先调用 L1 检查画像，再通过工具注册表动态调用 L4/L3。
- 候选不足时消费工具返回的 `retryable` 和 `suggested_actions`，校验后重新执行。
- 根据 L4 分数拆解和排序原因生成可追溯的推荐解释。
- 保存计划、完整工具 observation、决策、停止原因和后续反馈 trajectory。
- 使用 session 支持“换一批”，继承上一轮条件并排除已展示歌曲。
- 向网页提供聊天式请求接口。

L6 现在同时提供可替换的 `LLMProvider` 接口和受控模型 Agent loop。当前仓库
已提供 DeepSeek 官方 API adapter；未配置 `DEEPSEEK_API_KEY` 时继续使用规则
解析器。启用 provider 后，模型最多执行 5 个结构化决策，并可见以下只读工具：

```text
L1.inspect_user_profile
L2.inspect_song_profile
L3.retrieve_candidates
L4.rank_candidates
L5.inspect_feedback_state
```

模型只能提交结构化 tool call、请求条件补丁和公开决策摘要。程序继续强制
校验用户 ID、歌曲数量、排除项、相似度、歌手多样性和已展示歌曲；provider
异常、未知工具或非法参数会自动降级到原有规则循环。重复请求更新和重复工具
调用会被忽略；如果模型在 5 步内没有调用 L4，程序会执行一次经过校验的 L4
排序，避免 Agent 空转后返回空列表。

运行自然语言推荐：

```bash
PYTHONPATH=src python3 -m rateyourdj.l6.cli recommend demo-user \
  '推荐 5 首多样一点的摇滚，不要“Pink Floyd”'
```

可显式选择执行模式：

```bash
PYTHONPATH=src python3 -m rateyourdj.l6.cli \
  --agent-mode model recommend demo-user '推荐 5 首摇滚'
```

当前没有配置 provider，因此 `model` 会安全降级到 `rules`，并在响应和
trajectory 中写入 `fallback_reason`。`auto` 是默认模式。

### DeepSeek 官方 API

密钥只通过环境变量读取，不要写入代码或提交到 Git：

```bash
export DEEPSEEK_API_KEY="你的 API key"
```

可选配置：

```bash
export DEEPSEEK_MODEL="deepseek-chat"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
```

CLI：

```bash
PYTHONPATH=src python3 -m rateyourdj.l6.cli \
  --agent-mode auto \
  --llm-provider auto \
  recommend demo-user '不要 Pink Floyd，推荐 5 首不同歌手的摇滚'
```

网页：

```bash
PYTHONPATH=src python3 -m rateyourdj.web.app \
  --agent-mode auto \
  --llm-provider auto
```

`auto` 在检测到 `DEEPSEEK_API_KEY` 时使用 DeepSeek，否则使用规则 fallback。
`--llm-provider deepseek` 会要求必须配置密钥；`none` 会完全禁用模型。

DeepSeek 请求不会发送真实用户 ID。模型只看到 `current_user` 作用域，工具
执行前由本地程序注入真实 user ID。

trajectory 默认写入：

```text
data/trajectories/<user_id>/<trajectory_id>.json
```

session 默认写入 `data/sessions/<session_id>.json`。

网页接口：

```text
POST /api/chat/<user_id>
{"query": "推荐五首多样一点的摇滚", "session_id": null}
```

后续请求把响应中的 `session_id` 原样传回即可使用“换一批”多轮状态。

网页推荐卡片已接入 Spotify Embed。存在合法 `spotify_track_id` 的歌曲会显示
“试听”按钮；页面同时只保留一个活动播放器。该功能使用 Spotify 托管播放器，
不下载或代理音频。Spotify IFrame 播放事件会作为 L5 feedback 写入 L1，并在
存在 trajectory ID 时回连 L6。SFT/GRPO 属于 L6 积累足够 trajectory 和
reward 数据之后的训练阶段，不作为 L6 MVP 的阻塞项。

## L7 数据导出与离线评估

L7 只读取 L6 trajectory，不修改 L1-L6 的画像、推荐或反馈数据。它提供：

- JSONL 导出：保留 query、工具调用、推荐、解释和反馈，供后续分析或训练。
- CSV 导出：每条 trajectory 一行，方便表格分析。
- 默认用户脱敏：真实 `user_id` 被稳定的 SHA-256 摘要替换。
- 数据过滤：可限定用户，或只导出包含反馈的 trajectory。
- 坏文件隔离：无法解析的 trajectory 会列入 `skipped_files`，不阻断整批任务。
- 离线评估：统计目标达成、数量满足、反馈覆盖、reward、工具调用、回退和
  推荐歌手多样性。

查看 L7 schema：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli schema
```

导出默认脱敏 JSONL：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli export \
  data/exports/trajectories.jsonl
```

导出只包含反馈的 CSV：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli export \
  data/exports/feedback.csv \
  --format csv \
  --feedback-only
```

运行全量或单用户离线评估：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli evaluate

PYTHONPATH=src python3 -m rateyourdj.l7.cli evaluate \
  --user-id demo-user
```

可通过 `RATEYOURDJ_EXPORT_SALT` 设置部署环境专用脱敏盐。只有在受控环境明确
需要原始 ID 时才使用 `--include-user-id`。导出文件默认放在 `data/exports/`，
该目录不会提交到 Git。

L7 指标反映当前已记录数据，不能消除曝光偏差，也不能替代多用户在线 A/B
测试。没有反馈的 trajectory 仍参与推荐和工具指标，但不参与 reward 平均值。

### 合成数据

L7 可以使用现有 L2 歌曲画像生成隔离的合成 trajectory，用于测试导出、评估
和未来训练代码。默认不会写入真实 `data/trajectories`：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli generate-synthetic \
  data/synthetic/trajectories \
  --count 500 \
  --users 25 \
  --seed 20260615 \
  --feedback-rate 0.7
```

生成内容包括多种流派请求、推荐列表、L1/L4 工具 observation、候选不足、
规则与模型模式，以及 `play`、`play_complete`、`like`、`skip`、`favorite`
等反馈。所有用户和 trajectory ID 都使用 `synthetic-` 前缀。输出目录必须为空，
避免误覆盖已有样本。

评估和导出：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli \
  --trajectory-dir data/synthetic/trajectories evaluate

PYTHONPATH=src python3 -m rateyourdj.l7.cli \
  --trajectory-dir data/synthetic/trajectories \
  export data/synthetic/exports/trajectories.jsonl \
  --feedback-only
```

合成数据只能验证数据结构、指标和训练管道，不能用于判断推荐系统真实效果，
也不能替代真实用户反馈进行有效模型训练。

### 按用户切分数据集

训练、验证和测试数据必须按用户切分，不能把同一用户的不同 trajectory 随机
分散到多个集合：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli \
  --trajectory-dir data/synthetic/trajectories \
  split data/synthetic/splits-v1 \
  --train-ratio 0.8 \
  --validation-ratio 0.1 \
  --test-ratio 0.1 \
  --seed 20260615
```

输出 `train.jsonl`、`validation.jsonl`、`test.jsonl` 和 `manifest.json`。
相同 seed 和用户集合会得到相同切分。当前合成数据的实际结果是：

```text
train:      20 users / 400 trajectories
validation:  3 users /  60 trajectories
test:        2 users /  40 trajectories
```

三个集合的用户交集为零。用户数不能精确满足小数比例时，manifest 会记录最接近
目标比例的实际整数分配。

## 本地网页

项目包含一个无需前端构建工具的 Flask 单页界面，支持：

- 查看 L1 收藏画像与 L5 反馈摘要。
- 查看收藏歌曲、专辑和主要流派；缺失 L2 画像时显示缺失数量。
- 通过 L6 自然语言输入生成推荐并保存 trajectory。
- 生成 L4 推荐列表并查看分数拆解。
- 通过 Spotify Embed 试听具有 `spotify_track_id` 的推荐歌曲。
- 将播放开始、播放完成和快速关闭记录到 L5 与 L6 trajectory。
- 提交 `like`、`skip`、`dislike` 和 `favorite`。
- 收藏后立即更新收藏列表，并刷新推荐排序。

安装项目并启动：

```bash
python -m pip install -e . --no-build-isolation
rateyourdj-web
```

开发和测试环境建议安装：

```bash
python -m pip install -e '.[dev]' --no-build-isolation
```

不安装 `pytest` 也可以运行完整测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -q
```

固定 50 条评测集可单独运行：

```bash
PYTHONPATH=src python -m rateyourdj.l7.cli run-eval-suite
```

需要机器可读输出时：

```bash
PYTHONPATH=src python -m rateyourdj.l7.cli run-eval-suite --json
```

标准回归入口会先跑 eval suite，再跑完整单测：

```bash
PYTHONPATH=src python -m rateyourdj.l7.cli run-regression
```

未重新安装命令入口时：

```bash
PYTHONPATH=src python3 -m rateyourdj.web.app
```

浏览器访问：

```text
http://127.0.0.1:8000
```

默认使用 `data/user_profiles` 和 `data/song_profiles`。可指定其他目录：

```bash
rateyourdj-web \
  --profile-dir /path/to/user_profiles \
  --song-dir /path/to/song_profiles \
  --port 8000
```



### 后续 SFT+GRPO

训练应使用长期积累的 user query、tool trajectory、ranked songs、feedback
和 reward。单个用户或少量手工反馈只能用于功能验证，不能支持有效训练。

SFT 可优化 Agent 的工具调用能力和任务流程稳定性。

GRPO 可使用 L5 生成的 reward 优化推荐决策能力。





### 评估与部署

Evaluation Metrics：
Skip Rate
Favorite Rate
Play Completion Rate
User Satisfaction

Agent Metrics：
工具调用成功率
平均响应时间
推荐解释合理性
自验证通过率

对比：
普通推荐系统 vs Agent 推荐
无反馈更新 vs 有反馈更新
无 SFT/GRPO vs 有 SFT/GRPO

### L6 前端目标

网页增加聊天输入框，用户可以用自然语言请求推荐。响应包含推荐理由和歌曲
列表，并继续复用现有喜欢、跳过、不喜欢和收藏反馈。当前已使用 Spotify
Embed 提供试听；没有 `spotify_track_id` 的歌曲不会显示试听按钮。
