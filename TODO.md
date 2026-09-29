# rateyourDJ 长尾推荐重构计划

## 1. 最终目标

将现有项目改造成面向单用户的长尾音乐发现 Agent：

```text
participant_001 的种子歌曲 + 当前文本请求
                    |
                    v
            RAG 多路检索真实歌曲
                    |
                    v
       长尾相关性排序与确定性约束校验
                    |
                    v
       SFT-LoRA / GRPO 模型选择并解释歌曲
                    |
                    v
             记录曝光和真实反馈
```

第一阶段只服务 `participant_001`：

- Pink Floyd 是迷幻、前卫、艺术摇滚和氛围化作品方向的兴趣锚点；
- Oasis 是 Britpop、另类摇滚和旋律型吉他摇滚方向的兴趣锚点；
- 两条兴趣分支独立保存，不合并成笼统的“英伦摇滚”；
- 结论限定为 single-user case study，不宣称对其他用户具有统计泛化能力。

技术分工：

- RAG：让相关长尾歌曲进入候选集；
- SFT + LoRA：训练意图解析、工具调用、候选内选歌和结构化输出；
- GRPO：训练相关性、长尾性、多样性和约束之间的动态权衡；
- 规则系统：提供 baseline、程序化 reward 和模型失效时的回退。

## 2. 阶段总览

整个重构分为 **7 个阶段**，必须按依赖顺序推进：

| 阶段 | 名称 | 核心产物 |
|---|---|---|
| 0 | 冻结现状与定义契约 | baseline、版本化 schema、实验边界 |
| 1 | 单用户上下文与长尾曲库 | `participant_001`、标准化曲库、长尾分桶 |
| 2 | RAG 多路召回 | embedding、向量索引、带证据的候选集 |
| 3 | 长尾排序、反馈与离线评估 | 固定策略 baseline、曝光反馈、长尾指标 |
| 4 | SFT + LoRA | SFT 数据、adapter、微调评估 |
| 5 | GRPO | 程序化 reward、GRPO adapter、消融结果 |
| 6 | 系统集成与单用户研究 | Web 闭环、真实反馈、最终报告 |

任何阶段未达到完成标准，不进入下一阶段。

## 3. 全局边界

### 必须保留

- 保留现有 L1-L7 分层；
- 保留 Flask Web 和 `/api/v1/agent/*` 主接口；
- 保留规则排序和 rule fallback；
- 保留 DeepSeek provider，但只作数据辅助（请求改写、长尾歌 YouTube 链接搜索），不再提名歌曲；
- head/mid 歌曲保留 Spotify embed/跳转，tail 歌曲改用 YouTube 链接；不把 Spotify 内容当默认训练语料；
- 保留现有单元测试和 L7 50-case eval suite；
- 保留历史 trajectory，不删除或覆盖原始数据；
- 所有新 schema 提供迁移或向后兼容读取。

### 明确不做

- 不再使用“DeepSeek 提名歌曲 + Spotify grounding”的旧范式，该路径在阶段 3 前下线，只在 baseline 归档中保留一份结果作历史对照；
- 不把 Pink Floyd、Oasis 或 `participant_001` 硬编码进算法；
- 不构建复杂的多用户协同过滤产品；
- 不使用一个用户的数据声称模型能泛化到所有用户；
- 不把未收藏、未播放或未曝光歌曲直接视为负样本；
- 不让大模型记忆整个曲库，歌曲事实由 RAG 提供；
- 不允许 Agent 推荐候选集之外的歌曲；
- 不训练或保存隐藏思维过程；
- 不在固定 baseline 和评估集完成前开始正式 SFT/GRPO；
- 不在 3B 模型跑通前直接训练 7B/8B；
- 不以训练 reward 上升代替离线指标和真实反馈。

## 4. 阶段 0：冻结现状与定义契约

### 目标

保存当前可运行基线，先定义后续模块共享的数据契约。

### 修改哪些地方

- `data-contract.md`
- `agent-tools-contract.md`
- `api-contract.md`
- `src/rateyourdj/config.py`
- `src/rateyourdj/l7/`
- `tests/`
- 新增实验配置和 run manifest

### 需要完成

- [x] 归档当前单元测试、50-case eval 和 trajectory quality 结果；（脚本：`scripts/baseline_v0.py`，需在 Mac 本机联网运行）
- [x] ~~保存当前 L3/L4 推荐结果作为 relevance baseline~~（决定：不做规则模式 baseline；长尾 baseline 在阶段 3 新曲库上建立）；
- [x] 归档一次旧 DeepSeek 提名 + Spotify grounding 路径的结果，之后该路径下线；
- [x] 标记 `data/grpo.jsonl`、`data/sft.jsonl`、`data/dataset.jsonl` 为旧格式废弃数据（已移出 Git，本地保留），新训练数据从头生成；
- [x] 定义 `UserContextV2`、`SongProfileV2`、`RetrievalCandidateV2`、feedback 和 training sample schema；
- [x] 定义内部 song ID 与外部平台 ID 的边界；
- [x] 定义 head/mid/tail 初始阈值及版本字段；
- [x] 定义训练、验证和测试隔离原则；
- [x] 定义 run manifest：git commit、数据版本、模型、adapter、seed、配置和指标；
- [x] 将 thought coverage 门禁改为检查 action、decision summary 和 evidence；
- [x] 准备新旧 schema 迁移测试骨架。

