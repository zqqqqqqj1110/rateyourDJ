# 阶段 0：冻结现状与定义契约

> 对应 TODO.md 第 4 节。完成日期：2026-09-26。提交：`15a0efa`（阶段 0：冻结现状与定义 V2 契约）。

## 目标

在长尾推荐重构开始前做两件事：

1. 把当前能跑的系统存成 baseline，重构后有对照；
2. 先把阶段 1–6 共享的数据格式（V2 契约）定下来，后面各模块按同一套格式开发。

本阶段**没有改动**召回、排序公式和 Web 布局，没有下载数据，没有生成 embedding，也没有训练模型。

## 做出的决策

| 问题 | 决定 |
|---|---|
| 旧范式（DeepSeek 提名歌曲 + Spotify 确认存在） | 下线；只在 baseline 里归档一次结果作历史对照 |
| 规则模式 baseline | 不做；衡量长尾效果的 baseline 到阶段 3 在新曲库上建立 |
| 曲库数据源 | MusicBrainz + ListenBrainz（替代原计划的 FMA） |
| 长尾歌播放 | 给 YouTube 链接，由 LLM API 搜索，必须经 YouTube 官方接口校验，失败就标“暂无可播放链接” |
| 隐藏正样本 | 来自 ListenBrainz / MSD 中口味相近用户的真实收听日志，**严格不进 reward**，只用于验证、测试和选 checkpoint |
| 在线实验 | 只做相邻策略的两两交错对比（B vs A、C vs B、E vs C、F vs E） |
| 冷门程度分档 | 按 ListenBrainz 收听量百分位：head 前 10%、mid 10–50%、tail 后 50%（`bucket-v1`） |
| 旧 GRPO 数据（6 组） | 废弃，GRPO 数据从头构建 |
| 运行环境 | 阶段 0–3 在 Mac 本地；阶段 4 正式训练和阶段 5 需要 GPU，开始前会提前说明 |

这些决策已同步写入 TODO.md 各阶段。

## 做了什么

### 1. 清理仓库

- 删除项目目录里的无关文件（实习资料、简历 PDF）和缓存（`.DS_Store`、`.pytest_cache`、`__pycache__`）；
- `.env.example` 里的真实 key 换成占位符，并允许它进 Git；真实 key 只放在被忽略的 `.env`；
- `data/dataset.jsonl`、`sft.jsonl`、`grpo.jsonl` 移出 Git（本地保留）；
- `.gitignore` 新增：`data/*.jsonl`、`data/training/`、`data/users/`、`data/catalog/`、`data/index/`、`runs/**/raw/`。

### 2. 定义 V2 契约

| 文档 | 新增内容 |
|---|---|
| `data-contract.md` | “V2 契约”一节：数据流与目录、ID 规则、UserContextV2、SongProfileV2（含分档和播放来源规则）、RetrievalCandidateV2、ImpressionV2、FeedbackV2、SFT / GRPO 样本、数据集划分、run manifest、v1 迁移表 |
| `agent-tools-contract.md` | V2 工具契约：模型只能在候选集内选歌；工具 `get_user_context` / `retrieve_candidates` / `get_track_facts` / `rank_candidates`；系统校验 `validate_selection`（候选集外、约束、证据任一不合格就回退到确定性排序）；模型输出不含隐藏推理 |
| `api-contract.md` | 只加字段不改旧字段：请求加 `exploration_level`、`branch_hint`；每条推荐加 `impression_id`、`bucket`、`channel`、`strategy_version`、`playback`、`evidence_items`；反馈必须带 `impression_id`，新增播放事件和问卷；交错实验归属不返回前端 |

关键规则：

- 内部 song ID 为 `s_` + 16 位十六进制，外部平台 ID（Spotify、MBID、YouTube 等）只存在 `external_ids`，不再当文件名，解决了 `spotify:track:...` 带冒号导致文件名校验失败的问题；
- 没有曝光记录的歌不能成为负反馈；
- `phase: "final"` 的反馈永远不进训练和调参。

### 3. 代码

