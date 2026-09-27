# 阶段 1：单用户上下文与长尾曲库

> 对应 TODO.md 第 5 节。完成日期：2026-09-27。全部数据采集在 Mac 本机运行，不需要 GPU。

## 目标

1. 建立 `participant_001` 的轻量用户上下文，两条兴趣分支（Pink Floyd / Oasis）独立保存；
2. 建立一个可重复生成、每首歌都有来源和冷门程度分档的标准化曲库，供阶段 2 的检索使用。

## 做出的决策

| 问题 | 决定 |
|---|---|
| 种子歌 | 沿用 TODO 的 6 首：Time、Wish You Were Here、Shine On You Crazy Diamond（Parts I–V）；Live Forever、Slide Away、Champagne Supernova |
| 曲库范围 | v1 用种子扩展：相似艺人 2 跳 + 分支风格标签艺人。已知局限见下文；以后可换“按风格圈定、与用户无关的大曲库” |
| 热度指标 | ListenBrainz **听众数**（不用播放次数，避免被少数人循环播放放大） |
| 分档参照 | **ListenBrainz 全网**。官方不提供全网百分位，用 MusicBrainz 导出抽样估计 |
| 分档阈值 | `bucket-v2`：全网前 1% = head，前 1%–10% = mid，其余 = tail（v1 的 10%/50% 使 tail 只剩 0.3%，已弃用） |
| 播放链接 | 按需解析、结果缓存。head/mid 走 Spotify（ISRC 精确匹配优先）；tail 走 YouTube Data API（每天 ≤ 95 次搜索），优先官方频道 |

## 做了什么

### 1. 用户上下文

- `config/participant_001.seeds.json`：种子歌、分支标签、扩展参数；
- `data/users/participant_001/context.json`：`UserContextV2`，两条分支各 3 首种子；
- `data/users/participant_001/seeds_resolved.json`：每首种子选中的录音和备选项，便于人工复核；
- `load_user_context(user_id)`：读 V2 上下文，找不到时通过迁移适配器读旧 L1 画像。

种子解析规则：优先选 ListenBrainz 上同名录音里**听众最多**的那条（ListenBrainz 实际把收听记在这条上），排除 live / demo 等版本；分段曲目按分段号精确匹配（罗马数字与阿拉伯数字等价）；也可在配置里用 `recording_mbid` 钉死。

| 分支 | 种子 | 首发 | 听众 |
|---|---|---|---|
| pink_floyd | Time | 1973 | 190,104 |
| pink_floyd | Wish You Were Here | 1975-09-12 | 204,096 |
| pink_floyd | Shine On You Crazy Diamond, Parts I–V | 1975-09-12 | 29,773 |
| oasis | Live Forever | 1994-08-02 | 111,658 |
| oasis | Slide Away | 1994-08-30 | 72,026 |
| oasis | Champagne Supernova | 1995-10-02 | 103,598 |

### 2. 曲库构建（`src/rateyourdj/data_pipeline/`）

| 文件 | 作用 |
|---|---|
| `http_cache.py` | 带磁盘缓存、按域名限速、自动重试的 HTTP 客户端；中断后重跑不重复请求 |
| `sources.py` | MusicBrainz / ListenBrainz 客户端；版本后缀、分段号、非官方现场发行的识别 |
| `seeds.py`、`user_context.py` | 种子解析与用户上下文读写 |
| `catalog.py` | 艺人扩展 → 每个艺人分层抽歌 → 去重 → 批量元数据 → `SongProfileV2` |
| `popularity.py` | 从 MusicBrainz 导出抽样建全网参照分布，算百分位并分档 |
| `playback.py` | 按需解析 Spotify / YouTube 播放链接 |
| `report.py`、`cli.py` | 质量报告；命令行 `rateyourdj-catalog` |

每个艺人取**前 10 首代表作 + 从其余歌中按排名均匀抽 30 首**（只在听众 ≥ 该艺人最高值 0.1% 且 ≥ 10 人的范围内抽），先合并同一首歌的重复录音条目，并过滤 live / demo / remix / 带日期的现场 / 非官方现场发行。

### 3. 全网热度参照

从 MusicBrainz 核心导出（`mbdump.tar.bz2`，2026-09-23，7 GB）流式读取录音表：