### Baseline 结果（`runs/baseline-v0/`，2026-09-26，macOS / Python 3.12）

| 项目 | 结果 |
|---|---|
| 单元测试 | 265 passed，4 skipped |
| 50-case eval suite | 50/50 PASS |
| 旧路径：请求数 | 50（`eval/queries_v1.jsonl`，deepseek-chat + Spotify/Last.fm grounding） |
| 旧路径：推新歌 / 只回答 | 48 / 2（2 条因 DeepSeek 工具参数不合法而退化成只回答，0 首） |
| 旧路径：填充率（出歌数 ÷ 请求数） | 48.3%，平均 3.72 首 |
| 旧路径：分类填充率 | 多样性 0.70、单锚点相似 0.57、基础 0.55、双锚点 0.55、版本噪声 0.53、session 追加 0.36、负向约束 0.34、UI 总结 0.30 |
| 旧路径：延迟 | P50 12.4 s，P95 23.1 s |
| 旧路径：decision summary / evidence 覆盖率 | 100% / 100%（186 首） |
| 不可得指标 | grounding 率、幻觉率（先回答路径不记录 `discover_tracks`）；长尾指标（旧路径无曲库和分档） |

说明：manifest 中 `git.dirty = true`，运行时代码为 commit `520e7d1` 加上阶段 0 的未提交改动（`scripts/baseline_v0.py`、`l6/tools.py` 的 discovery_service 修复等），提交后以该 commit 为准。

### 本阶段不动

- 不修改召回和排序公式；
- 不下载全量数据；
- 不生成 embedding；
- 不训练模型；
- 不改 Web 布局。

### 完成标准

- [x] 当前 baseline 已归档；
- [x] 所有 V2 schema 有示例 JSON 和字段说明；
- [x] 新设计不破坏现有 API 核心结构；
- [x] 只看契约文档即可理解数据流。

## 5. 阶段 1：单用户上下文与长尾曲库

### 目标

建立 `participant_001`，并获得可计算热度、可供 RAG 使用的标准化曲库。

### 修改哪些地方

- `src/rateyourdj/l1/`
- `src/rateyourdj/l2/`
- `src/rateyourdj/collectors/`
- 新增 `src/rateyourdj/data_pipeline/`
- `data/user_profiles/`
- processed catalog 目录
- `.gitignore`
- L1/L2 与数据管线测试

### 需要完成：用户上下文

- [x] 初始化 `participant_001`；
- [x] 确认 Pink Floyd 与 Oasis 的具体种子歌曲；
- [x] 初始候选：`Time`、`Wish You Were Here`、`Shine On You Crazy Diamond`、`Live Forever`、`Slide Away`、`Champagne Supernova`；
- [x] 将 L1 收缩为轻量上下文：种子、已听/已推荐、排除项、探索强度和最近反馈（`data/users/<id>/context.json`；现有 L6 推荐服务改读 V2 上下文放到阶段 2/3）；
- [x] 保留 `load_user_context(user_id)` 通用接口；
- [x] 流派、艺人和标签偏好按需计算，不维护重型手工画像；
- [ ] 后续允许用该用户的网易云收藏扩充种子，第一版不依赖逆向登录接口。

### 需要完成：歌曲目录

- [x] 使用 MusicBrainz 构建曲库（recording/artist/release/tag），ListenBrainz 提供收听量和相似艺人/歌曲数据；
- [x] 曲库范围从两个兴趣分支出发扩展（相似艺人多跳），不导入全量 MusicBrainz；
  - 决定（2026-09-27）：v1 维持种子扩展（相似艺人 2 跳 + 分支标签艺人，每个艺人前 10 首 + 分层抽 30 首冷门曲目）。已知局限：共同收听会带入风格较远的主流艺人，且相关性部分在建库时内置。后续可换成“按风格圈定、与用户无关的大曲库”（MusicBrainz 导出离线筛选），或在阶段 3 作为对照。
- [ ] ListenBrainz 公开收听日志同时作为阶段 4/5 隐藏正样本来源（与训练 reward 严格隔离）；
- [x] 记录数据源版本、下载日期、许可和用途；
- [x] 实现 MusicBrainz 导入与字段标准化（API + 磁盘缓存，遵守 1 req/s 限速；dump 只用于全网热度抽样）；
- [x] 实现 ListenBrainz 收听量与相似度数据导入；
- [x] 扩展 `SongProfile`：外部 ID、播放/收听数据、来源和许可（音频特征不可得：Spotify 已不向新应用开放 audio-features，暂缺）；
- [x] 基于 ListenBrainz 听众数计算全网（抽样估计）及流派内 popularity percentile；
  - 决定（2026-09-27）：分档改为 `bucket-v2` = 全网前 1% head / 前 1–10% mid / 其余 tail（v1 的 10%/50% 使 tail 仅 0.3%）。
- [x] 生成 `head`、`mid`、`tail` 分桶；
- [x] 内部 ID 与外部 URI 分离，解决冒号文件名校验冲突；
- [x] 为每首歌确定播放来源：head/mid 用 Spotify（ISRC 精确匹配优先），tail 用 YouTube 链接；按需解析（只解析要展示的歌），结果缓存；
- [x] ~~YouTube 链接由 LLM API 联网搜索获得~~ 改为 YouTube Data API 搜索（每天 ≤ 95 次），经 `videos.list` 校验公开可嵌入且标题/艺人匹配，优先官方 Topic/艺人频道，未通过则标为“无可播放链接”，不凑数；
- [x] 链接结果缓存到曲库，记录搜索时间和校验状态；
- [x] 输出缺失率、重复率、匹配率和分桶报告；
- [x] 原始数据、权重、用户隐私和索引不提交 Git。

