# rateyourDJ Agent 重构设计文档

> 目标:把当前"基于本地候选库的分层推荐管线"重构为"以 LLM 为发现引擎、
> 以外部 API 做事实落地的对话式音乐 DJ Agent"。
>
> 本文档是动手写代码前的设计稿,供 review。确认后再按阶段实现。

---

## 0. 一句话定位

> rateyourDJ 是一个对话式音乐推荐 Agent。用户用自然语言提需求,DeepSeek 结合
> 用户长期画像生成候选歌曲,再通过 Spotify / Last.fm / MusicBrainz 做事实落地
> 与去幻觉,最后结合用户记忆排序、生成可解释的推荐理由,并支持试听与反馈闭环。

简历可写的一句话:
> "Rebuilt a layered local recommender into a conversational, tool-using DJ agent
> where an LLM acts as a generative discovery engine grounded by external music APIs."

---

## 1. 核心架构转变

### 1.1 三种推荐范式对比

```
传统推荐(项目当前形态)
  用户收藏 → 扫描本地 284 首 → 加权相似度 → 排序
  问题:候选被锁死在本地库,本质是检索系统,不是 Agent

在线 API 召回
  收藏种子 → Last.fm track.getSimilar → 拿候选 → 排序
  特点:候选动态,但发现逻辑由 API 决定,LLM 只做参数解析

LLM-as-DJ(本次目标)
  用户的话 + 用户画像 → DeepSeek 用音乐知识生成候选(artist + title)
                       → 外部 API 验证落地(去幻觉)
                       → 用户记忆排序 + 生成理由
  特点:发现能力来自 LLM 的音乐知识 + 个性化画像,API 负责事实校验
```

本项目采用 **LLM-as-DJ**。这是 generative recommendation 的方向,也是最能体现
"Agent 架构 + LLM 工程"能力的形态。

### 1.2 关键设计:Grounding(事实落地 / 去幻觉)

LLM 生成的候选可能包含不存在的歌、记错的艺人、张冠李戴的专辑。必须有一道
验证关卡:

```
DeepSeek 候选:  [{"artist": "Pink Floyd", "title": "Echoes"}, ...]
        │
        ▼  对每个候选
Spotify search  →  命中?  ── 否 ──▶ 丢弃(自动过滤幻觉)
        │ 是
        ▼
拿到真实 track_id / 预览 URL / 专辑 / 年份
        │
        ▼
Last.fm 补 tags(用于排序和理由)
        │
        ▼
进入排序候选池
```

> 设计要点:**LLM 负责"想到哪些歌",API 负责"确认这些歌真的存在并补全事实"**。
> 这是把幻觉风险关在门外的标准做法,也是简历上能讲的亮点。

### 1.3 本地数据重新定位

| 目录 | 旧角色 | 新角色 |
|---|---|---|
| `data/song_profiles/` | 唯一候选池 | **Cache**:验证过的歌缓存于此,命中则跳过 API |
| `data/user_profiles/` | 用户画像 | **Memory**:长期偏好 + 反馈,核心保留 |
| `data/trajectories/` | 运行记录 | **Trajectory**:Agent 运行轨迹,即 SFT 训练数据 |
| `data/sessions/` | 会话状态 | **Session Memory**:本轮意图、约束、已推荐歌曲 |

本地 284 首不再是"能推荐的全部",而是"碰巧已缓存的"。Agent 现在能推荐
DeepSeek 知识范围内 + Spotify 可验证的任何歌。

---

## 2. 目标目录结构

保留现有 L1-L7 作为内部 domain 能力(短期不删),新增标准 agent 层包在外面。