| 文件 | 说明 |
|---|---|
| `src/rateyourdj/contracts/v2.py` | V2 契约的实现：`make_song_id`、`assign_bucket`、`percentile_ranks`、各格式校验器 `validate_record`、`strip_eval_only`（训练前剥掉隐藏正样本）、v1 → V2 迁移适配器 |
| `src/rateyourdj/experiment.py` | 生成 run manifest：git commit、是否有未提交改动、环境、数据文件 sha256、模型、seed、配置、指标 |
| `src/rateyourdj/l7/trajectory_quality.py` 等 | 质量门禁改为检查 action 合法、decision summary 覆盖率 ≥ 95%、推荐证据覆盖率 ≥ 90%；thought 覆盖率仍然统计，但默认不再作为门禁 |
| `src/rateyourdj/l6/tools.py` | **修复 bug**：CLI 入口 `request_recommendations` 没有构造 `discovery_service`，model 模式下 DeepSeek 提名的歌全部被丢弃，永远返回 0 首（Web 端不受影响） |
| `scripts/baseline_v0.py` | baseline 归档脚本（见下） |
| `tests/test_contracts_v2.py`、`tests/test_experiment.py`、`tests/test_trajectory_quality.py` | 新增 / 更新 21 个测试 |

### 4. 归档 baseline

脚本 `scripts/baseline_v0.py` 在隔离的数据副本上运行，不改动 `data/`，结果写入 `runs/baseline-v0/`：

- `manifest.json`：运行记录和全部指标；
- `unit_tests.log`、`eval_suite.log`：回归日志；
- `legacy_results.jsonl`：旧路径每条请求的结果；
- `raw/`：完整响应和隔离数据（不进 Git）。

## Baseline 结果

运行环境：macOS 15.6.1（arm64），Python 3.12，deepseek-chat + Spotify / Last.fm。

| 项目 | 结果 |
|---|---|
| 单元测试 | 265 passed，4 skipped |
| 50-case eval suite | 50/50 PASS |
| 旧路径请求数 | 50（`eval/queries_v1.jsonl`） |
| 推新歌 / 只回答 | 48 / 2（2 条因 DeepSeek 返回的工具参数不合法而退化，0 首） |
| 填充率（出歌数 ÷ 请求数） | 48.3%，平均每条 3.72 首 |
| 延迟 | P50 12.4 s，P95 23.1 s |
| decision summary / evidence 覆盖率 | 100% / 100%（186 首） |

分类填充率：

| 类别 | 条数 | 填充率 |
|---|---|---|
| 多样性 | 5 | 0.70 |
| 单锚点相似 | 10 | 0.57 |
| 基础 | 8 | 0.55 |
| 双锚点相似 | 6 | 0.55 |
| 版本噪声 | 4 | 0.53 |
| session 追加 | 6 | 0.36 |
| 负向约束 | 8 | 0.34 |
| UI 总结 | 3 | 0.30 |

**解读**：旧路径出歌数不稳（约一半被丢弃，负向约束和追加请求最差）、慢（中位数 12 秒），而且只能推 Spotify 上确认得到的歌。这正是重构要解决的问题。

**测不到的指标**：grounding 率和幻觉率（“先回答”路径不记录 `discover_tracks`）；长尾相关指标（旧路径没有曲库和分档）。

用新质量门禁检查 138 条历史轨迹：action 和 summary 覆盖率 100%，evidence 覆盖率只有 34%，不通过，说明旧路径的推荐大多拿不出证据。

## 已知限制

- baseline 运行时代码尚未提交，`manifest.json` 记为 `520e7d1` + `dirty: true`；实际运行的代码即 `15a0efa`；
- 旧路径 baseline 用的是给 demo-user 写的通用请求，不是 participant_001 的长尾场景，只能和新系统比较出歌稳定性、延迟、证据和幻觉，不能作为长尾效果的 baseline；
- 目前能用的两个受限环境（Claude 在 Mac 上开的隔离环境、云端工作区）都无法访问外网，凡是调外部 API 的步骤都要在 Mac 本机终端运行。

## 如何复现

```bash
cd ~/Desktop/rateyourDJ
PYTHONPATH=src python -m pytest tests -q
PYTHONPATH=src python -m rateyourdj.l7.cli run-eval-suite
PYTHONPATH=src python scripts/baseline_v0.py --run-dir runs/baseline-v0-rerun
```

## 下一步：阶段 1

- 建立 `participant_001`，确认 6 首种子歌（Pink Floyd 3 首、Oasis 3 首）；
- 从 MusicBrainz / ListenBrainz 拉曲库，计算收听量百分位和 head / mid / tail 分档；
- 为每首歌确定播放来源（Spotify / 已校验的 YouTube / 无）。

拉数据需要联网，在 Mac 本机运行，不需要 GPU。