### 本阶段不动

- 不修改 L3/L4 行为；
- 不创建向量索引；
- 不制作 SFT/GRPO 数据；
- 不接入多个真实用户；
- 不重做前端。

### 完成标准

- [x] `participant_001` 可通过 `load_user_context` 读取（推荐服务接入见阶段 2/3）；
- [x] Pink Floyd 与 Oasis 两条兴趣分支可区分；
- [x] 曲库可稳定导入并重复生成；
- [x] 每首可用歌曲具有来源和长尾分桶；
- [x] 旧 JSON 仍可读取；
- [x] L1/L2 新增测试通过。

## 6. 阶段 2：RAG 多路召回

> 文档备忘：`stage-2.md` 需包含一节 RAG 原理说明（定义、离线建库与在线检索流程、切块、稠密/稀疏/混合检索、rerank、评估指标、常见问题与优化、RAG vs 微调、与本项目的对应），用于面试准备。
> 决定（2026-09-27）：embedding 模型用 `BAAI/bge-m3`。

### 目标

让文本请求和种子音乐共同参与检索，使相关长尾歌曲进入候选集。

### 修改哪些地方

- `src/rateyourdj/l2/` 的 RAG 文档字段
- `src/rateyourdj/l3/`
- 新增 embedding/index 模块
- `src/rateyourdj/l6/query_filters.py`
- RAG 配置、索引 manifest 和检索测试

### 需要完成

- [x] 根据艺人、风格标签、年代生成歌曲文档（`doc-v2`；刻意不写歌名/专辑名，见实现记录；地区与音频特征数据不可得）；
- [x] 文档只使用结构化事实或有来源的描述；
- [x] 选择并锁定多语言 embedding 模型（`BAAI/bge-m3`，1024 维）；
- [x] 为歌曲文档和种子歌曲生成 embedding；
- [x] 分别构建 Pink Floyd 与 Oasis 两个兴趣中心；
- [x] 建立向量索引并记录版本（numpy 精确余弦检索 + manifest）；
- [x] 实现四路召回：规则（基于 V2 标签重新实现，旧 L3 保留未动）、语义、长尾专用、探索；
- [ ] 查询融合文本、两个兴趣中心、最近反馈和探索强度（文本、兴趣中心、点名种子、探索强度已完成；“最近反馈”待阶段 3 有反馈数据后接入）；
- [x] 为各通道设置候选配额（随探索强度变化）；
- [x] 去重并校准不同通道分数（按排名校准 + 多通道命中加分）；
- [x] 候选输出 channel、score、tail score 和 evidence（`RetrievalCandidateV2`，全部通过校验）；
- [x] 无索引时回退到规则召回；
- [x] 建立偏 Pink Floyd、偏 Oasis、交集和不同探索强度的固定查询集（`eval/retrieval_queries_v1.jsonl`，13 条）。

### 实现记录：RAG 是如何做的（2026-09-27）

这一节记录阶段 2 实际的实现方式、中途的失败和修正，便于复盘和讲解。代码在 `src/rateyourdj/rag/`，详细说明和 RAG 原理见 `stage-2.md`。

**1. 知识库与检索文档（离线）**

- 知识库就是阶段 1 的曲库（14,065 首），每首歌天然是一“块”，不需要切块；
- 每首歌生成一段只含事实的文档（`documents.py`）。最终版 `doc-v2` 形如：`A 1970s song by Hawkwind. Style: space rock, psychedelic rock, progressive rock.`；
- 听众数、分档不写进文档：它们是元数据，由长尾/探索通道单独使用，写进文本会干扰语义匹配。

**2. 向量化与索引（离线）**

- 用 `BAAI/bge-m3`（多语言，1024 维，Mac 上用 MPS 加速）为每段文档编码，约 5 分钟；
- 按 1,024 条分块编码、分块保存，中断可续跑；文档内容变了会自动重编码；
- 不用向量数据库：1.4 万 × 1024 的矩阵约 57 MB，numpy 矩阵乘法做精确余弦检索，单次约 60 毫秒，结果可复现；
- 索引版本 = 曲库版本 + 模型 + 文档版本，例如 `idx-catalog-20260926-baai-bge-m3-doc-v2`，写入 manifest。

**3. 查询构造（在线）**

- **兴趣中心**：每条分支的种子歌向量取平均，得到 Pink Floyd / Oasis 两个中心；
- **点名种子解析**：请求里提到种子歌名或种子艺人（如“像 Time 那样”），就用该种子的向量代替这个词，并从文本中删掉它（整词匹配，“Timeless”不算）；
- **分支权重**：指定了分支就用它；点名了种子就按点名的分支；否则看请求文本的向量更靠近哪个中心（softmax）；
- **查询向量** = 兴趣中心 + 请求文本向量（+ 点名种子向量）的加权和。

**4. 混合相关性**

- 相关性 = 0.5 × 稠密（向量）相关性的百分位 + 0.5 × 标签相关性的百分位；
- 标签相关性 = 歌曲标签与“分支种子标签 + 请求中提到的风格”的余弦相似度，中文风格词（迷幻、前卫、英伦……）先映射成英文标签；
- 这就是稠密 + 稀疏的混合检索：单靠向量会被字面撞词带偏，单靠标签又太粗。

