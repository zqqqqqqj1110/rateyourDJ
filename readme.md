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
| feedback_memory | object[] | 后续推荐反馈 | 已有存储框架，当前不参与首次推荐 | 后续根据喜欢、收藏、跳过调整推荐 |
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

重新安装命令入口并开始采集：

```bash
python -m pip install -e .
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
rateyourdj-collect album batch-1 --user-id demo-user
```

第二批 10 张专辑、共 139 首：

```bash
rateyourdj-collect album batch-2 --user-id demo-user
```

目前 `all` 会采集全部 18 张专辑、共 284 首。

例如：

```bash
rateyourdj-collect album frank-sinatra-in-the-wee-small-hours --user-id demo-user
rateyourdj-collect album sly-and-the-family-stone-theres-a-riot-goin-on --user-id demo-user
rateyourdj-collect album elvis-costello-this-years-model-expanded --user-id demo-user
rateyourdj-collect album bob-dylan-1963 --user-id demo-user
rateyourdj-collect album the-who-tommy --user-id demo-user
rateyourdj-collect album creedence-clearwater-revival-green-river --user-id demo-user
rateyourdj-collect album elton-john-goodbye-yellow-brick-road-expanded --user-id demo-user
```

采集结果：

```text
data/song_profiles/pink-floyd-the-wall-01-in-the-flesh-question.json
...
data/song_profiles/pink-floyd-the-wall-26-outside-the-wall.json
data/user_profiles/demo-user.json
```

批量任务会采集 Spotify metadata、MusicBrainz metadata 和 Last.fm tags，
调用 L2 完成版本匹配、genre 标准化、置信度计算与落盘，随后更新 L1 的
`collection_song_ids` 及 artist、genre、tag preferences。



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
- feedback 调权和当前自然语言需求不属于本版 L4，后续在字段契约明确后接入。

基础分：

```text
BaseScore =
0.50 * L3Similarity
+ 0.08 * ArtistPreference
+ 0.14 * GenrePreference
+ 0.18 * TagPreference
+ 0.10 * ProfileQuality
```

其中 genre 和 tag 偏好使用带权 Jaccard；artist 使用标准化后的精确匹配；
`ProfileQuality` 直接使用 L2 `confidence_score`，缺失时按 0 计算。

L4 按贪心方式逐首选择歌曲。每次选择时，候选会与已进入结果的歌曲比较，
使用 artist、genres 和 tags 计算最高重复度：

```text
DiversitySimilarity =
0.20 * SameArtist
+ 0.40 * GenreSimilarity
+ 0.40 * TagSimilarity

FinalScore =
max(0, BaseScore - 0.15 * MaxDiversitySimilarity)
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

输出中的 `score_breakdown` 五项相加等于 `base_score`；
`final_score` 等于基础分减去当前列表产生的 `diversity_penalty`。



## L5 Agent Tool Layer

将L1-L4写为agent可调用的工具包

| 工具名称             | 对应模块     | 输入                                             | 输出                                      | 作用               |
| -------------------- | ------------ | ------------------------------------------------ | ----------------------------------------- | ------------------ |
| parse_user_intent    | L6 / 解析工具 | user_query                                       | short_term_intent                         | 解析用户当前需求   |
| get_user_profile     | L1           | user_id                                          | collection_song_ids、artist/genre/tag preferences | 获取用户收藏画像   |
| search_song_metadata | L2           | title / artist / query                           | song profile                              | 查询歌曲信息       |
| retrieve_candidates  | L3           | user_id、top_k、过滤参数                         | candidate_songs                           | 召回候选歌曲       |
| rank_candidates      | L4           | user_id、top_k、候选池与多样性参数               | ranked_songs                              | 对候选歌曲排序     |
| play_song            | 外部音乐 API | song_id                                          | playback_url / playback_status            | 播放或返回播放链接 |
| collect_feedback     | L7           | user_id、song_id、feedback_type                  | feedback_record                           | 记录用户反馈       |
| import_profile_dictionary | L1      | L6 / L7 生成的画像字典                           | updated_user_profile                      | 校验、合并并保存画像 |



## L6 ReAct Agent

根据用户 query 判断任务类型，选择合适的工具调用流程，完成歌曲推荐、相似歌曲查找、播放、反馈处理和推荐解释。

一个任务调度器，通过用户的回答选择合适的工具

流程如下

```
User Query
		↓
Task Classification
		↓
Tool Planning
		↓
Tool Execution
		↓
Observation
		↓
Result Integration
		↓
