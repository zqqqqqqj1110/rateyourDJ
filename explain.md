# L1

## L1 是什么

L1 是本地的用户收藏画像。它不保存每首歌的完整信息，而是保存：

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

字段含义：

| 字段 | 用途 |
| --- | --- |
| `user_id` | 用户标识 |
| `collection_song_ids` | 当前本地收藏歌曲的 L2 song_id |
| `artist_preferences` | 根据全部收藏歌曲聚合的歌手权重 |
| `genre_preferences` | 根据全部收藏歌曲的标准 genres 聚合的流派权重 |
| `tag_preferences` | 根据全部收藏歌曲的 Last.fm tags 聚合的标签权重 |
| `feedback_memory` | 后续保存喜欢、跳过、收藏等推荐反馈 |
| `version` | 每次更新画像时递增 |
| `updated_at` | 最近更新时间 |

## L1 目前完成了什么

- 实现了固定 schema 和字段校验。
- 实现了 JSON 创建、读取、迁入、合并和持久化。
- 支持旧版 L1 JSON 自动迁移到当前结构。
- 批量采集专辑后，自动把成功生成的 L2 song_id 加入
  `collection_song_ids`。
- 每次采集后，根据当前收藏的全部 L2 文件重新计算
  `artist_preferences`、`genre_preferences` 和 `tag_preferences`。
- 默认文件保存在：

```text
data/user_profiles/<user_id>.json
```

`demo-user` 的收藏数量取决于当前测试配置。采集全部专辑后可包含 284 首；
测试 L3 时可以临时只保留少量种子歌曲。

## 检查 L1

查看 L1 接受的 schema：

```bash
rateyourdj-l1 schema
```

创建一个空用户画像：

```bash
rateyourdj-l1 init demo-user
```

`init` 只负责创建或读取画像，不会自动采集歌曲。因此在没有运行采集器时，
列表和偏好为空是正常的。

查看当前用户画像：

```bash
rateyourdj-l1 show demo-user
```

直接查看存储文件：

```bash
cat data/user_profiles/demo-user.json
```

只检查收藏歌曲数量：

```bash
python -c "import json; print(len(json.load(open('data/user_profiles/demo-user.json'))['collection_song_ids']))"
```

如果刚完成全部专辑采集，预期为 284；如果正在测试 L3，则以手动设置的
种子数量为准。

## 调试 L1

运行 L1 单元测试：

```bash
PYTHONPATH=src python -m unittest tests.test_l1 -v
```

验证一个准备迁入 L1 的部分字典：

```bash
rateyourdj-l1 validate path/to/profile_patch.json
```

验证通过后迁入：

```bash
rateyourdj-l1 import demo-user path/to/profile_patch.json
```

如果 `collection_song_ids` 数量少于预期：

1. 检查采集命令输出中的 `stored_tracks`。
2. 检查每张专辑输出中的 `failures`。
3. 检查对应 L2 JSON 是否确实存在。
4. 重新运行失败的单张专辑，L1 会基于全部已有 L2 文件重新聚合。

# L2

## L2 是什么

L2 是本地歌曲画像数据集。每首歌曲对应一个 JSON：

```text
data/song_profiles/<song_id>.json
```

主要结构：

```json
{
  "song_id": "internal-song-id",
  "external_ids": {
    "spotify_track_id": null,
    "musicbrainz_recording_id": null
  },
  "metadata": {
    "title": null,
    "artist": null,
    "album": null,
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

## L2 目前完成了什么

- Spotify 采集歌曲 metadata 和 Spotify track ID。
- MusicBrainz 补充 metadata 和 recording ID。
- Last.fm 采集 track tags 和 artist tags。
- 跨平台检查歌名和歌手是否一致。
- 版本选择顺序为：

```text
重制版 > 原版 > 现场版/翻唱版
```

- Genre Normalizer 将 Last.fm 社区标签清洗为标准 genres。
- 过滤年代、地点、歌手名和 `seen live` 等非 genre 标签。
- 合并三个数据源为统一 L2 SongProfile。
- 记录字段的数据来源。
- 计算 `confidence_score`。它表示数据质量，不是歌曲相似度。
- 自动写入 `data/song_profiles/<song_id>.json`。
- 外部 API 请求最多重试三次；单个来源失败不会终止整张专辑。
- 批量采集默认只扩充 L2 候选库；显式传入 `--user-id` 时才更新 L1。

目前登记了 18 张专辑、284 首歌曲：

```text
batch-1：8 张，145 首
batch-2：10 张，139 首
all：18 张，284 首
```

## 采集 L2

先激活环境并配置 API 凭证：

```bash
conda activate rateyourDJ
export SPOTIFY_CLIENT_ID="..."
export SPOTIFY_CLIENT_SECRET="..."
export LASTFM_API_KEY="..."
```

如果修改过 `pyproject.toml` 或命令不存在，重新安装：

```bash
python -m pip install -e . --no-build-isolation
```

只采集第一批：

```bash
rateyourdj-collect album batch-1
```

只采集第二批：

```bash
rateyourdj-collect album batch-2
```

采集全部登记专辑：

```bash
rateyourdj-collect album all
```

上述命令只构建候选库。若采集内容确实是用户收藏，再显式指定：

```bash
rateyourdj-collect album pink-floyd-the-wall --user-id demo-user
```

成功完成全部采集时，汇总应包含：

```text
requested_albums: 18
requested_tracks: 284
stored_tracks: 284
```

`stored_tracks` 表示歌曲至少有一个数据源成功。还要检查各专辑的
`failures`：空数组表示三个来源均未报告错误。

## 检查 L2

查看 L2 schema：

```bash
rateyourdj-l2 schema
```

创建一个空歌曲框架：

```bash
rateyourdj-l2 init demo-song
```

这里生成空字段是正常的，因为 `init` 不调用采集器。

查看指定歌曲：

```bash
rateyourdj-l2 show <song_id>
```

查看已生成的 L2 文件数量：

```bash
find data/song_profiles -maxdepth 1 -name "*.json" | wc -l
```

当前目录可能显示 285：其中 284 个是真实采集歌曲，另一个是此前用于检查
空 schema 的 `demo-song.json`。L2 文件总数是候选库规模，不等于当前用户
的收藏数量；收藏规模应以 L1 的 `collection_song_ids` 为准。

手工修改收藏 ID 后必须同步重建偏好：

```bash
rateyourdj-collect rebuild-profile demo-user
```

检查某个真实 L2 文件：

```bash
find data/song_profiles -name "*.json" ! -name "demo-song.json" | head -1
```

然后执行：

```bash
rateyourdj-l2 show <上一步文件名中的song_id>
```

真实歌曲文件应重点检查：

- `external_ids` 是否至少有一个 ID。
- `metadata.title`、`metadata.artist` 是否正确。
- `metadata.version_type` 是否符合版本。
- `source_tags` 是否包含 Last.fm 标签。
- `genres` 是否完成标准化。
- `data_source` 是否记录字段来源。
- `confidence_score` 是否在 0 到 1 之间。

## 调试 L2

运行 L2 单元测试：

```bash
PYTHONPATH=src python -m unittest tests.test_l2 -v
```

运行采集器离线测试：

```bash
PYTHONPATH=src python -m unittest tests.test_collectors -v
```

运行全部测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

联网 smoke test 默认跳过。需要单独验证真实 API 时：

```bash
RUN_LIVE_API_TESTS=1 python -m unittest \
  tests.test_data_collection_smoke -v