**5. 四路召回与融合**

| 通道 | 选法 | 探索强度 0.5 时的名额（共 30） |
|---|---|---|
| 长尾 tail | 只在 tail 分档里选，且混合相关性须排进前 15% | 10 |
| 探索 explore | 相关性在 60%–85% 区间的 mid/tail 歌，均匀间隔抽取，每艺人 1 首 | 3 |
| 语义 semantic | 混合相关性最高的歌 | 11 |
| 规则 rule | 纯标签重合度（不需要向量，也是无索引时的回退） | 6 |

- 名额随探索强度变化：长尾 = 30 × (0.2 + 0.3e)，探索 = 30 × 0.2e，规则 = 30 × 0.2，其余给语义；
- 按“长尾 → 探索 → 语义 → 规则”的顺序填名额，已选过的歌跳过（去重）；每个艺人每通道最多 2 首、全局最多 3 首；
- 分数校准：各通道原始分数量纲不同，统一换成“1 − 排名 / 列表长度”，再给同时命中多个通道的歌加分；
- 排除：种子歌、已听、已推荐、用户排除的歌和艺人；
- 每首候选附证据：最像哪首种子（向量相似度）、共同风格标签、是否符合请求风格、听众数与分档。每条证据都能追溯到曲库或种子；
- 输出 `RetrievalCandidateV2`，同一请求得到同一个 `candidate_set_id` 和同样的结果。

**6. 评估方法**

- 13 条固定查询：偏 Pink Floyd、偏 Oasis、两者交集、同一请求在探索强度 0.1/0.5/0.9 下的对比；
- 还没有真实标注，所以用两个独立裁判交叉判断“相关”：向量裁判（与目标分支兴趣中心的相似度）和标签裁判（与种子标签的重合度），“相关”= 该裁判下曲库前 15%；
- 对比“只有规则召回”和“完整 RAG”，另查可追溯性、证据有效性、分支重叠、枢纽歌（同一首歌出现在过半查询中）。

**7. 迭代过程**

- **第一版（`doc-v1`，文档以歌名开头）失败**：RAG 只在向量裁判下赢，标签裁判下明显输给规则召回（相关长尾 4.5 vs 9.7）。看样例发现三个问题：
  1. 字面撞词：“Time” → *Take Your Time*，“Supernova” → *Supernova*，“progressive” → *Progress?*；
  2. 模型不知道 “Time” 指 Pink Floyd 的那首，兴趣中心也被种子歌名污染；
  3. 枢纽歌（Embrace – *Today* 几乎每个查询都出现）；
- **修正**：文档去掉歌名和专辑名（`doc-v2`）、点名种子解析、混合相关性、评估加入枢纽歌指标；
- **第二版结果**：两个裁判下都不输规则召回。

| 指标（13 条查询平均） | 规则召回 | RAG `doc-v1` | RAG `doc-v2` |
|---|---|---|---|
| 相关长尾数（标签裁判） | 10.7 | 4.5 | **11.4** |
| 相关长尾数（向量裁判） | 1.5 | 9.2 | **9.8** |
| 相关候选占比（标签 / 向量） | 0.94 / 0.43 | 0.60 / 0.79 | **0.95 / 0.84** |
| 分支判对率（标签 / 向量） | 0.997 / 0.96 | 0.75 / 0.83 | **0.98 / 0.98** |

**8. 遗留到阶段 3**

- 枢纽歌仍偏多（12 首歌出现在过半查询中）：靠记录已推荐歌曲 + 排序的多样性惩罚缓解；
- “两者之间”类查询偏向 Oasis 一侧；
- 少量版本曲漏网（如 *(extended)*），需补进版本词表并重建曲库；
- 查询融合“最近反馈”、召回接入 L6 Agent。

### 本阶段不动

- 不让 LLM 生成曲库外歌曲；
- 不用 GRPO 控制召回；
- 不修改真实反馈权重；
- 不删除原有标签召回；
- 不训练 embedding 模型。

### 完成标准

- [x] 所有候选可追溯到曲库；
- [x] RAG 相比原召回提高 Tail Candidate Recall（相关长尾数：标签裁判 10.7 → 11.4，向量裁判 1.5 → 9.8）；
- [x] 长尾候选满足最低语义相关性（混合相关性排名前 15%）；
- [x] 两条兴趣分支产生可区分结果（两分支候选零重叠，分支判对率 0.98）；
- [x] 规则回退仍可用。

## 7. 阶段 3：长尾排序、反馈与离线评估

### 目标

先建立确定性强 baseline，并形成后续 SFT/GRPO 可复用的指标和反馈闭环。

### 修改哪些地方

- `src/rateyourdj/l4/`
- `src/rateyourdj/l5/`
- `src/rateyourdj/l6/` 的候选约束和 trajectory
- `src/rateyourdj/l7/`
- Web/API 的反馈字段
- L4-L7 与 API 测试

> 实际落地：排序、校验、指标放在新包 `src/rateyourdj/ranking/`，ReAct agent 与曝光/反馈服务放在新包 `src/rateyourdj/agent/`；旧 L4–L7 只做删减（DeepSeek 提名链路已删除），Web/API 的 `/api/v1/agent/recommend` 与 `/feedback` 对 V2 用户改走新服务。

### 需要完成：排序