```
src/rateyourdj/
├── agent/                      # ★ 新增:Agent 核心
│   ├── __init__.py
│   ├── runtime.py              # Agent loop: plan → act → observe → repeat
│   ├── planner.py              # DeepSeek 规划器 + 规则 fallback
│   ├── tools.py                # 工具契约:输入/输出 schema、ToolObservation
│   ├── registry.py             # 工具注册表(名字 → 执行器)
│   ├── guards.py               # 参数校验、用户隔离、约束守卫
│   ├── memory.py               # 长期/短期记忆读写
│   ├── explanations.py         # 基于证据的推荐理由生成
│   └── providers/
│       ├── __init__.py
│       ├── base.py             # LLMProvider 抽象
│       └── deepseek.py         # DeepSeek adapter(复用现有 l6/deepseek.py)
│
├── domain/                     # ★ 新增:业务领域(从 L1-L7 抽取)
│   ├── discovery.py            # LLM 生成候选 + grounding 验证
│   ├── ranking.py              # 排序(复用 L4 逻辑)
│   ├── profiles.py            # 用户/歌曲画像(复用 L1/L2)
│   └── music_sources.py        # Spotify/Last.fm/MusicBrainz 统一 provider
│
├── l1..l7/                     # 现有实现,逐步被 agent/domain 调用
├── collectors/                 # 现有采集器 → 改造成"按需验证工具"
└── web/
    └── app.py                  # 简化为 Agent API gateway
```

迁移策略:**不推倒重来**。先在 `agent/` 和 `domain/` 里搭新骨架,内部仍调用
现有 L1-L5 service,跑通后再逐步把逻辑搬进 domain,最后让 L 系列只剩薄壳。

---

## 3. 工具契约(简历核心亮点)

所有工具返回统一的 `ToolObservation`,Agent 据此决定下一步。

```python
# agent/tools.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class ToolStatus(str, Enum):
    OK = "ok"            # 成功,结果满足请求
    PARTIAL = "partial"  # 有结果但不完整(如数量不够)
    EMPTY = "empty"      # 无结果,需调整参数
    ERROR = "error"      # 工具执行失败

@dataclass
class ToolObservation:
    tool: str
    status: ToolStatus
    data: dict
    diagnostics: list[str] = field(default_factory=list)      # 发生了什么
    suggested_actions: list[dict] = field(default_factory=list) # 建议下一步
    retryable: bool = False

    def ok(self) -> bool:
        return self.status in (ToolStatus.OK, ToolStatus.PARTIAL)
```

### 3.1 工具清单

DeepSeek 在 Agent loop 中可调用的工具(围绕音乐发现和记忆,不再暴露 L1-L7):

| 工具 | 作用 | 落地实现 |
|---|---|---|
| `get_user_memory` | 读取长期偏好 + 会话约束 | 复用 L1 + session store |
| `discover_tracks` | **核心**:LLM 生成候选 + grounding 验证 | 新增 domain/discovery.py |
| `get_similar_tracks` | 基于一首歌/艺人找相似 | DeepSeek 生成 or Last.fm getSimilar |
| `get_track_metadata` | 补全歌曲信息(缓存优先) | Spotify + 本地 cache |
| `rank_candidates` | 结合用户记忆排序 | 复用 L4 |
| `explain_recommendation` | 生成推荐理由 | 新增 explanations.py |
| `record_feedback` | 记录反馈 | 复用 L5 |
| `save_to_collection` | 加入收藏 | 复用 L1 |

### 3.2 核心工具:discover_tracks

这是整个重构的心脏。

```python
@dataclass
class DiscoverTracksInput:
    user_id: str
    intent: str                  # 本轮意图,如 "适合深夜的迷幻摇滚"
    count: int = 10
    exclude_artists: list[str] = field(default_factory=list)
    exclude_seen: bool = True

# 执行流程(domain/discovery.py):
#
# 1. 读取用户画像(长期偏好艺人/流派/标签)
# 2. 构造 prompt,让 DeepSeek 生成 count*2 个候选(artist + title + 一句理由)
#    - prompt 注入:用户偏好、本轮意图、排除项
#    - 要求结构化 JSON 输出
# 3. 对每个候选调 Spotify search 验证:
#    - 命中 → 拿 track_id / preview / album / year,写入 cache
#    - 未命中 → 记为 hallucination,丢弃
# 4. 命中的候选补 Last.fm tags
# 5. 返回 ToolObservation:
#    data = {"candidates": [...], "generated": N, "grounded": M, "dropped": N-M}
#    diagnostics = ["DeepSeek 生成 20 首,Spotify 验证通过 14 首,丢弃 6 首幻觉"]
```