- 扫描 39,929,786 条录音，均匀抽样 200,000 条，其中 93,234 条（46.6%）至少有 1 个 ListenBrainz 听众，作为全网样本；
- 全网分布极度偏斜：一半有听众的录音只有 ≤ 5 个听众，前 10% 门槛 189，前 1% 门槛 3,735；
- `bucket-v2` 门槛：**head ≥ 3,735 个听众，mid ≥ 189，tail < 189**。

## 结果

| 项目 | 结果 |
|---|---|
| 艺人 / 歌曲 | 368 / 14,065 |
| 种子在曲库中 | 6 / 6 |
| 字段缺失 | 发行年份 1.4%，其余 0% |
| 重复 | 0 |
| 格式校验 | 14,065 首全部通过 `SongProfileV2` 校验 |
| 分档 | head 5,439 · mid 5,480 · **tail 3,146** |
| pink_floyd 分支 | 7,966 首（tail 1,744） |
| oasis 分支 | 7,825 首（tail 1,471），两分支重叠 1,920 首 |
| 艺人来源 | 相似艺人 63 个，风格标签 303 个（贡献 81% 的歌） |

tail 示例：Rick Wakeman – *The Maker*（95 听众）、Anthony Phillips – *Wildlife Choir*（39）、Amplifier – *Gargantuan*（45）。

播放链接实测：6 首种子全部通过 ISRC 匹配到 Spotify；3 首 tail 歌全部匹配到正确的 YouTube 视频（2 个官方 Topic 频道、1 个粉丝上传，后者标记 `official: false`），同名翻唱版本被正确排除。

## 已知限制

- **曲库带种子偏差**：共同收听会带入风格较远的主流艺人（如 Green Day、Coldplay），风格标签会带入偏离的艺人（如 Björk 靠 “alternative rock” 进了 Oasis 分支）；标签艺人亲和度统一为 0.3，不分远近，需要阶段 2 按与种子的相似度过滤。相关性有一部分在建库时就已内置，评估时要注意。
- **“冷门”只代表 ListenBrainz 用户群**：用户规模远小于 Spotify，偏欧美和独立/另类音乐。
- **同一首歌的多条录音不合并听众**：去重时保留听众最多的一条，热度可能略被低估。
- **发行年份不一定是首发年份**：取自某个具体发行版本（如 Hey You 显示 1984，原版 1979），阶段 2 做检索文档时修正。
- **少量非官方现场专辑仍会漏过**：只能从发行名判断，不查 MusicBrainz 发行类型。
- **没有音频特征**：Spotify 已不向新应用开放 audio-features；检索只能依赖标签、艺人等文本信息。
- **YouTube 搜索额度**：每天约 100 次，按需解析 + 缓存；粉丝上传的视频可能失效。

## 如何复现

```bash
cd ~/Desktop/rateyourDJ
# .env 需要：LISTENBRAINZ_TOKEN、MUSICBRAINZ_CONTACT、SPOTIFY_CLIENT_ID/SECRET、YOUTUBE_API_KEY
PYTHONPATH=src python -m rateyourdj.data_pipeline.cli init-seeds
PYTHONPATH=src python -m rateyourdj.data_pipeline.cli build
PYTHONPATH=src python -m rateyourdj.data_pipeline.cli sample-reference \
  --dump data/catalog/raw/mbdump/mbdump.tar.bz2 --dump-label mbdump-20260923-002121
PYTHONPATH=src python -m rateyourdj.data_pipeline.cli bucket
PYTHONPATH=src python -m rateyourdj.data_pipeline.cli resolve-playback --seeds
PYTHONPATH=src python -m rateyourdj.data_pipeline.cli report
```

`data/users/`、`data/catalog/` 都不进 Git；所有外部请求缓存在 `data/catalog/raw/`，重跑只补缺失部分。

## 下一步：阶段 2（RAG 多路召回）

- 为每首歌生成检索文档（歌名、艺人、标签、年代、分档），修正首发年份；
- 选定多语言 embedding 模型，为曲库和种子生成向量，建两个兴趣中心和向量索引；
- 四路召回：规则、文本语义、长尾专用、探索；候选输出通道、分数、长尾分和证据。

阶段 2 在 Mac 本地运行（embedding 约 1.4 万条，Apple Silicon 可胜任），不需要 GPU。