- [x] 增加 relevance、tail score、tail relevance、novelty、diversity 和 exploration fit；
- [x] 使用 `tail_relevance = relevance * tail_score`；
- [x] 增加重复艺人、已听歌曲和过度相似惩罚；
- [x] 实现 relevance-only baseline；
- [x] 实现固定混排 baseline，例如 `6 relevant + 3 long-tail + 1 exploration`；
- [x] 根据探索强度调整配额并保留上下限；
- [x] 在 `score_breakdown` 保存全部分量和权重版本；
- [x] Agent 只能在候选集内选歌，违规时回退规则排序。

### 需要完成：反馈

- [x] 增加 impression、rank、bucket、retrieval channel 和 strategy version；
- [x] 记录播放开始、时长、完成、快速跳过、喜欢和收藏；
- [x] 记录此前是否听过、相关性、发现价值和“太陌生”；
- [x] 区分“不喜欢歌曲”和“当前不想探索”；
- [x] 没有曝光的歌曲不能成为负反馈；
- [x] feedback 关联 trajectory、策略版本和候选来源。

### 需要完成：评估

- [x] 增加 Recall@K、NDCG@K；
- [x] 增加 Tail Candidate Recall、Tail Coverage、Tail Exposure；
- [x] 增加 Catalog/Artist Coverage；
- [x] 增加 Intra-list Diversity、Novelty 和 Serendipity；
- [x] 增加 Hallucination Rate、Constraint Pass Rate 和 Evidence Accuracy；
- [x] 按兴趣分支、长尾分桶和探索强度分层报告；
- [x] 固定测试集、候选集、seed 和指标版本，并输出 JSON/CSV。

### 实现记录：排序、Agent 与评估是如何做的（2026-09-27）

代码在 `src/rateyourdj/ranking/`（排序、校验、指标、弱正例、评估）和 `src/rateyourdj/agent/`（工具、ReAct 循环、推荐服务）。详细说明见 `stage-3.md`。

**1. 排序（`ranker.py`，权重版本 `rank-w1`）**

- 每个候选算 6 个分量：relevance（阶段 2 的混合相关性）、tail_score（全网冷门程度）、tail_relevance = relevance × tail_score、novelty、diversity（与已选歌的最大相似度的补）、exploration_fit；
- 惩罚：同艺人已选（只对混排生效，软惩罚 0.3/首）、用户听过（1.0）、近重复（相似度 > 0.97，0.5）；另有硬上限：每位艺人最多 2 首；
- 两个确定性策略：`rag-rel-v1` 只按相关性；`rag-tailmix-v1` 按配额混排，count=10、探索 0.5 时为 6 相关 + 3 长尾 + 1 探索，配额随探索强度变化；
- 每首歌的 `score_breakdown` 保存全部分量、惩罚和权重版本，结果可完全复算。

**2. 校验器（`validator.py`）**

- 检查：候选集外、重复、被排除、数量、艺人上限、最少长尾数、reason 为空、evidence_refs 越界；
- 除了错误文本，还返回结构化的 `violations`，供 agent 修正时使用。

**3. ReAct agent（`agent/`）**

- 工具：`get_user_context`、`retrieve_candidates`、`get_track_facts`、`rank_candidates`、`submit_recommendations`，每次调用必须带可见的 `summary`（不保存隐藏思维）；
- 模型走 OpenAI 兼容接口：现在是 DeepSeek，阶段 4/5 换成自己微调的模型时只改 `AGENT_LLM_*` 环境变量，循环不变；
- 提交后由校验器检查；不通过就把**具体修改要求**（去掉哪几首、可换哪几首）作为工具结果返回，最多修正 2 次；仍失败、模型出错或步数（8）用完时，退回确定性排序 `rag-tailmix-v1`，保证每次都有合法结果；
- 推荐服务 `RecommenderV2`：每首展示的歌写一条 `ImpressionV2`，反馈必须引用 impression_id（没有曝光就没有反馈），反馈区分“不喜欢这首”和“当前不想探索”。

**4. 离线评估（`evaluate.py`，指标版本 `metrics-v1`）**

- 13 条固定查询（与阶段 2 相同），固定索引与候选集；
- “正确答案” `weak-pos-v1`：ListenBrainz Labs 相似录音（基于真实收听会话），只用于评估，不进任何 reward；
- 指标：Recall/NDCG@10、候选召回、长尾曝光/覆盖、新颖度、列表内多样性、意外发现度、幻觉率、约束通过率、证据准确率、曲库/艺人覆盖；按兴趣分支、探索强度、分档分层；输出 JSON / CSV / Markdown。

**5. 迭代过程**

| 版本 | 改动 | 长尾曝光 | 退回率 | 问题 |
|---|---|---|---|---|
| ranking-v1 | 第一版 agent 提示词 | 74% | 0/13 | 过度偏向长尾，相关性下降 |
| ranking-v2 | 平衡提示词 + 按探索强度给目标长尾区间 | 42% | 2/13 | 两次因同艺人超上限退回 |
| ranking-v3 | 校验失败时给具体修改要求，修正次数 1 → 2 | 39% | 0/13 | — |

**6. 结果（`runs/ranking-v3/`）**

| 指标 | rag-rel-v1 | rag-tailmix-v1 | agent-react-v1 |
|---|---|---|---|
| 长尾曝光 | 23.1% | 46.9% | 39.2% |
| 平均相关性 | 0.9955 | 0.9823 | 0.9873 |
| Recall@10 | 0.115 | 0.092 | 0.100 |
| 意外发现度 | 0 | 0.439 | 0.277 |
| 幻觉率 | 0 | 0 | 0 |
| 约束通过率 | 100% | 100% | 100% |
| 证据准确率 | 100% | 100% | 97.7% |

