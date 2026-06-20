# Manual Eval Score Sheet V1

这份表是给你做真实人工评测用的，不替代内置的 `run-eval-suite`。

内置 eval suite 解决的是：
- parser / loop / tool 调用 / stop_reason / session 约束有没有回归

这份人工 eval 解决的是：
- 推荐方向对不对
- similarity 扩展像不像
- 版本噪声多不多
- 列表是否多样
- UI 摘要文案是否干净

对应的 query 清单在：
- [eval/queries_v1.jsonl](/Users/qijiazhou/Desktop/rateyourDJ/eval/queries_v1.jsonl)

## How To Run

每条 query 建议这样测：

1. 打开网页，输入 query。
2. 看前 5 首，至少试听前 2-3 首。
3. 记录结果。
4. 对推荐卡片给真实反馈：
   - `喜欢`
   - `不喜欢`
   - `跳过`
   - `收藏`
5. 如果是 `session_more` 类，严格按 `notes` 里的前置 query 顺序来测。

## Scoring Fields

每条 query 建议记录下面这些字段。

### Core Scores

- `direction_score`
  - `2` = 很对
  - `1` = 大体对，但有明显噪声
  - `0` = 跑偏

- `top3_quality`
  - `2` = 前 3 首里至少 2 首明显对
  - `1` = 前 3 首里只有 1 首对
  - `0` = 前 3 首基本不对

- `diversity_score`
  - `2` = 列表分布自然，不挤在一个艺人/支线
  - `1` = 有点重复或有点扎堆
  - `0` = 明显重复或同簇刷屏

- `version_noise_score`
  - `2` = 基本都是正常主版本
  - `1` = 有少量 live/remaster/deluxe 噪声
  - `0` = 版本噪声明显

### Binary Checks

- `hard_floor_ok`
  - `true` / `false`
  - similarity query 下，是否拦住了明显只是关键词沾边的歌

- `negative_constraint_ok`
  - `true` / `false`
  - `不要 X` 是否真的有效

- `exclude_seen_ok`
  - `true` / `false` / `n/a`
  - more/refinement 查询里，是否避开已展示结果

- `summary_copy_ok`
  - `true` / `false`
  - UI 摘要是否自然，没有把原始脏词直接打印出来

## Failure Tags

如果不满意，尽量从下面挑 1-3 个标签：

- `too_generic`
- `wrong_artist_cluster`
- `keyword_only_match`
- `too_punky`
- `not_melodic_enough`
- `too_many_live_versions`
- `too_many_remasters`
- `same_artist_repeated`
- `same_cluster_repeated`
- `family_branch_overexposed`
- `ignored_negative_constraint`
- `ignored_session_context`
- `summary_copy_dirty`
- `result_count_too_low`

## JSONL Template

你后面可以把人工标注也存成 jsonl，格式建议如下：

```json
{
  "query_id": "manual-similarity-002",
  "query": "来点更像 Oasis 的英伦摇滚，旋律一点",
  "run_date": "2026-06-20",
  "direction_score": 2,
  "top3_quality": 2,
  "diversity_score": 1,
  "version_noise_score": 2,
  "hard_floor_ok": true,
  "negative_constraint_ok": true,
  "exclude_seen_ok": "n/a",
  "summary_copy_ok": true,
  "failure_tags": [],
  "liked_track_ids": ["spotify:track:abc"],
  "disliked_track_ids": [],
  "skipped_track_ids": ["spotify:track:def"],
  "notes": "整体方向对，前两首很像 Oasis，第 4 首有点泛。"
}
```

## Quick Pass Rule

一轮人工评测里，我建议你先用这套粗规则看版本有没有明显进步：

- `direction_score` 平均值 `>= 1.4`
- `top3_quality` 平均值 `>= 1.2`
- `hard_floor_ok = false` 的 query 不超过 `10%`
- `version_noise_score = 0` 的 query 不超过 `10%`
- `same_artist_repeated` 或 `same_cluster_repeated` 不超过 `15%`

## Recommended Order

先测这 15 条，最有信号：

1. `manual-similarity-001`
2. `manual-similarity-002`
3. `manual-similarity-003`
4. `manual-similarity-011`
5. `manual-similarity-012`
6. `manual-negative-001`
7. `manual-negative-002`
8. `manual-negative-004`
9. `manual-negative-006`
10. `manual-session-002`
11. `manual-session-003`
12. `manual-diversity-002`
13. `manual-diversity-005`
14. `manual-version-002`
15. `manual-ui-002`

## What To Tune After Scoring

如果主要失败标签是：

- `keyword_only_match`
  - 继续加严 `reference_match / expanded_reference_match`

- `too_many_live_versions` / `too_many_remasters`
  - 继续加大 `version_penalty`

- `same_cluster_repeated` / `family_branch_overexposed`
  - 调 `cluster quota`、`adjacency diversity`、`family penalty`

- `result_count_too_low`
  - 适当放松 `cluster quota`
  - 不要先拆 `hard floor`

- `summary_copy_dirty`
  - 继续清洗 UI 摘要生成规则
