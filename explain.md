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
python -m unittest tests.test_l1 -v
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
- 批量采集完成后自动更新 L1。

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
rateyourdj-collect album batch-1 --user-id demo-user
```

只采集第二批：

```bash
rateyourdj-collect album batch-2 --user-id demo-user
```

采集全部登记专辑：

```bash
rateyourdj-collect album all --user-id demo-user
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
python -m unittest tests.test_l2 -v
```

运行采集器离线测试：

```bash
python -m unittest tests.test_collectors -v
```

运行全部测试：

```bash
python -m unittest discover -s tests -v
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

L3 当前只负责召回，不负责最终推荐排序。L4 后续可以结合用户偏好、反馈和
当前需求重新排序 L3 的候选结果。

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

当前使用四类信号：

```text
Similarity =
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

每个候选先与全部收藏种子分别计算上述歌曲级相似度，再聚合为整体收藏分：

```text
CollectionSimilarity =
0.70 * BestSeedScore
+ 0.30 * Top5SeedAverageScore
```

`BestSeedScore` 保留候选与某一首收藏歌曲的强关联；
`Top5SeedAverageScore` 衡量候选是否同时符合多首收藏歌曲。种子不足 5 首时，
使用全部有效种子计算平均值。Top-5 中会保留 0 分种子，因此只与一首歌曲
相似的候选会被适度降权。

`score_breakdown` 也按相同的 70% + 30% 公式聚合。它保存的是已经乘过歌曲
特征权重和收藏聚合权重的贡献，因此四个分项相加等于
`similarity_score`。

多个种子都能匹配同一候选时：

- `matched_seed_song_ids` 保存所有相似度大于 0 的种子。
- `best_seed_song_id` 保存得分最高的种子。
- `best_seed_score` 保存候选与最佳种子的歌曲级分数。
- `top_seed_average_score` 保存 Top-5 种子的平均歌曲级分数。
- `similarity_score` 和 `score_breakdown` 代表整体收藏聚合结果。

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

只修改 JSON 文件时要保持完整 L1 结构和合法 JSON。更稳妥的方式是创建
一个 patch 文件，然后通过 L1 import 写入。

注意：当前 L1 import 会合并收藏 ID，不会删除旧收藏。需要把大量收藏临时
替换成少量测试种子时，应直接编辑测试用户文件，或新建一个专用测试用户。

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
    "pink-floyd-the-wall-19-comfortably-numb"
  ],
  "missing_seed_song_ids": [],
  "candidates": [
    {
      "candidate_song_id": "pink-floyd-the-wall-14-hey-you",
      "best_seed_song_id": "pink-floyd-the-wall-19-comfortably-numb",
      "matched_seed_song_ids": [
        "pink-floyd-the-wall-19-comfortably-numb"
      ],
      "best_seed_score": 0.956997,
      "top_seed_average_score": 0.821,
      "similarity_score": 0.916198,
      "score_breakdown": {
        "track_tags": 0.512838,
        "genres": 0.244159,
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