验收（事先约定）：固定混排相关性 = 只看相关性的 98.7%（要求 ≥ 90%），长尾曝光 +103%（要求 ≥ +50%），**通过**。

**已知局限**

- 弱正例全部是 head 歌（曲库里 Pink Floyd 81 首、Oasis 131 首，tail 为 0），所以 Recall/NDCG 衡量的是“熟悉度”，长尾策略 Recall 下降是预期的，不能用它判断长尾推荐质量；
- 热门歌集中（hubness）：13 × 10 次推荐只有 72–83 首不同歌；
- agent 的 `summary` 和 `reason` 不经校验，偶有数字不符（如说“5 首长尾”实为 6 首）；证据准确率只检查数字，3/130 条理由引用了证据里没有的数字；
- agent 单次约 9 秒（DeepSeek 多轮工具调用），平均 9.2 秒、最长 11.3 秒。

### 本阶段不动

- 不开始模型微调；
- 不让 LLM 决定 reward；
- 不删除现有 L5 reward；
- 不用最终测试轮次调权重；
- 不把固定混排描述成学习策略。

### 完成标准

- [x] 排序结果可完全复算；
- [x] 候选集外推荐率为 0；
- [x] 固定长尾策略提高长尾曝光，相关性下降在预设范围内；
- [x] 每次推荐可关联曝光和反馈；
- [x] 已形成训练可复用的 oracle、约束校验器和指标函数。

## 8. 阶段 4：SFT + LoRA

### 目标

将开放权重模型微调成稳定的 rateyourDJ Agent，训练行为和协议，不训练模型记忆曲库。

### 修改哪些地方

- `src/rateyourdj/training/dataset.py`
- `src/rateyourdj/training/sft.py`
- `src/rateyourdj/training/cli.py`
- `pyproject.toml` training extras
- SFT schema、生成器、校验器和评估脚本
- L6 开放权重模型 provider

> 决定（2026-09-27）：
> - 基座模型 `Qwen/Qwen3-4B-Instruct-2507`（Apache 2.0，非思考版，原生 tool calling；略超 3B，但 32GB 显存可做 bf16 LoRA，不需要量化）；冒烟用 `Qwen3-1.7B`；
> - 训练机：AutoDL RTX 5090 D 32GB（需 PyTorch ≥ 2.7 / CUDA ≥ 12.8 镜像）；推理评估用 vLLM（OpenAI 兼容接口，改 `AGENT_LLM_*` 即可替换 DeepSeek）；
> - 数据：oracle 程序生成为主，DeepSeek 只改写请求模板；
> - 划分改为按“说法 + 候选艺人”隔离（单用户下无法按种子艺人隔离），见 data-contract 9.9。

### 需要完成：数据

- [x] 选择许可允许研究和衍生训练的中文/多语言模型（Qwen3-4B-Instruct-2507，见上）；
- [x] 将 SFT 数据改为 chat/tool-call 格式（OpenAI 格式，含 `tools`；system/user 与推理逐字一致）；
- [x] 覆盖意图解析、检索调用、候选选择、证据解释和错误修正（`get_user_context` / `rank_candidates` / repair 样本）；
- [x] 生成偏 Pink Floyd、偏 Oasis、交集、排除条件和不同探索强度场景（23 个请求模板 × 探索强度 × 数量 × 排除 × 续推）；
- [x] 标准答案主要由阶段 3 的规则/oracle 产生（`oracle-v1` = `rag-tailmix-v1` + 证据模板理由）；
- [x] 强模型只辅助请求改写，不负责定义正确歌曲（`paraphrase` 命令，只改写模板，占位符必须保留）；
- [x] 校验候选 ID、数量、约束和 evidence（每条样本独立复查，理由中的数字必须来自所引证据）；
- [x] 按说法和候选艺术家隔离 train/validation/test（`split-v1`）；
- [x] 在 Mac 上用 bge-m3 生成 500 条冒烟数据并抽查；
- [ ] ~~验证/测试集的隐藏正样本来自 ListenBrainz/MSD 中与 `participant_001` 口味相近用户的真实收听日志~~ → 移到阶段 5（那里用于 checkpoint 选择）；阶段 4 用 oracle 对比 + 阶段 3 的 `weak-pos-v1`；
- [x] ~~人工抽查至少 300 条~~ 决定（2026-09-28）不做：每条样本都经程序独立复查，模型评估也未发现数据问题；冒烟阶段的人工查看已发现并修正了热门歌集中和改写数量词问题；
- [x] 第一版目标 8,000-10,000 条高质量样本（`sft-v1`：train 6,436 / val 756 / test 821）。

### 需要完成：训练与评估