Final Answer
```

还需要有一个任务路由器

| 组成部分            | 作用                       | 输入                               | 输出                      |
| ------------------- | -------------------------- | ---------------------------------- | ------------------------- |
| Task Router         | 判断用户请求类型           | user_query、session_state          | task_type                 |
| Tool Planner        | 选择工具调用顺序           | task_type、available_tools         | tool_plan                 |
| Tool Executor       | 执行 L5 工具               | tool_plan、tool_args               | tool_results              |
| Observation Handler | 读取工具结果并决定是否继续 | tool_results                       | next_action / final_ready |
| Response Generator  | 生成推荐回答               | ranked_songs、ranking_reason       | final_answer              |
| Tool Logger         | 保存工具调用轨迹           | tool_calls、tool_results、feedback | trajectory_log            |
| Session Memory      | 保存当前会话状态           | last_query、last_recommendations   | updated_session_state     |

处理：
首先通过 Task Router 判断用户请求类型，例如普通推荐、相似歌曲推荐、场景推荐、反馈更新、播放请求或推荐解释；
然后由 Tool Planner 选择合适的工具调用链；
Tool Executor 调用 L5 中的用户画像、歌曲检索、候选召回、排序、播放和反馈工具；
最后由 Response Generator 结合排序结果和推荐理由生成自然语言回答。
系统同时保存 tool trajectory，用于后期 SFT 和 GRPO 训练数据积累。

输出：
task_type、tool_plan、tool_results、final_answer、tool_trajectory 和 updated_session_state。

## L7 反馈与个性化奖励

负责把用户的播放、收藏、跳过、喜欢/不喜欢等行为转化为 reward，并用这些 reward 更新用户画像、推荐权重和后期训练数据。

流程

```
用户输入
↓
L6 Agent 调用工具
↓
L3 召回
↓
L4 排序
↓
系统推荐歌曲
↓
用户反馈
↓
L7 记录反馈、计算 reward、更新画像
↓
下一次推荐变得更个性化
```

输入：

| 输入         | 来源                     | 例子                                      |
| ------------ | ------------------------ | ----------------------------------------- |
| 用户行为反馈 | 前端 / 播放器 / 用户操作 | like、dislike、skip、favorite             |
| 推荐上下文   | L6 / L4                  | 当时用户 query、推荐列表、rank            |
| 歌曲特征     | L2                       | genre、mood、instrument、sound_texture    |
| 用户旧画像   | L1                       | long_term_preference、negative_preference |
| 工具调用轨迹 | L6                       | tool_trajectory、ranked_songs             |

输出：

```
{
  "feedback_record": {},
  "reward_score": 0.8,
  "updated_user_profile": {},
  "training_trajectory": {}
}
```

| 输出                 | 用途                   |
| -------------------- | ---------------------- |
| feedback_record      | 保存原始用户反馈       |
| reward_score         | 把行为转成数值奖励     |
| updated_user_profile | 更新长期偏好和负向偏好 |
| training_trajectory  | 后期用于 SFT / GRPO    |

针对feedback_record

| 反馈类型      | 含义             | reward 建议 |
| ------------- | ---------------- | ----------- |
| like          | 用户点击喜欢     | +1.0        |
| favorite      | 用户收藏歌曲     | +0.8        |
| playlist_add  | 加入歌单         | +1.2        |
| play_complete | 完整播放         | +0.6        |
| replay        | 重复播放         | +0.7        |
| normal_play   | 普通播放一段时间 | +0.2        |
| skip          | 普通跳过         | -0.4        |
| quick_skip    | 快速跳过         | -0.8        |
| dislike       | 明确不喜欢       | -1.0        |

针对奖励函数

```
Reward =
w1 * like
+ w2 * favorite
+ w3 * playlist_add
+ w4 * play_complete
+ w5 * replay
- w6 * skip
- w7 * quick_skip
- w8 * dislike
```

针对用户画像更新,假设有

```
{
  "genres": ["britpop"],
  "moods": ["nostalgic", "warm"],
  "instruments": ["acoustic_guitar"],
  "vocal_styles": ["forward_vocal"],
  "sound_textures": ["guitar_driven"]
}
```

可都加0.8或-0.8

针对上下文理解，需保存在feedback_record中

最终，需保存三类数据

1. Raw Feedback

```
{
  "feedback_id": "fb_001",
  "user_id": "user_001",
  "song_id": "song_018",
  "feedback_type": "favorite",
  "reward": 0.8,
  "context_query": "推荐几首像 Wonderwall 的歌",
  "rank_position": 1,
  "timestamp": "2026-06-03T21:10:00"
}
```



1. Profile Update Log

```
{
  "user_id": "user_001",
  "feedback_id": "fb_001",
  "updated_fields": {
    "genres.britpop": 0.08,
    "moods.nostalgic": 0.08,
    "instruments.acoustic_guitar": 0.08
  }
}
```



1. Training Trajectory

```
{
  "user_query": "推荐几首像 Wonderwall 的英文歌",
  "task_type": "reference_song_recommendation",
  "tool_trajectory": [
    "parse_user_intent",
    "get_user_profile",
    "search_song_metadata",
    "retrieve_candidates",
    "rank_candidates"
  ],
  "ranked_songs": ["song_018", "song_034"],
  "feedback": {
    "song_018": "favorite",
    "song_034": "skip"
  },
  "reward": {
    "song_018": 0.8,
    "song_034": -0.4
  }
}
```

所需模块

| 子模块                      | 输入                                             | 输出                        | 作用                             |
| --------------------------- | ------------------------------------------------ | --------------------------- | -------------------------------- |
| Feedback Collector          | user_id、song_id、feedback_type                  | raw_feedback                | 记录用户行为                     |
| Reward Calculator           | feedback_type、play_time、rank_position、context | reward_score                | 将反馈转成奖励分数               |
| Profile Updater             | reward_score、song_features、old_profile         | updated_user_profile        | 更新长期偏好和负向偏好           |
| Negative Preference Updater | negative_feedback、song_features                 | updated_negative_preference | 强化用户不喜欢的特征             |
| Trajectory Logger           | query、tool_calls、ranked_songs、feedback        | training_trajectory         | 保存后期 SFT / GRPO 数据         |
| Evaluation Logger           | feedback、recommendation_result                  | metrics_log                 | 计算收藏率、跳过率、完成率等指标 |



## L8 SFT+GRPO 训练

输入：L7长期保存的user_query，tool_trajectory，ranked_songs，feedback，reward

先进入SFT层，优化Agent的工具调用能力和任务流程稳定性（使用tool_trajectory）

后进入GRPO层，优化Agent的推荐决策能力（使用L7 生成的reward）





## L9 评估与部署

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

## L10 前端（如果有）

我问一个然后ai给我一段推荐的理由，和歌曲的试听，试听可选择加入到我的歌单，跳过等选择，账号独享奖励函数