```

常见问题：

| 问题 | 含义和处理 |
| --- | --- |
| `command not found: rateyourdj-l2` | 运行 `python -m pip install -e . --no-build-isolation` |
| `missing environment variables` | 当前终端没有配置 Spotify 或 Last.fm 凭证 |
| `read operation timed out` | 外部 API 超时；采集器会重试并记录失败，可重跑对应专辑 |
| `stored_tracks < requested_tracks` | 有歌曲三个来源都未成功，需要查看 `failures` |
| `stored_tracks` 相等但有 `failures` | 每首至少有一个来源成功，但部分来源缺失 |
| L2 文件为空 | 这是通过 `init` 创建的空框架，不是采集结果 |

单张专辑失败时，优先重跑单张而不是全部重跑：

```bash
rateyourdj-collect album <album-key> --user-id demo-user
```

查看所有可用 album key：

```bash
rateyourdj-collect album --help
```

# L3

## L3 是什么

L3 是本地相似歌曲召回模块。它从 L1 读取用户收藏的
`collection_song_ids`，从 L2 加载这些种子歌曲及候选歌曲画像，然后返回
相似歌曲。

L3 当前只负责召回，不负责最终推荐排序。已实现的 L4 会结合 L1 收藏偏好、
L2 画像质量和列表多样性重新排序 L3 候选。

数据流：

```text
L1 collection_song_ids
          |
          v
加载对应的 L2 种子歌曲
          |
          v
扫描其余 L2 歌曲作为候选
          |
          v
计算相似度、过滤收藏和重复版本
          |
          v
返回 Top-K candidates
```

## L3 目前完成了什么

- 扫描本地 `data/song_profiles` 候选库。
- 排除 L1 中已经收藏的歌曲。
- 排除 external ID 相同的歌曲。
- 根据歌名、歌手和时长合并原版、重制版、现场版等重复结果。
- 合并多个种子对同一候选的匹配结果。
- 默认每位歌手最多返回 2 首，避免结果被同一歌手占满。
- 支持 `top_k` 和最低相似度阈值。
- 缺少对应 L2 JSON 的收藏 ID 会列入 `missing_seed_song_ids`，不会使整个
  召回失败。
- 当前 `retrieval_sources` 为 `local_candidate_library`。
- Last.fm similar tracks 在线召回尚未接入。

## 相似度计算

L3 分两步计算分数：先计算候选与每首收藏歌曲的歌曲级相似度，再把多个
歌曲级分数聚合成整体收藏相似度。

### 歌曲级相似度

候选与单首种子歌曲之间使用四类信号：

```text
PairwiseSimilarity =
0.55 * TrackTagSimilarity
+ 0.25 * GenreSimilarity
+ 0.15 * ArtistTagSimilarity
+ 0.05 * ReleaseEraSimilarity
```

| 分项 | L2 字段 | 权重 |
| --- | --- | --- |
| Track tags | `source_tags.lastfm_track_tags` | 0.55 |
| Genres | `genres` | 0.25 |
| Artist tags | `source_tags.lastfm_artist_tags` | 0.15 |
| Release year | `metadata.release_year` | 0.05 |

tags 和 genres 使用带权 Jaccard 相似度。标签权重越接近，共享标签越多，
分数越高。发行年份相差越小，年代分越高；相差 30 年或以上时年代分为 0。

### 整体收藏相似度

每个候选都会与 L1 `collection_song_ids` 中所有能够成功加载 L2 JSON 的
种子逐一计算 `PairwiseSimilarity`，然后聚合为最终分数：

```text
CollectionSimilarity =
0.70 * BestSeedScore
+ 0.30 * Top5SeedAverageScore
```

`BestSeedScore` 保留候选与某一首收藏歌曲的强关联；
`Top5SeedAverageScore` 衡量候选是否同时符合多首收藏歌曲。种子不足 5 首时，
使用全部有效种子计算平均值。Top-5 中会保留 0 分种子，因此只与一首歌曲
相似的候选会被适度降权。

候选的排序、Top-K 截断和 `--min-score` 过滤都使用
`CollectionSimilarity`，而不是只使用 `BestSeedScore`。

`score_breakdown` 也按相同的 70% + 30% 公式聚合。它保存的是已经乘过歌曲
特征权重和收藏聚合权重的贡献，因此四个分项相加等于
`similarity_score`。

多个种子都能匹配同一候选时：

- `matched_seed_song_ids` 保存所有相似度大于 0 的种子。
- `best_seed_song_id` 保存得分最高的种子。
- `best_seed_score` 保存候选与最佳种子的歌曲级分数。
- `top_seed_average_score` 保存 Top-5 种子的平均歌曲级分数。
- `similarity_score` 和 `score_breakdown` 代表整体收藏聚合结果。

例如某候选的最佳种子分是 `0.90`，Top-5 种子平均分是 `0.50`：

```text
similarity_score = 0.70 * 0.90 + 0.30 * 0.50 = 0.78
```

这意味着它与其中一首收藏歌曲非常相似，同时与整体收藏也有一定一致性。

## 准备测试用户

测试 L3 时，不应把整个 L2 候选库都放进 `collection_song_ids`，否则所有
歌曲都会被收藏过滤器排除。

例如可以让 `demo-user` 只收藏三首：

```json
{
  "collection_song_ids": [
    "pink-floyd-the-wall-01-in-the-flesh-question",
    "pink-floyd-the-wall-19-comfortably-numb",
    "pink-floyd-the-wall-22-run-like-hell"
  ]
}
```

这三个 ID 都必须存在对应文件：

```text
data/song_profiles/<song_id>.json
```

只修改 JSON 文件时要保持完整 L1 结构和合法 JSON。当前 L1 import 会合并
收藏 ID，不会删除旧收藏。需要把大量收藏临时替换成少量测试种子时，应直接
编辑测试用户文件或新建专用测试用户，然后重建偏好：

```bash
rateyourdj-collect rebuild-profile demo-user
```

## 运行 L3

如果已经重新安装项目：

```bash
rateyourdj-l3 retrieve demo-user --top-k 20
```

没有安装命令行入口时：

```bash
PYTHONPATH=src python3 -m rateyourdj.l3.cli retrieve demo-user --top-k 20
```

限制每位歌手最多一首：

```bash
PYTHONPATH=src python3 -m rateyourdj.l3.cli retrieve demo-user \
  --top-k 20 \
  --max-per-artist 1