- [x] ~~支持 QLoRA~~ 改为 bf16 LoRA（32GB 显存够用，不需要量化）；锁定依赖下限和 target modules（全部线性层）；只对 weight ≠ 0 的 assistant 消息计算损失（`training/sft_lora.py`）；
- [x] 在线评估脚本：重建工具环境后跑真实 ReAct 循环，对比 oracle（`training/sft_eval.py`）；服务器脚本 `scripts/pack_for_gpu.sh`、`scripts/gpu_sft.sh`；
- [x] 先用 500 条数据 smoke run（改为直接用 Qwen3-4B-Instruct-2507：1.7B 是思考/非思考混合版，聊天模板难以与推理对齐）；结果见 stage-4.md 第五节：参数与协议大幅提升，但首次提交的约束错误变多 → 检索每艺人上限改为 2、加大 15 首与改错样本比例；
- [x] 执行正式 SFT（6,436 条，1 epoch，805 步，4.5 小时，验证 loss 0.026）；
- [x] 保存 adapter、tokenizer、配置、曲线和 run manifest（`runs/sft-v1/`，adapter 不进 Git）；
- [x] 增加兼容 OpenAI 协议的自托管 provider（vLLM + `AGENT_LLM_*`，无需改代码）；
- [x] 保留 DeepSeek 和 rule provider；
- [x] 比较 base model 与 SFT-LoRA（`runs/sft-eval-{base,lora}-v1/`）；
- [x] 评估工具准确率、JSON 合法率、候选外歌曲率、约束和 evidence；
- [x] 检查两个兴趣分支是否模式坍塌（按单分支 / 两分支 / 无内容 / 固定查询分组，见 stage-4.md）；
- [x] 检索加分支守卫：请求点名的种子决定分支，与模型传的分支矛盾时以点名为准（修正“像 Champagne Supernova”被判到 pink_floyd）。

### 本阶段不动

- 不修改曲库事实；
- 不把历史 thought 当监督目标；
- 不使用 GRPO；
- 不使用最终真实用户测试数据训练；
- 不取消规则 fallback。

### 完成标准

- [x] SFT-LoRA 在冻结测试集上优于 base model（事先登记的门槛全部达到，见 stage-4.md 第六、九节）；
- [x] 候选集外推荐率保持为 0；
- [x] 工具、格式、约束和 evidence 指标达到阈值；
- [x] SFT checkpoint 可被阶段 5 加载（adapter 已由 vLLM 加载并完成评估）。

## 9. 阶段 5：GRPO

### 目标

从 SFT checkpoint 出发，学习根据场景动态平衡相关性、长尾曝光和多样性。

### 修改哪些地方

- `src/rateyourdj/training/grpo.py`
- `src/rateyourdj/training/dataset.py`
- 新增 reward、completion parser 和 reward 测试
- GRPO 配置、训练和离线对比脚本

### 需要完成：数据与 Reward

- [x] GRPO 样本改为 `user_context + request + candidates + constraints + hidden positives`；
- [x] prompt 不包含唯一标准输出（oracle 只在 `eval_only`，训练视图里去掉）；
- [x] 围绕一个用户构造多种场景，不把场景数量描述成独立用户数；
- [x] ~~相近用户的真实收听日志~~ → 改为两跳共听（ListenBrainz Labs similar-recordings，`hidden-pos-v1`）：单用户没有“相近用户”可取，共听是真实会话行为的聚合；
- [x] 隐藏正样本严格不进入 reward 计算，只用于验证集、测试集和 checkpoint 选择；
- [ ] 用 ListenBrainz 中 tail 分桶歌曲构造长尾任务 → 未做：共听数据里没有 tail 歌曲（0 首）；长尾只通过 `tail_band` 约束和冷门曝光体现；
- [ ] ~~第一版目标 2,000-3,000 个 prompt，每个 20-30 个候选~~ → 实际 800 / 100 / 200，每题 30–40 个候选（受 6–8h 预算限制）；
- [x] GRPO 数据从头构建：现有 `data/grpo.jsonl` 仅 6 组旧 trajectory 反馈，大多 reward 为 0，废弃不用；
- [x] 删除当前 completion 文本完全匹配的查表 reward；
- [x] 解析任意新 completion 的结构化推荐；
- [x] 实现 relevance、tail relevance（基于 embedding 与曲库事实，不使用隐藏正样本）；
- [x] 实现 diversity、novelty 和 evidence correctness；
- [x] 实现候选外歌曲、重复、格式和约束惩罚；
- [x] 归一化、截断并版本化（`reward-v1`）每个 reward 分量；
- [x] 添加防止堆满冷门歌、伪造分数和超长输出的对抗测试；
- [ ] 用户开发反馈只用于检查 reward 排序是否符合人类判断 → 未做，顺延到阶段 6。

### 需要完成：训练与评估

- [x] 从阶段 4 的 SFT checkpoint 开始（合并为 `rateyourdj-sft-v1`）；
- [x] 先完成 smoke run（两轮；第二轮加提交前缀预填）；
- [x] 监控 reward 分量、KL、非法率、输出长度和模式坍塌；
- [x] 保存最佳 checkpoint（val 上按 C 选出 ckpt-100）；
- [x] 比较 SFT、SFT + 固定混排、SFT + GRPO（见 stage-5.md 第四节）；
- [x] 分别报告相关性、长尾曝光、多样性和约束指标。

### 本阶段不动

- 不修改冻结测试集；
- 不使用最终 case study 反馈训练；
- 不让大模型充当唯一 reward judge；
- 不取消确定性候选校验；
- 不以训练 reward 更高直接判定成功。

### 完成标准

- [ ] GRPO 在冻结测试集上优于 SFT + 固定混排的预设综合指标 → **未达到**：C 0.4818 vs 0.5053，CI [−0.049, 0.000]；
- [x] 相关性没有不可接受的下降（0.984 vs 0.975）；
- [x] 候选外推荐率仍为 0（合法率 99%；2 条不合法：重复歌曲 1、evidence 引用无效 1）；
- [x] 如未超过固定策略，保留并如实报告结论：阶段 6 默认用 SFT + 固定混排，GRPO 作为对照策略。