> `dropped` / `grounded` 这两个数字本身就是很好的可观测指标,也是 eval 里
> "幻觉率"的来源。

---

## 4. Agent Runtime(标准 loop)

```python
# agent/runtime.py(伪代码)

class AgentRuntime:
    def __init__(self, planner, registry, guards, trajectory_store, max_steps=6):
        ...

    def run(self, user_id, query, session_id=None) -> AgentResult:
        trajectory = Trajectory(user_id, query, session_id)

        # Step 0: 规划(DeepSeek 解析意图;失败则规则 fallback)
        plan = self.planner.parse(query, user_memory=...)
        trajectory.record_plan(plan)

        # Step 1..N: 工具调用循环
        for step in range(self.max_steps):
            action = self.planner.next_action(plan, trajectory.observations())

            if action.kind == "finish":
                trajectory.stop("goal_satisfied"); break

            # 守卫:用户隔离 + 参数校验 + 约束(不放宽硬约束)
            safe_args = self.guards.check(action, plan.constraints)

            obs = self.registry.execute(action.tool, safe_args)
            trajectory.record_step(action, obs)

            # 不满足时消费 suggested_actions(经守卫校验后重试)
            if not obs.ok() and obs.retryable:
                continue

        # 兜底:若循环结束仍没排序结果,补一次受约束的 rank_candidates
        result = self.finalize(trajectory)
        self.trajectory_store.save(trajectory)
        return result
```

要点:
- **每一步都进 trajectory** → 可观测 + 可作为训练数据。
- **守卫层独立** → LLM 不能越权(跨用户、放宽"不要某艺人"、超数量)。
- **DeepSeek 失败自动降级规则** → 系统始终可用(你现在已有这个机制,保留)。
- `max_steps` 限制 → 不会无限循环烧 token。

---

## 5. Memory 设计(长期 / 短期分离)

`change.md` 第 6、7 点的落地。关键原则:**临时约束不污染长期偏好**。

```json
{
  "user_id": "demo-user",
  "long_term": {
    "preferred_artists": {"Pink Floyd": 0.9, "Radiohead": 0.7},
    "preferred_genres": {"progressive rock": 0.8},
    "preferred_tags": {"atmospheric": 0.6},
    "negative_preferences": {},          // 只有明确说"我不喜欢X"才写这里
    "explanation_style": "music_history" // 用户偏好的理由风格
  },
  "session": {
    "session_id": "...",
    "current_intent": "适合深夜",
    "constraints": {"exclude_artists": ["Pink Floyd"]}, // 本轮约束,会过期
    "seen_track_ids": [],                 // 已推荐,"换一批"时排除
    "turn_index": 2
  },
  "feedback": [
    {"track_id": "...", "event": "like", "reward": 0.6, "created_at": "..."}
  ]
}
```

记忆更新规则:
- 临时约束("今天不要 Pink Floyd")→ 只写 session,会话结束即失效。
- 长期偏好("我不喜欢 Pink Floyd")→ 才写 long_term.negative_preferences。
- 单次 skip ≠ 长期讨厌;需多次反馈或明确表达才升级。
- `propose_memory_update` 与 `commit_memory_update` 分开:Agent 可提议,
  规则/反馈足够明确才提交,且每次更新留可审计记录。

---

## 6. 推荐理由生成(基于证据,不靠瞎编)

```
ranking evidence(L4 分数拆解)
+ user memory(匹配到的偏好)
+ song metadata(年代/专辑/标签)
+ discovery reason(DeepSeek 当初为什么想到这首)
        ↓
结构化 evidence
        ↓
LLM explanation writer(润色成 DJ 口吻,可选)
        ↓
final reasons
```

每首歌返回:

```json
{
  "track": {"title": "Echoes", "artist": "Pink Floyd"},
  "score": 0.87,
  "evidence": {
    "matched_preferences": ["progressive rock"],
    "similar_collection_items": ["Comfortably Numb"],
    "discovery_reason": "迷幻氛围与长篇结构契合深夜场景",
    "feedback_signals": ["liked similar guitar-led tracks"]
  },
  "reasons": [
    "你收藏过 Comfortably Numb,这首在编曲氛围上一脉相承",
    "符合你偏好的 progressive rock",
    "23 分钟的长篇结构,适合你说的深夜场景"
  ]
}
```

> 关键:理由先有结构化 evidence,再写成人话。即使不接 LLM writer,也能
> 用模板生成可信理由。这是推荐系统可解释性的标准做法。

---

## 7. 产品化 API(隐藏 L1-L7)

```
POST /api/chat/<user_id>          对话式推荐(主入口)
POST /api/feedback/<user_id>      记录反馈
GET  /api/session/<session_id>    会话状态
GET  /api/profile/<user_id>       用户画像
POST /api/collection/<user_id>    加入收藏
```

`/api/chat` 响应:

```json
{
  "message": "找到几首适合深夜的迷幻摇滚",
  "session_id": "...",
  "recommendations": [ /* 见第 6 节 */ ],
  "trace": {
    "steps": 3,
    "tools_called": ["get_user_memory", "discover_tracks", "rank_candidates"],
    "discovery": {"generated": 20, "grounded": 14, "dropped": 6},
    "stop_reason": "goal_satisfied"
  }
}
```

`trace` 仅作开发/调试,前端默认折叠。不再向用户暴露 "L4 分数拆解"
"L6 Agent" 这类内部名。

---

## 8. 前端:DJ 对话体验

```
┌──────────────────────────────────────────┐
│        🎵 rateyourDJ — your AI DJ          │
├──────────────────────────────────────────┤
│  你:想听点适合深夜的迷幻摇滚            │
│                                          │
│  DJ:挑了几首氛围感强的,都不是大热门   │
├──────────────────────────────────────────┤
│  1. Echoes — Pink Floyd          ♪ 试听   │
│     👍  ⏭️  💔  ⭐                          │
│     为什么推荐? ▾                         │
│       · 你收藏过 Comfortably Numb         │
│       · 符合你偏好的 progressive rock     │
│  ...                                     │
├──────────────────────────────────────────┤
│  [ 换一批 ]  [ 推荐类似这首 ]  [ 查看过程 ]│
└──────────────────────────────────────────┘
```

- 自然语言输入为主入口。
- 每首歌:试听(Spotify Embed)+ 四种反馈 + 可展开理由。
- 快捷意图:换一批 / 推荐类似这首 / 不要这个风格。
- "查看过程"展开 trace(tool calls),作为开发者彩蛋,也展示 Agent 能力。

---

## 9. 测试方向(从内部实现 → Agent 契约)

```
tests/
├── test_agent_runtime.py      # loop 正确推进、停止条件、兜底
├── test_agent_contract.py     # 工具选择正确、ToolObservation 结构合法
├── test_guards.py             # 用户隔离、硬约束不被放宽
├── test_discovery_grounding.py# 幻觉歌曲被丢弃、缓存命中
├── test_memory.py             # 临时约束不写长期、反馈升级规则
├── test_explanations.py       # 理由基于 evidence 非空泛
└── test_*  (现有 L1-L7 保留)
```

用 mock provider 跑完整链路,不依赖真实 API/token。

---

## 10. SFT / LoRA:诚实定位为"未来工作 + 现在打地基"

**现在不做训练**,但现在就把 trajectory 设计成可直接用于 SFT 的格式:

```
现在:Agent 每次运行 → trajectory 记录 (query, tool_calls, observations,
                                        recommendations, feedback, reward)
未来:积累足够多用户数据 → 这些 trajectory 即 SFT/GRPO 训练样本
```

简历这样写(诚实且专业):
> "设计了面向训练的 trajectory schema 与按用户切分的数据导出管线,为后续
> SFT/LoRA 预留;当前阶段聚焦 Agent 架构、工具契约与 grounding 的稳定性。"