```

只保留分数高于 `0.2` 的候选：

```bash
PYTHONPATH=src python3 -m rateyourdj.l3.cli retrieve demo-user \
  --top-k 20 \
  --min-score 0.2
```

查看 L3 输出 schema：

```bash
PYTHONPATH=src python3 -m rateyourdj.l3.cli schema
```

## L3 输出说明

简化输出示例：

```json
{
  "user_id": "demo-user",
  "seed_song_ids": [
    "pink-floyd-the-wall-01-in-the-flesh-question",
    "pink-floyd-the-wall-19-comfortably-numb",
    "pink-floyd-the-wall-22-run-like-hell"
  ],
  "missing_seed_song_ids": [],
  "candidates": [
    {
      "candidate_song_id": "pink-floyd-the-wall-14-hey-you",
      "best_seed_song_id": "pink-floyd-the-wall-19-comfortably-numb",
      "matched_seed_song_ids": [
        "pink-floyd-the-wall-01-in-the-flesh-question",
        "pink-floyd-the-wall-19-comfortably-numb",
        "pink-floyd-the-wall-22-run-like-hell"
      ],
      "best_seed_score": 0.956997,
      "top_seed_average_score": 0.506538,
      "similarity_score": 0.821859,
      "score_breakdown": {
        "track_tags": 0.41027,
        "genres": 0.211589,
        "artist_tags": 0.15,
        "release_year": 0.05
      },
      "retrieval_sources": [
        "local_candidate_library"
      ]
    }
  ]
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `seed_song_ids` | 本次成功加载的全部 L2 种子 |
| `missing_seed_song_ids` | L1 中存在、但找不到 L2 JSON 的 ID |
| `candidate_song_id` | 被召回的候选歌曲 |
| `best_seed_song_id` | 与候选歌曲级相似度最高的种子 |
| `matched_seed_song_ids` | 与候选存在任意正相似度的种子 |
| `best_seed_score` | 候选与最佳种子的歌曲级相似度 |
| `top_seed_average_score` | 候选与 Top-5 种子的平均歌曲级相似度 |
| `similarity_score` | 70% 最佳分加 30% Top-5 平均分 |
| `score_breakdown` | 四类特征对整体收藏分的加权贡献 |
| `retrieval_sources` | 候选来自哪个召回来源 |

## 调试 L3

运行 L3 单元测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_l3 -v
```

运行全部离线测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

常见问题：

| 问题 | 含义和处理 |
| --- | --- |
| `candidates` 为空 | L1 可能已经收藏全部 L2 歌曲，或所有候选分数未超过阈值 |
| `missing_seed_song_ids` 不为空 | 对应 ID 没有 L2 JSON，检查拼写和文件名 |
| `seed_song_ids` 很长 | 当前用户收藏过多；建立只包含少量种子的测试用户 |
| 候选大多来自同一专辑 | 同歌手、同年代和标签高度相似；可降低 `max-per-artist` |
| 低分候选的 `track_tags` 为 0 | 它只通过 genres、artist tags 或年代产生弱匹配 |
| `rateyourdj-l3` 命令不存在 | 重新运行 `python -m pip install -e . --no-build-isolation` |

# L4

## L4 是什么

L4 是偏好感知的推荐排序模块。L3 负责找出与收藏歌曲相似的候选，L4 再读取
L1 的 artist、genre、tag 偏好和 L2 的画像质量，把较大的候选池重排为最终
Top-K。

当前 L4 只使用已经稳定存在的数据字段，不使用旧设计中的 short-term
intent、negative preference、mood、scene 或 embedding。

数据流：

```text
L3 扩大候选池
      |
      v
L1 artist / genre / tag 偏好匹配
      |
      v
L2 confidence_score 质量加权
      |
      v
与已选歌曲计算重复度
      |
      v
输出多样化 Top-K
```

## L4 分数

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

genre 和 tag 使用带权 Jaccard 匹配。artist 在忽略大小写和多余空格后精确
匹配。`ProfileQuality` 使用 L2 `confidence_score`，缺失值按 0 处理。

为了避免最终列表充满高度相似的歌曲，L4 每次从剩余候选中选择歌曲时，都会
与已经选中的歌曲比较：

```text
DiversitySimilarity =
0.20 * SameArtist
+ 0.40 * GenreSimilarity
+ 0.40 * TagSimilarity

FinalScore =
clamp(BaseScore - 0.15 * MaxDiversitySimilarity, 0, 1)
```

因此第一首歌曲没有多样性惩罚，后续歌曲如果与前面结果的 artist、genres
或 tags 高度重复，排名会下降。

## 运行 L4

```bash
PYTHONPATH=src python3 -m rateyourdj.l4.cli rank demo-user --top-k 20
```

指定候选池、歌手上限和 L3 最低分：

```bash
PYTHONPATH=src python3 -m rateyourdj.l4.cli rank demo-user \
  --top-k 20 \
  --candidate-pool-size 100 \
  --max-per-artist 2 \
  --min-retrieval-score 0.05
```

默认候选池大小是 `top_k * 5`。候选池必须不小于最终 `top_k`。

查看 schema：

```bash
PYTHONPATH=src python3 -m rateyourdj.l4.cli schema
```

## L4 输出

每首结果包括：

| 字段 | 含义 |
| --- | --- |
| `rank` | 最终名次 |
| `song_id` | L2 song ID |
| `title` / `artist` | L2 展示字段 |
| `base_score` | 基础贡献与 L5 反馈调整之和 |
| `score_breakdown` | L3、三类偏好、质量和反馈的加权贡献 |
| `diversity_penalty` | 与更高排名歌曲重复产生的惩罚 |
| `final_score` | `base_score - diversity_penalty` |
| `ranking_reasons` | 可读的主要排序原因 |
| `best_seed_song_id` | L3 中与该候选最匹配的收藏歌曲 |
| `retrieval_sources` | L3 候选来源 |

结果顶层还会保留 `seed_song_ids`、`missing_seed_song_ids` 和
`missing_candidate_song_ids`，方便定位数据缺口。

运行 L4 测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_l4 -v
```

# L5

## L5 是什么

L5 是推荐反馈闭环。它负责：

- 接收用户对某首 L2 歌曲的行为反馈。
- 将反馈类型转换为 `[-1, 1]` reward。
- 把原始反馈写入 L1 `feedback_memory`。
- 根据直接反馈或相似歌曲反馈计算 `FeedbackScore`。
- 将反馈分提供给 L4，影响下一次排序。
- 对 `favorite` 和 `playlist_add` 同步更新用户收藏。

L5 不创建单独的反馈文件，数据继续保存在：

```text
data/user_profiles/<user_id>.json
```

单条反馈结构：

```json
{
  "feedback_type": "like",
  "song_id": "pink-floyd-the-wall-14-hey-you",
  "timestamp": "2026-06-11T00:00:00+10:00",
  "reward_score": 0.6,
  "recommendation_context": {
    "rank": 1,
    "source": "l4"
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `feedback_type` | 用户行为类型 |
| `song_id` | 必须存在的 L2 song ID |
| `timestamp` | ISO-8601 时间；省略时自动生成 UTC 时间 |
| `reward_score` | `[-1, 1]` 内的奖励值 |
| `recommendation_context` | 可选的推荐位置、查询、来源等上下文 |

## Reward 映射

| 反馈类型 | 默认 reward | 含义 |
| --- | ---: | --- |
| `play` | 0.1 | 普通播放 |
| `play_complete` | 0.4 | 完整播放 |
| `replay` | 0.5 | 重复播放 |
| `like` | 0.6 | 明确喜欢 |
| `favorite` | 0.8 | 收藏歌曲 |
| `playlist_add` | 1.0 | 加入歌单 |
| `skip` | -0.4 | 普通跳过 |
| `quick_skip` | -0.8 | 快速跳过 |
| `dislike` | -1.0 | 明确不喜欢 |

调用代码接口时可以显式覆盖默认 reward，但值必须在 `[-1, 1]` 内。CLI
对应参数是 `--reward-score`。

## 反馈分计算

### 直接反馈

如果候选歌曲本身有反馈，使用该歌曲最新一条非零 reward：

```text
FeedbackScore(candidate) = latest direct reward
```

例如对 `Hey You` 记录 `like`：

```text
FeedbackScore(Hey You) = 0.6
L4 feedback_adjustment = 0.15 * 0.6 = 0.09
```

### 相似歌曲传播

候选没有直接反馈时，L5 会比较它与历史反馈歌曲：

```text
FeedbackSimilarity =
0.25 * SameArtist
+ 0.35 * GenreSimilarity
+ 0.40 * TagSimilarity
```

genre 和 tag 使用带权 Jaccard。相似度低于 `0.30` 的反馈不传播；达到阈值
时按相似度衰减：

```text
TransferredFeedback =
sum(HistoryReward * FeedbackSimilarity)

FeedbackScore =
clamp(TransferredFeedback, -1, 1)
```

因此一条 `like` 不会给所有候选相同加分：

- 原歌曲保留完整反馈。
- 高度相似歌曲获得接近但小于完整值的反馈。
- 弱相关或无关歌曲反馈分为 0。

## L5 对 L1 和 L4 的影响

普通行为反馈，例如 `like`、`skip`、`dislike`，只写入
`feedback_memory`，不会永久修改收藏聚合出的 artist/genre/tag preferences。

`favorite` 和 `playlist_add` 会额外执行：

1. 将歌曲加入 L1 `collection_song_ids`。
2. 从当前全部收藏 L2 文件重新聚合偏好。
3. 使 L3 后续将该歌曲作为已收藏歌曲过滤。

L4 基础分包含：

```text
+ 0.15 * FeedbackScore
```

输出位于：

```text
score_breakdown.feedback_adjustment
```

正反馈会产生 `promoted by positive feedback` 排序原因，负反馈会产生
`penalized by negative feedback`。

## 运行 L5

查看 schema 和 reward：

```bash
PYTHONPATH=src python3 -m rateyourdj.l5.cli schema
```

记录反馈：

```bash
PYTHONPATH=src python3 -m rateyourdj.l5.cli record \
  demo-user pink-floyd-the-wall-14-hey-you like
```

显式指定 reward：

```bash
PYTHONPATH=src python3 -m rateyourdj.l5.cli record \
  demo-user pink-floyd-the-wall-14-hey-you play \
  --reward-score 0.25
```

保存推荐上下文。先建立 `recommendation-context.json`：

```json
{
  "rank": 1,
  "query": "推荐一些 Pink Floyd 风格的歌曲",
  "source": "l4"
}
```

然后执行：

```bash
PYTHONPATH=src python3 -m rateyourdj.l5.cli record \
  demo-user pink-floyd-the-wall-14-hey-you skip \
  --context-json recommendation-context.json
```

查看反馈摘要：

```bash
PYTHONPATH=src python3 -m rateyourdj.l5.cli summary demo-user
```

输出包括反馈总数、正负反馈数量、平均 reward、各反馈类型数量和缺失的 L2
song ID。

查看某首歌曲当前获得的反馈分：

```bash
PYTHONPATH=src python3 -m rateyourdj.l5.cli score demo-user <song-id>
```

如果已通过 editable install 注册命令，也可以省略 `PYTHONPATH`：

```bash
rateyourdj-l5 summary demo-user
```

## 检查 L5

检查反馈是否写入 L1：

```bash
PYTHONPATH=src python3 -m rateyourdj.l1.cli show demo-user
```

检查反馈是否影响 L4：

```bash
PYTHONPATH=src python3 -m rateyourdj.l4.cli rank demo-user --top-k 5
```

重点查看：

- 被直接 `like` 的歌曲是否有正 `feedback_adjustment`。
- 相似歌曲是否获得衰减后的较小调整。
- 无关歌曲的 `feedback_adjustment` 是否为 0。
- `dislike` 或 `quick_skip` 是否降低候选分数。
- `favorite` 后歌曲是否从 L3 候选中消失。

运行 L5 测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_l5 -v
```

运行全部离线测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 常见问题

| 问题 | 含义和处理 |
| --- | --- |
| `SongNotFoundError` | 反馈的 song ID 不存在对应 L2 JSON |
| `feedback_type must be one of ...` | 反馈类型不在支持列表中 |
| `reward_score must be between -1 and 1` | 自定义 reward 超出范围 |
| `timestamp must be an ISO-8601 string` | 时间格式不合法 |
| 其他歌曲也有反馈加分 | 它们与历史反馈歌曲的相似度达到 `0.30` |
| 无关歌曲仍有相同加分 | 应检查是否运行了当前版本以及相似度传播测试 |
| `favorite` 后仍在候选中 | 检查 L1 是否成功加入 song ID，并重新运行 L3 |
| CLI 命令不存在 | 使用 `PYTHONPATH=src python3 -m rateyourdj.l5.cli ...` 或重新安装项目 |

# 本地网页

网页层直接复用 L1-L5 服务，提供用户画像、收藏列表、推荐列表、反馈摘要和
反馈按钮。它使用 Flask 和原生 HTML/CSS/JavaScript，不需要 Node.js 或前端
构建步骤。

启动：

```bash
PYTHONPATH=src python3 -m rateyourdj.web.app
```

访问：

```text
http://127.0.0.1:8000
```

页面中的反馈按钮会真实修改当前用户的 L1 JSON。`favorite` 会加入收藏并
立即显示在“我的收藏”区域，其他按钮会写入 L5 feedback 并影响下一次 L4
排序。如果 L1 收藏 ID 缺少对应的 L2 文件，收藏 API 会返回
`missing_song_ids`，页面继续展示其余可用歌曲。

运行网页 API 测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_web -v
```

# L1-L5 Agent 工具层

L1-L5 的原有 service 和数据模型保持不变，同时新增了面向 Agent 的结构化工具
入口。后续 L6 planner 可以观察工具结果，再决定是否调整参数或调用其他工具。

统一返回 `ToolObservation`：

```json
{
  "tool": "L4.rank_candidates",
  "status": "partial",
  "data": {},
  "diagnostics": [
    "requested 10 songs but ranked 4"
  ],
  "retryable": true,
  "suggested_actions": [
    {
      "tool": "L4.rank_candidates",
      "arguments": {
        "candidate_pool_size": 100
      },
      "reason": "expand the L3 candidate pool"
    }
  ]
}
```

`status` 有三种值：

| 状态 | 含义 |
| --- | --- |
| `ok` | 工具正常完成，结果满足当前请求 |
| `partial` | 有可用结果，但存在缺失数据或数量不足 |
| `empty` | 没有可用结果，需要调整参数或补充数据 |

当前 Agent 工具：

| 层级 | 工具 | 作用 |
| --- | --- | --- |
| L1 | `inspect_user_profile` | 查看收藏、偏好、反馈数量和画像版本 |
| L2 | `inspect_song_profile` | 查看歌曲画像及缺失 metadata、tags、genres |
| L3 | `retrieve_candidates_tool` | 召回候选并返回缺失种子、数量不足和重试建议 |
| L4 | `rank_candidates_tool` | 排序并返回缺失候选、数量不足和可调整参数 |
| L5 | `inspect_feedback_state` | 查看反馈摘要及缺失的反馈歌曲画像 |
| L5 | `record_feedback_tool` | 记录反馈并报告 trajectory 是否完成回连 |

L3/L4 在候选不足时会给出结构化建议，例如：

- 降低 `min_score` 或 `min_retrieval_score`。
- 扩大 `candidate_pool_size`。
- 放宽 `max_per_artist`。
- 补采缺失的 L2 种子歌曲。

这些工具目前不会自动重试。它们提供 observation 和建议，下一阶段由 L6 的
执行循环决定是否采纳。

## L5 trajectory 回写

网页层会把 `JsonTrajectoryStore` 作为 feedback sink 注入 L5。携带合法
`trajectory_id` 的反馈会同时写入：

```text
L1 feedback_memory
data/trajectories/<user_id>/<trajectory_id>.json
```

trajectory 新增 `feedback_events`：

```json
{
  "feedback_events": [
    {
      "feedback_type": "like",
      "song_id": "song-id",
      "timestamp": "2026-06-11T00:00:00+00:00",
      "reward_score": 0.6,
      "recommendation_context": {
        "trajectory_id": "trajectory-id",
        "rank": 1,
        "source": "web"
      }
    }
  ]
}
```

trajectory feedback 使用线程锁、文件锁和原子替换，避免并发反馈互相覆盖。
如果 `trajectory_id` 不属于当前用户，L5 会在写入 L1 前拒绝请求。

运行工具层测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_agent_tools -v
```

# L6

L6 是自然语言 Agent 编排层。它消费现有 L1-L5 能力，不重新实现画像、召回、
排序或反馈算法。

## L6 工作流程

```text
自然语言 query
    ↓
解析数量、流派、排除项、相似度和多样性
    ↓
调用 L1 工具检查画像和收藏种子
    ↓
动态调用 L4，并观察结构化 ToolObservation
    ↓
候选不足时扩大候选池、放宽阈值或歌手限制并重试
    ↓
仍不足时调用 L3 记录召回诊断
    ↓
检查目标约束并返回歌曲、分数拆解和推荐原因
    ↓
保存 trajectory
    ↓
保存 session；“换一批”继承条件并排除已展示歌曲
    ↓
L5 反馈通过 trajectory_id 回连本次请求
```

L6 第一版已实现：

1. 接收自然语言推荐请求。
2. 解析推荐数量、流派偏好、排除词、相似度要求和歌手多样性。
3. 通过工具注册表选择和执行 L1、L3、L4 工具。
4. 检查候选数量和请求约束，最多执行三次 L4 参数调整。
5. 使用 L4 的 `ranking_reasons` 和 `score_breakdown` 生成可追溯解释。
6. 保存计划、工具观察、决策、停止原因、推荐结果和响应文本。
7. 使用 session 支持“换一批”多轮请求。
8. 为网页提供聊天式 API。

## 当前解析规则

L6 保留本地确定性解析器作为 fallback，并提供可替换的 `LLMProvider` 接口。
当前已实现 DeepSeek 官方 API adapter。只有设置 `DEEPSEEK_API_KEY` 并选择
`auto` 或 `deepseek` provider 时才会产生 API 请求和费用；否则使用规则路径。
provider 启用后使用最多 5 步的受控 Agent loop，API 不可用或返回非法调用时
自动回落到规则路径。

规则 fallback 当前支持：

| 自然语言内容 | 解析结果 |
| --- | --- |
| `推荐 5 首`、`推荐五首` | `top_k = 5` |
| `摇滚`、`爵士`、`电子` 等 | 写入 `preference_terms` |
| `不要“Pink Floyd”` | 写入 `exclude_terms` |
| `多样一点`、`不同歌手` | `max_per_artist = 1` |
| `相似` | `min_retrieval_score = 0.1` |
| `高度相似` | `min_retrieval_score = 0.3` |
| `换一批`、`再来` | `intent = more`，继承上一轮条件并排除已展示歌曲 |

当前内置的中英文流派映射包括：

```text
摇滚 rock
爵士 jazz
流行 pop
灵魂 soul
民谣 folk
电子 electronic
朋克 punk
金属 metal
放克 funk
乡村 country
蓝调 blues
古典 classical
氛围 ambient
```

数量限制在 `1-50`。没有显式数量时，CLI 和 API 默认返回 10 首；网页会使用
页面中选择的推荐数量作为默认值。

## L6 执行循环

模型模式下，provider 每一步只能返回：

- 一个结构化工具调用；
- 一个经过 schema 校验的请求条件补丁；
- 一段可记录的简短决策摘要；
- 或结束本轮的 `finish` 决策。

模型可见的只读工具为：

```text
L1.inspect_user_profile
L2.inspect_song_profile
L3.retrieve_candidates
L4.rank_candidates
L5.inspect_feedback_state
```

程序会拒绝跨用户访问、未知工具、越界参数和过早结束。模型可以补充规则没有
识别出的排除项或偏好，但不能删除已识别的排除项、放宽明确要求的歌手多样性、
取消“换一批”或覆盖明确指定的歌曲数量。模型循环最多 5 步。重复请求更新和
完全相同的工具调用会被忽略；模型耗尽步骤仍未调用 L4 时，程序会补充一次
经过相同约束校验的 L4 排序，避免 Agent 空转。

DeepSeek adapter 使用官方 OpenAI-compatible `/chat/completions` 接口和
function calling。内部 `L4.rank_candidates` 等工具名会转换成 API 接受的
`L4__rank_candidates`，返回后再映射回内部名称。模型请求不包含真实 user ID；
本地执行器会在校验后注入当前 user ID。

启用：

```bash
export DEEPSEEK_API_KEY="你的 API key"

PYTHONPATH=src python3 -m rateyourdj.l6.cli \
  --agent-mode auto \
  --llm-provider auto \
  recommend demo-user '推荐 5 首不同歌手的摇滚，不要 Pink Floyd'
```

可选环境变量：

```text
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

L6 不修改 L4 的评分公式。初始候选池为：

```text
candidate_pool_size = top_k * 5
```

随后使用以下 L4 参数：

| 参数 | 来源 |
| --- | --- |
| `top_k` | 扩大后的候选池大小 |
| `candidate_pool_size` | 扩大后的候选池大小 |
| `max_per_artist` | 自然语言中的多样性要求 |
| `min_retrieval_score` | 自然语言中的相似度要求 |

每次 L4 调用后，L6 检查过滤后的有效歌曲是否达到 `top_k`。不足时，L6
读取 `ToolObservation.retryable` 和 `suggested_actions`，合并同一工具的参数
建议，经白名单、数值范围和用户约束校验后执行。扩大候选池或降低召回阈值
等具体数值由 L4 工具给出，不在 L6 中重复硬编码。最多尝试三次；仍不足时
调用 L3 获取召回层诊断并停止。

被采纳的建议会写入 trajectory step 的 `selected_action`，其中同时保存工具
原始参数补丁、合并后的 `resolved_arguments` 和建议原因。未注册工具、未知
参数、越界数值，以及违反“每位歌手最多一首”等用户约束的建议不会执行。

执行过程不是无限循环。默认 `max_steps = 5`，允许范围为 `2-10`。停止原因
包括 `goal_satisfied`、`empty_profile` 和 `insufficient_candidates`。

## 运行 L6

查看 L6 请求和响应 schema：

```bash
PYTHONPATH=src python3 -m rateyourdj.l6.cli schema
```

运行自然语言推荐：

```bash
PYTHONPATH=src python3 -m rateyourdj.l6.cli recommend demo-user \
  '推荐 5 首多样一点的摇滚，不要“Pink Floyd”'
```

如果已经重新安装项目，也可以使用：

```bash
rateyourdj-l6 recommend demo-user \
  '推荐 5 首多样一点的摇滚，不要“Pink Floyd”'
```

预期解析结果：

```json
{
  "top_k": 5,
  "max_per_artist": 1,
  "min_retrieval_score": 0.0,
  "preference_terms": ["rock"],
  "exclude_terms": ["pink floyd"],
  "intent": "recommend",
  "exclude_seen": false
}
```

响应中的主要字段：

| 字段 | 含义 |
| --- | --- |
| `trajectory_id` | 本次请求的唯一 UUID |
| `session_id` | 多轮会话 UUID |
| `user_id` | 当前 L1 用户 |
| `query` | 原始自然语言请求 |
| `parsed_request` | 解析后的 L6 参数 |
| `message` | L6 生成的总体推荐说明 |
| `ranked_songs` | 过滤后的 L4 推荐结果 |
| `seed_song_ids` | 本次 L3/L4 使用的有效收藏种子 |
| `missing_seed_song_ids` | L1 中存在但缺少 L2 文件的种子 |
| `stop_reason` | 执行循环停止原因 |
| `attempts` | L4 排序尝试次数 |
| `tool_calls` | 每步工具参数、观察、采纳的 suggested action 和后续决策 |

每首 `ranked_songs` 继续保留 L4 的：

```text
final_score
base_score
score_breakdown
diversity_penalty
ranking_reasons
best_seed_song_id
retrieval_sources
```

## Trajectory

每次 L6 请求会写入：

```text
data/trajectories/<user_id>/<trajectory_id>.json
```

trajectory 包含：

| 字段 | 内容 |
| --- | --- |
| `trajectory_id` | 请求 UUID |
| `session_id` | 所属多轮会话 UUID |
| `turn_index` | 会话中的轮次 |
| `user_id` | 用户 ID |
| `query` | 原始请求 |
| `parsed_request` | 解析后的参数 |
| `plan` | 本轮初始目标和条件工具计划 |
| `tool_calls` | 每步工具名称、参数、完整 observation 和决策 |
| `recommendations` | 最终返回的推荐歌曲 |
| `response_text` | L6 总体解释 |
| `feedback_events` | L5 回写的反馈和 reward |
| `stop_reason` | 达成目标、空画像或候选不足 |
| `agent_mode` | 实际使用 `model` 或 `rules` |
| `provider` | provider 名称；未配置时为空 |
| `fallback_reason` | 模型路径降级原因 |
| `agent_decisions` | 工具选择和可验证决策摘要，不包含隐藏思维链 |
| `created_at` | UTC 创建时间 |

查看某个用户已保存的 trajectory：

```bash
find data/trajectories/demo-user -name '*.json' -print
```

查看最近生成的内容：

```bash
python3 -m json.tool \
  data/trajectories/demo-user/<trajectory-id>.json
```

trajectory 运行时数据已加入 `.gitignore`，默认不会提交到 Git。

多轮会话状态写入：

```text
data/sessions/<session_id>.json
```

其中保存累计排除词、上一轮偏好、已展示歌曲、轮次和最近 trajectory。会话
数据同样不会提交到 Git。

## 网页 API

启动网页：

```bash
PYTHONPATH=src python3 -m rateyourdj.web.app
```

聊天接口：

```text
POST /api/chat/<user_id>
Content-Type: application/json
```

请求示例：

```json
{
  "query": "推荐五首多样一点的摇滚，不要“Pink Floyd”",
  "default_top_k": 10
}
```

`default_top_k` 可省略，允许范围为 `1-50`。如果 query 自己包含数量，query
中的数量优先。

页面收到 L6 响应后，会继续使用现有推荐卡片展示：

- 歌名和歌手。
- 最终分数。
- `ranking_reasons`。
- `score_breakdown`。
- 喜欢、跳过、不喜欢和收藏按钮。

## L6 与 L5 回连

用户点击 L6 推荐结果的反馈按钮时，网页会将当前 trajectory ID 写入：

```json
{
  "recommendation_context": {
    "trajectory_id": "<trajectory-id>",
    "rank": 1,
    "final_score": 0.42,
    "source": "web"
  }
}
```

该记录由 L5 同时写入 L1 `feedback_memory` 和 L6 trajectory。因此可以通过
`trajectory_id` 将：

```text
自然语言 query
→ 解析参数
→ L4 工具调用
→ 推荐歌曲
→ 用户反馈
→ reward
```

串成完整的数据链路。

## 验证 L6

运行 L6 单元测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_l6 -v
```

运行网页 API 测试：

```bash
PYTHONPATH=src python3 -m unittest tests.test_web -v
```

运行全部测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

当前预期结果：

```text
Ran 130 tests
OK (skipped=4)
```

4 项跳过项是需要网络或凭据的 Spotify、MusicBrainz 和 Last.fm 在线 smoke
tests，不代表 L6 失败。

## 当前限制

- 未配置 provider 时使用规则 fallback，不能理解任意复杂自然语言。
- 已接入 DeepSeek 官方 API；OpenAI adapter 尚未实现。
- 流派偏好目前作为结果过滤条件，不会动态修改 L4 权重。
- 排除项支持 `不要“Pink Floyd”`、`不要 Pink Floyd` 和
  `不要pinkfloyd` 等格式。
- 在线扩展候选尚未接入；音频试听已通过 Spotify Embed 接入网页推荐卡片。
- trajectory 已回写反馈，但还没有独立的训练数据导出器。
- L6 没有调用外部 LLM，因此当前解释来自可追溯的规则和 L4 分数。

L6 MVP 不包含 SFT/GRPO。训练需要先积累足够的多用户 trajectory 和 reward
数据；当前少量本地反馈只适合验证数据链路。

## Spotify 试听

网页层会为推荐结果读取对应 L2 的 `external_ids.spotify_track_id`。合法的
22 位 Spotify track ID 会转换为：

```text
https://open.spotify.com/embed/track/<spotify_track_id>
```

卡片仅在 ID 可用时显示“试听”。点击后加载 Spotify 托管的 iframe；切换到
另一首歌时会卸载前一个 iframe，避免多个播放器同时播放。推荐 API 同时增加：

```text
spotify_track_id
spotify_embed_url
spotify_url
preview_available
```

这些字段只用于网页展示，不进入 L4 分数，也不修改 L6 trajectory。

网页使用 Spotify 官方 IFrame API 监听播放状态：

| Spotify 事件/状态 | L5 反馈 |
| --- | --- |
| 首次 `playback_started` | `play` |
| `position >= duration * 0.9` | `play_complete` |
| 已开始播放且 15 秒内关闭或切歌 | `quick_skip` |

同一播放器实例中每类事件最多写入一次。反馈上下文中的 `source` 为
`spotify_embed`，并保存播放位置和总时长。L6 聊天推荐会通过 trajectory ID
回写本次请求；普通 L4 推荐没有 trajectory，只写入 L1 feedback memory。

# L7

## L7 是什么

L7 是 trajectory 数据导出与离线评估层。它读取 L6 写入的 JSON，不修改任何
用户画像、歌曲画像、排序结果或反馈记录。

## L7 导出

默认导出脱敏 JSONL：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli export \
  data/exports/trajectories.jsonl
```

每行包含原始 trajectory 的 query、解析条件、计划、工具 observation、推荐、
解释、反馈和 L7 生成的摘要字段。`user_id` 默认转换为稳定的 `user_key`。

CSV 只保留 trajectory 级摘要：

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli export \
  data/exports/trajectories.csv --format csv
```

常用过滤：

```bash
--user-id demo-user
--feedback-only
```

## L7 评估

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli evaluate
```

主要指标：

| 指标 | 口径 |
| --- | --- |
| `goal_satisfied_rate` | `stop_reason` 为 `goal_satisfied` 的比例 |
| `quantity_satisfied_rate` | 返回数量达到请求 `top_k` 的比例 |
| `feedback_coverage_rate` | 至少收到一次反馈的 trajectory 比例 |
| `tool_call_success_rate` | observation 为 `ok` 或 `partial` 的工具调用比例 |
| `fallback_rate` | 模型路径发生 fallback 的 trajectory 比例 |
| `average_reward` | 有数值 reward 的反馈平均值 |
| `skip_rate` | `skip` 和 `quick_skip` 占全部反馈的比例 |
| `favorite_rate` | `favorite` 和 `playlist_add` 占全部反馈的比例 |
| `artist_diversity` | 每条推荐列表不同歌手数除以歌曲数后汇总 |

无数据时比例和平均值返回 `0.0`，不产生除零错误。损坏或不兼容的 trajectory
列入 `skipped_files`。这些指标用于本地回归和数据质量检查，不替代线上实验。

## 验证 L7

```bash
PYTHONPATH=src python3 -m unittest tests.test_l7 -v
```

## 生成合成 trajectory

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli generate-synthetic \
  data/synthetic/trajectories \
  --count 500 \
  --users 25 \
  --seed 20260615 \
  --feedback-rate 0.7
```

生成器从现有 L2 中抽取真实歌曲标题、歌手和流派，但用户、请求、工具调用、
排序分数和反馈事件均为模拟数据。固定 `seed` 可复现相同 ID 和随机分布。
目标目录已有 trajectory 时命令会拒绝执行，不会覆盖现有数据。

当前生成的本地样本：

```text
500 trajectories
25 synthetic users
132 sessions
840 feedback events
353 trajectories with feedback
```

它们保存在 `data/synthetic/` 并被 Git 忽略。不要将合成 reward 与真实用户
reward 混合汇报，也不要用合成数据声称模型获得了真实偏好学习能力。

## 按用户切分

```bash
PYTHONPATH=src python3 -m rateyourdj.l7.cli \
  --trajectory-dir data/synthetic/trajectories \
  split data/synthetic/splits-v1
```

切分单位是 `user_id`，不是单条 trajectory。一个用户只会出现在 train、
validation 或 test 中的一个文件。`manifest.json` 记录 seed、请求比例、实际
用户数、trajectory 数、脱敏状态和坏文件列表。