## 10. 阶段 6：系统集成与单用户研究

### 目标

接入最佳策略，对 `participant_001` 进行受控 case study，并完成最终消融。

### 修改哪些地方

- `src/rateyourdj/web/`
- API 的 evidence、bucket、channel 和 strategy version
- 用户反馈交互
- L7 实验汇总
- `readme.md`、研究报告和运行文档

### 需要完成：产品与研究

- [x] Web 默认加载 `participant_001`，API 仍保留 `user_id`；
- [x] 提供探索强度控制；
- [x] 标记“熟悉相关”“长尾发现”“探索”来源；
- [x] 展示可核验的简短理由，不展示 reward 或训练术语；
- [x] 提供“听过/没听过”“相关”“愿意收藏”“太陌生”反馈（另有发现价值、不要这首并区分原因、收听时长自动上报）；
- [ ] 必要时用该用户网易云收藏扩充种子，凭证只用于一次性导入 → 暂不需要；
- [x] 提供参与者数据删除能力（两档：曝光与反馈 / 全部）；
- [x] 保持现有 API 主结构和 Spotify 播放（另加 YouTube；推荐时按需查找并缓存播放源）；
- [ ] 将开发轮次与最终测试轮次按时间隔离 → 机制已完成（`--phase dev|final` 写进每条记录），待实际执行；
- [x] 在线对比采用两两交错（interleaving）：同一次请求把两个策略的结果交错混排，按反馈归因到策略（team-draft；模型策略待部署后接入 `arm_runners`）；
- [x] 随机化策略出现顺序（每轮掷硬币 + A/B 标签随机映射）；
- [ ] 覆盖偏 Pink Floyd、偏 Oasis、交集和不同探索强度；
- [ ] 记录曝光、时长、熟悉度、相关性、收藏意愿和发现价值；
- [ ] 明确 `n=1`，歌曲评分数量不能写成用户样本量。

### 最终消融

离线：六组全部在冻结测试集上评估。在线：只做相邻两两交错对比——B vs A（RAG）、C vs B（固定长尾混排）、E vs C（SFT）、F vs E（GRPO）。


- [ ] A：现有 relevance baseline；
- [ ] B：RAG + relevance；
- [ ] C：RAG + 固定长尾混排；
- [ ] D：RAG + SFT-LoRA；
- [ ] E：RAG + SFT-LoRA + 固定混排；
- [ ] F：RAG + SFT-LoRA + GRPO；
- [ ] 分别说明 RAG、SFT 和 GRPO 的增量价值；
- [ ] 明确数据许可、单用户限制、平台偏差和匹配误差。

### 本阶段不动

- 不再使用最终测试反馈调模型或 reward；
- 不扩展到多个真实用户；
- 不重构与实验无关的页面和服务；
- 不夸大单用户结论；
- 不隐藏 GRPO 未超过规则 baseline 的结果。

### 完成标准

- [ ] Web 稳定完成检索、推荐、播放和反馈闭环；
- [ ] 每条推荐可追溯到上下文、候选来源、策略和模型版本；
- [ ] 完成全部消融并保存可复现配置；
- [ ] 报告明确表述为 single-user case study；
- [ ] 原有测试、50-case eval 和新增长尾评估通过。

## 11. 模块改动总表

| 模块 | 要修改 | 保留不动 |
|---|---|---|
| L1 | 改成轻量 `UserContext`，加入种子、探索强度、曝光和反馈 | 保留 `user_id` 与 JSON store 边界 |
| L2 | 增加 MusicBrainz/ListenBrainz ID、热度、分桶、播放来源（Spotify/YouTube）、RAG 文档引用 | 保留严格校验、merger 和 store 职责 |
| L3 | 增加文本、种子、长尾和探索召回，输出 evidence | 保留标签召回作为 baseline/fallback |
| L4 | 增加 tail relevance、novelty、diversity 和固定混排 | 保留确定性打分与 score breakdown |
| L5 | 增加 impression、时长、熟悉度和发现价值 | 保留 like/skip/favorite reward |
| L6 | 接入微调模型，限制候选内选择，输出结构化策略；下线 DeepSeek 提名 + Spotify grounding（已完成，新 agent 在 `src/rateyourdj/agent/`） | 保留工具、guards、trajectory 和 rule fallback |
| L7 | 增加长尾、覆盖、多样性和消融指标 | 保留 50-case eval 与导出能力 |
| training | 重构 SFT schema；GRPO 改为程序化 reward | 保留 TRL/PEFT 入口和惰性依赖设计 |
| Web/API | 增加探索控制、来源标记和实验反馈 | 保留主界面、API 主结构和 Spotify embed |

## 12. 立即开始的任务

- [ ] 完成阶段 0 baseline 归档；
- [ ] 确认 6 首初始种子是否合适；
- [ ] 将 V2 schema 写入契约文档；
- [ ] 确定 MusicBrainz/ListenBrainz 数据范围（dump vs API、种子扩展跳数）；
- [ ] 确定 YouTube 链接搜索用的 LLM API 与校验方式；
- [ ] 确定第一版 embedding 模型；
- [ ] 确定第一版 0.5B-3B 开放权重模型和 GPU；
- [ ] 先用小数据完成阶段 1-5 smoke pipeline，再处理全量数据。