这反而显示工程节奏感(先稳定架构再谈微调),比硬塞一个未完成的 SFT 更可信。
未来适合微调的场景:固定 DJ 语气、稳定 JSON 输出、强化工具调用习惯、优化
理由表达——前提是架构与 API 已稳定、有足够数据和明确 eval。

---

## 11. 实施阶段与优先级

| 阶段 | 内容 | 产出 | 简历价值 | 状态 |
|---|---|---|---|---|
| P1 | `discover_tracks` + grounding | LLM 生成→Spotify 验证闭环 | ★★★ 最核心,体现 LLM-as-DJ | ✅ 已实现 |
| P2 | Agent runtime + 工具契约 + 守卫 | 标准 loop,trace 可观测 | ★★★ 架构能力 | ✅ 已有 |
| P3 | Memory 长期/短期分离 | 不污染长期偏好 | ★★ 设计严谨 | ✅ 已有(session/memory 工具) |
| P4 | 推荐理由(evidence → reasons) | 可解释推荐 | ★★★ 可解释性 | ✅ 已实现 |
| P5 | 产品化 API + DJ 前端 | 端到端 demo | ★★ 完整度 | ✅ 已实现 |
| P6 | Agent 契约测试 | mock 链路稳定 | ★ 工程质量 | ✅ 已有 224 测试 |
| P7 | trajectory 导出管线(SFT 预留) | 训练数据 schema | ★★ 前瞻性 | ✅ 已有(L7) |

实现摘要(本轮完成):

- **P1 生成式发现**:`domain/discovery.py` + `domain/generators.py`,工具
  `discover_tracks` 已接入注册表、schema、守卫、loop 契约和模型循环。
- **P4 证据式理由**:`domain/explanations.py`,`ExplanationGenerator` 从偏好
  匹配、相似收藏、发现理由、反馈信号等结构化 evidence 生成自然语言理由;已接入
  `explain_recommendations` 工具和产品 API。
- **P5 产品化 API**:`/api/v1/agent/recommend`(已存在,理由升级为证据式)、
  新增 `/api/v1/agent/feedback` 和 `/api/v1/agent/session/<id>`,统一隐藏 L1-L7。
- **前端**:文案改为产品语言(DJ Agent / Music Discovery Agent / 为什么推荐这首),
  新增"换一批"快捷操作,trace 收进折叠的"开发者视图"。


---

## 12. 风险与权衡

| 风险 | 应对 |
|---|---|
| LLM 幻觉出不存在的歌 | grounding 验证强制丢弃;监控 dropped 率 |
| DeepSeek 音乐知识有时效/覆盖盲区 | 冷门需求可 fallback 到 Last.fm getSimilar |
| 每次请求多次 API 调用,延迟高 | cache 命中跳过验证;并发验证候选 |
| API key / 配额 | key 仅环境变量;无 key 时降级规则路径 |
| 过早拆 L1-L7 引入回归 | 渐进迁移,新层先调旧 service,测试护航 |

---

## 附:简历描述草稿

```
rateyourDJ — Conversational Music DJ Agent
Python · DeepSeek API · Spotify / Last.fm / MusicBrainz · Flask

• 将一个分层的本地音乐推荐管线重构为对话式、工具调用的 DJ Agent:以 LLM
  作为生成式发现引擎,结合用户长期画像生成候选,再通过外部音乐 API 做事实
  落地与去幻觉。

• 设计标准 Agent 架构:统一工具契约(ToolObservation)、执行引擎(plan→act→
  observe loop)、参数守卫层(用户隔离/硬约束保护)与 DeepSeek/规则双路 fallback。

• 实现基于证据的可解释推荐:每条推荐由偏好匹配、相似收藏、发现理由与反馈
  信号等结构化 evidence 生成,而非 LLM 空泛输出。

• 区分长期偏好与会话约束的分层 Memory,确保临时约束不污染长期画像;构建
  反馈闭环(like/skip/favorite → reward)。

• 设计面向训练的 trajectory schema 与按用户切分的导出管线,为后续 SFT/LoRA
  预留数据基础。
```
