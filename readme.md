# rateyourDJ

本项目旨在构建一个个性化音乐推荐 Agent，通过用户画像建模、音乐知识库构建和工具调用机制，实现基于用户需求的歌曲检索、排序、推荐与反馈更新；在系统积累交互数据后，进一步利用 SFT 和 GRPO 优化 Agent 的工具调用能力与推荐决策能力。

## 1. 总体流程

```
用户输入需求
    ↓
Agent 理解用户意图
		↓
读取用户画像
		↓
从音乐知识库检索候选歌曲
		↓
对候选歌曲排序
		↓
返回推荐结果和推荐理由
		↓
收集用户反馈
		↓
更新用户画像和推荐权重
		↓
积累交互轨迹
		↓
后期 SFT / GRPO 优化 Agent
		↓
	验证
		↓
	前端
```

## 1. L1 用户画像模块

把用户的自然语言、播放记录、收藏记录、反馈记录转成结构化画像。

| 字段                | 所属部分                                                     | 主要来源                                     | 获取方式                              | 可靠性   | 用途                                                     |
| ------------------- | ------------------------------------------------------------ | -------------------------------------------- | ------------------------------------- | -------- | -------------------------------------------------------- |
| genres              | Long-term Preference / Short-term Intent                     | Last.fm、MusicBrainz、人工标签、用户 query   | API / tag mapping / LLM 抽取          | 较高     | 用于候选歌曲召回、风格匹配和排序                         |
| artists             | Long-term Preference / Short-term Intent                     | Spotify、MusicBrainz、播放记录、用户 query   | API / metadata / LLM 抽取             | 很高     | 用于歌手偏好建模、相似歌手推荐                           |
| reference_songs     | Short-term Intent                                            | 用户 query、播放记录、收藏记录               | LLM 抽取 / metadata 匹配              | 高       | 用于“推荐类似某首歌”的相似歌曲检索                       |
| reference_artists   | Short-term Intent                                            | 用户 query、播放记录、收藏记录               | LLM 抽取 / metadata 匹配              | 高       | 用于“推荐类似某个歌手”的风格迁移推荐                     |
| languages           | Long-term Preference / Short-term Intent                     | 用户 query、歌曲 metadata、歌词语言检测、LLM | API / LLM / langdetect                | 中等     | 用于语言过滤和语言偏好排序                               |
| moods               | Long-term Preference / Short-term Intent                     | Last.fm tags、评论、用户 query、LLM          | tag mapping / LLM 抽取                | 中等     | 用于情绪推荐，例如怀旧、温暖、安静、忧郁                 |
| scenes              | Long-term Preference / Short-term Intent                     | 用户 query、用户行为上下文、评论、LLM        | LLM 抽取 / 行为上下文推断             | 中等     | 用于场景推荐，例如学习、夜晚、通勤、运动                 |
| instruments         | Long-term Preference / Short-term Intent                     | 用户描述、评论、标签、可选音频分析           | LLM 抽取 / tag mapping / 可选音频分析 | 中等     | 用于乐器偏好匹配，例如吉他、钢琴、合成器                 |
| vocal_styles        | Long-term Preference / Short-term Intent                     | 用户描述、评论、LLM                          | LLM 抽取 / 人工标签                   | 中等偏低 | 用于细粒度听感匹配和推荐解释，例如人声靠前、温柔女声     |
| sound_textures      | Long-term Preference / Short-term Intent / Negative Preference | 评论、Last.fm tags、用户 query、LLM          | LLM 抽取 / tag mapping                | 中等     | 用于主观听感匹配，例如 warm、dreamy、noisy、lo-fi        |
| tempo_preference    | Long-term Preference / Short-term Intent / Negative Preference | 用户描述、标签、评论语义、可选音频特征       | tag mapping / LLM 抽取 / 可选音频分析 | 中等     | 用于节奏偏好控制，例如 slow、medium、fast                |
| energy_preference   | Long-term Preference / Short-term Intent / Negative Preference | 用户描述、标签、评论语义、可选音频特征       | tag mapping / LLM 抽取 / 可选音频分析 | 中等     | 用于能量强度控制，例如 low、medium、high                 |
| must_have           | Short-term Intent                                            | 用户当前 query                               | LLM 抽取                              | 高       | 表示本次推荐必须满足的条件，例如英文歌、有人声、有吉他   |
| avoid               | Short-term Intent / Negative Preference                      | 用户当前 query、明确负反馈                   | LLM 抽取 / 行为反馈                   | 高       | 表示本次推荐需要规避的条件，例如不要太吵、不要电子感太强 |
| exploration_level   | Short-term Intent                                            | 用户 query、系统默认设置、历史反馈           | LLM 判断 / 规则设定                   | 中等     | 控制推荐新颖度，例如 safe、balanced、exploratory         |
| negative_preference | Negative Preference                                          | 用户明确反馈、快速跳过、dislike、连续负反馈  | LLM 抽取 / 行为反馈聚合               | 高       | 用于推荐过滤和排序惩罚                                   |
| feedback_memory     | Feedback Memory                                              | 用户行为日志                                 | 系统自己记录                          | 很高     | 用于画像更新、推荐评估、后期 SFT / GRPO 数据积累         |

将其保存为json文件，结构如下所示

```
{
  "long_term_preference": {},
  "short_term_intent": {},
  "negative_preference": {},
  "feedback_memory": []
}
```

其中，long_term如下，用于个性化召回，长期排序权重，用户稳定音乐品味建模

```
genres
artists
languages
moods
scenes
instruments
vocal_styles
sound_textures
tempo_preference
energy_preference
```

Short_team如下，用于理解用户本次想听什么，控制本轮推荐方向，优先满足当前需求

```
reference_songs
reference_artists
genres
artists
languages
moods
scenes
instruments
vocal_styles
sound_textures
tempo_preference
energy_preference
must_have
avoid
exploration_level
```

negative_preference如下,推荐过滤,排序惩罚,避免重复推荐用户不喜欢的内容

```
不喜欢的 genres
不喜欢的 artists
不喜欢的 moods
不喜欢的 instruments
不喜欢的 vocal_styles
不喜欢的 sound_textures
不喜欢的 tempo / energy
```

feedback_memory如下，实时更新用户画像, 计算个性化 reward, 评估推荐效果, 后期构造 SFT / GRPO 训练数据

```
播放
完整播放
快速跳过
收藏
喜欢
不喜欢
加入歌单
重复播放
当前 query
时间戳
对应歌曲标签
reward 分数
```

### L1 当前实现

L1 当前只负责用户画像的数据边界，不负责理解自然语言或计算反馈奖励。后续模块将整理好的字典迁入 L1，由 L1 完成校验、合并和持久化。

职责包括：

- 提供包含全部 L1 字段的空画像框架。
- 校验后续模块传入的部分画像字典。
- 合并长期偏好和负向偏好的标签权重。
- 覆盖本轮传入的短期意图字段。
- 追加 L7 生成的反馈记录。
- 按用户读取和保存 JSON。

不属于 L1 的职责：

- 自然语言意图抽取由 L6 或独立解析工具负责。
- 歌曲标签和元数据由 L2 提供。
- reward 计算以及如何更新偏好权重由 L7 决定。

本地查看完整框架：

```bash
conda activate rateyourDJ
python -m pip install -e .
rateyourdj-l1 schema
rateyourdj-l1 init demo-user
rateyourdj-l1 show demo-user
```

`init` 会直接生成完整的空画像，不需要传入 example。默认保存在 `data/user_profiles/<user_id>.json`。

后续模块需要迁入字典时：

```bash
rateyourdj-l1 validate path/to/profile_patch.json
rateyourdj-l1 import demo-user path/to/profile_patch.json
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

## L2 音乐知识库模块

与L1字段对齐，方便计算匹配度

需要对齐的字段：

| L2 字段           | 对齐的 L1 字段                                | 主要来源                            | 获取方式                         | 可靠性   | 用途                                         |
| ----------------- | --------------------------------------------- | ----------------------------------- | -------------------------------- | -------- | -------------------------------------------- |
| genres            | genres                                        | Last.fm、MusicBrainz、人工标签      | API / tag mapping                | 较高     | 风格匹配、候选召回、排序                     |
| artists           | artists / reference_artists                   | Spotify、MusicBrainz、歌曲 metadata | API / metadata                   | 很高     | 歌手偏好匹配、相似歌手推荐                   |
| title / song_name | reference_songs                               | Spotify、MusicBrainz、歌曲 metadata | API / metadata                   | 很高     | 支持“类似某首歌”的推荐                       |
| languages         | languages                                     | 歌曲 metadata、歌词语言检测、LLM    | metadata / langdetect / LLM      | 中等     | 语言过滤、语言偏好匹配                       |
| moods             | moods                                         | Last.fm tags、评论、LLM             | tag mapping / LLM 抽取           | 中等     | 情绪匹配，例如 nostalgic、calm、melancholic  |
| scenes            | scenes                                        | 评论、用户标签、LLM、人工规则       | LLM 抽取 / 规则映射              | 中等偏低 | 场景匹配，例如 night、study、walking         |
| instruments       | instruments                                   | 评论、标签、LLM、可选音频分析       | tag mapping / LLM 抽取           | 中等     | 乐器偏好匹配，例如 acoustic_guitar、piano    |
| vocal_styles      | vocal_styles                                  | 评论、LLM、人工标签                 | LLM 抽取 / 人工标注              | 中等偏低 | 人声偏好匹配，例如 forward_vocal、soft_vocal |
| sound_textures    | sound_textures                                | Last.fm tags、评论、LLM             | tag mapping / LLM 抽取           | 中等     | 听感匹配，例如 warm、dreamy、noisy、lo_fi    |
| tempo             | tempo_preference                              | 用户标签、评论语义、可选 BPM        | tag mapping / LLM / 可选音频分析 | 中等     | 节奏匹配，例如 slow、medium、fast            |
| energy            | energy_preference                             | 用户标签、评论语义、可选音频特征    | tag mapping / LLM / 可选音频分析 | 中等     | 能量匹配，例如 low、medium、high             |
| avoid_tags        | avoid / negative_preference                   | 歌曲标签、评论、LLM                 | tag mapping / LLM 抽取           | 中等     | 用于过滤或惩罚用户不想要的特征               |
| semantic_tags     | moods / scenes / sound_textures / instruments | Last.fm、评论、LLM                  | 统一标签映射                     | 中等     | 作为统一语义标签池，辅助召回和排序           |
| embedding_text    | 所有 L1 文本偏好字段                          | metadata + tags + LLM summary       | 模板生成 / LLM 总结              | 中等     | 生成歌曲 embedding，用于语义检索             |
| embedding         | 用户 query embedding / reference_songs        | embedding model                     | 文本向量化                       | 中等     | 相似歌曲检索、语义召回                       |

辅助字段

| L2 辅助字段      | 来源                       | 用途                               |
| ---------------- | -------------------------- | ---------------------------------- |
| song_id          | 系统生成 / API ID          | 唯一标识歌曲                       |
| album            | Spotify、MusicBrainz       | 展示和元数据补充                   |
| release_year     | Spotify、MusicBrainz       | 年代偏好、推荐解释                 |
| duration_ms      | Spotify、metadata          | 展示和过滤                         |
| popularity       | Spotify、Last.fm           | 排序辅助，避免推荐太冷门或质量过低 |
| source_tags      | Last.fm、MusicBrainz、评论 | 保留原始标签，方便追溯             |
| data_source      | API / 人工 / LLM           | 标记字段来源，方便判断可信度       |
| confidence_score | 系统计算                   | 判断该歌曲特征是否可靠             |

例子：

```
{
  "song_id": "song_001",

  "metadata": {
    "title": "Wonderwall",
    "artist": "Oasis",
    "album": "(What's the Story) Morning Glory?",
    "release_year": 1995,
    "language": "English",
    "duration_ms": 258000
  },

  "aligned_features": {
    "genres": {
      "britpop": 0.9,
      "alternative_rock": 0.7
    },
    "artists": {
      "Oasis": 1.0
    },
    "languages": {
      "English": 1.0
    },
    "moods": {
      "nostalgic": 0.8,
      "warm": 0.7,
      "melancholic": 0.5
    },
    "scenes": {
      "night": 0.6,
      "walking": 0.5,
      "alone": 0.5
    },
    "instruments": {
      "acoustic_guitar": 0.9,
      "electric_guitar": 0.4
    },
    "vocal_styles": {
      "forward_vocal": 0.7,
      "male_vocal": 0.8
    },
    "sound_textures": {
      "guitar_driven": 0.9,
      "warm": 0.6,
      "anthemic": 0.7
    },
    "tempo": {
      "medium": 0.8
    },
    "energy": {
      "medium": 0.7
    }
  },

  "avoid_tags": {
    "too_noisy": 0.2,
    "too_fast": 0.1,
    "too_electronic": 0.0
  },

  "source_tags": {
    "lastfm_tags": ["britpop", "rock", "alternative", "90s", "acoustic"],
    "review_keywords": ["guitar-driven", "nostalgic", "anthemic"]
  },

  "embedding_text": "Wonderwall by Oasis is a Britpop and alternative rock song with acoustic guitar, forward male vocal, nostalgic and warm mood, and a guitar-driven anthemic sound texture.",
  "embedding": []
}
```

### L2 当前实现

L2 当前只负责歌曲画像的数据边界，不负责调用外部 API、推断标签或生成 embedding。

职责包括：

- 提供包含全部 L2 字段的空歌曲画像框架。
- 校验采集器、标准化模块或 enrichment 模块传入的部分字典。
- 合并 metadata、对齐特征、标签、来源和 embedding 数据。
- 按 `song_id` 读取和保存 JSON。

查看完整 schema 和创建空歌曲画像：

```bash
conda activate rateyourDJ
python -m pip install -e .
rateyourdj-l2 schema
rateyourdj-l2 init song-001
rateyourdj-l2 show song-001
```

`init` 生成的文件位于 `data/song_profiles/song-001.json`，不需要传入 example。

后续数据采集模块迁入字典时：

```bash
rateyourdj-l2 validate path/to/song_patch.json
rateyourdj-l2 import song-001 path/to/song_patch.json
```

当前所有权重、`popularity` 和 `confidence_score` 均统一归一化到 `0` 至 `1`。



## L3 候选歌曲召回模块

目的是寻找n个在L1需求下的L2候选歌曲，最终输出候选歌曲列表

通过n种召回策略

| 召回方式                 | 输入                                    | 使用字段                                                     | 输出                 | 适合场景                     |
| ------------------------ | --------------------------------------- | ------------------------------------------------------------ | -------------------- | ---------------------------- |
| Keyword Retrieval        | 用户 query                              | title、artist、genres、tags                                  | 关键词匹配候选       | 用户直接说歌名、歌手、风格   |
| Tag-based Retrieval      | L1 short-term intent                    | genres、moods、scenes、instruments、sound_textures           | 标签匹配候选         | 用户说“安静、晚上、吉他”     |
| Embedding Retrieval      | query embedding                         | song embedding                                               | 语义相似候选         | 用户表达比较模糊             |
| Reference Song Retrieval | reference_songs                         | song embedding、aligned_features                             | 相似歌曲候选         | 用户说“像 Wonderwall”        |
| User Profile Retrieval   | long-term preference                    | genres、artists、languages、moods、scenes、instruments、vocal_styles、sound_textures、tempo、energy | 个性化候选           | 用户只说“推荐点我可能喜欢的” |
| Rule-based Filter        | must_have / avoid / negative_preference | language、avoid_tags、negative_preference、explicit constraints | 过滤或降权不合适歌曲 | 用户有明确限制条件           |

其中，Rule-based Filter 不直接产生候选歌曲，而是在多路召回前后用于过滤或惩罚不满足 must_have / avoid 条件的歌曲。

## L4 推荐排序

根据用户当前意图、长期偏好、负向偏好和候选歌曲特征，对 L3 输出的候选歌曲进行精细打分和排序，最终输出 Top-K 推荐歌曲。

输入：

1. L3结果
2. L1的short-term intent
3. L1的long-term performance
4. L2的song profile

输出例子：

```
{
  "ranked_songs": [
    {
      "rank": 1,
      "song_id": "song_018",
      "title": "Half the World Away",
      "artist": "Oasis",
      "final_score": 0.89,
      "score_breakdown": {
        "short_term_match": 0.31,
        "long_term_match": 0.22,
        "retrieval_score": 0.12,
        "quality_score": 0.08,
        "diversity_score": 0.06,
        "penalty": -0.02
      },
      "ranking_reason": [
        "matches the reference song style",
        "English vocal",
        "acoustic guitar",
        "nostalgic and warm mood",
        "not too noisy"
      ]
    }
  ]
}
```

其中，

```
Final Score =
0.35 * ShortTermMatch
+ 0.25 * LongTermMatch
+ 0.15 * RetrievalScore
+ 0.10 * QualityScore
+ 0.10 * DiversityScore
- 0.25 * NegativePenalty
- 0.20 * ConstraintPenalty
```

| 排序因子          | 来源                                      | 使用字段                                                     | 作用                           | 权重建议（暂时） |
| ----------------- | ----------------------------------------- | ------------------------------------------------------------ | ------------------------------ | ---------------- |
| ShortTermMatch    | L1 Short-term Intent + L2 Song Profile    | language、mood、scene、instrument、sound_texture、tempo、energy、must_have、avoid | 判断是否符合本次需求           | 0.35             |
| LongTermMatch     | L1 Long-term Preference + L2 Song Profile | genres、artists、moods、scenes、instruments、vocal_styles、sound_textures | 判断是否符合长期偏好           | 0.25             |
| RetrievalScore    | L3 Candidate Retrieval                    | retrieval_score、retrieval_sources                           | 保留召回阶段相关性             | 0.15             |
| QualityScore      | L2 Metadata / Source Tags                 | popularity、metadata completeness、tag confidence            | 避免推荐低质量或信息不可靠歌曲 | 0.10             |
| DiversityScore    | L4 当前推荐列表                           | artist、genre、embedding similarity                          | 避免推荐结果过于重复           | 0.10             |
| NegativePenalty   | L1 Negative Preference + L2 avoid_tags    | negative_preference、avoid_tags                              | 惩罚用户长期不喜欢的特征       | -0.25            |
| ConstraintPenalty | L1 Short-term Intent + L2 Song Profile    | must_have、avoid、language、energy、tempo                    | 惩罚或过滤违反当前限制的歌曲   | -0.20            |



## L5 Agent Tool Layer

将L1-L4写为agent可调用的工具包

| 工具名称             | 对应模块     | 输入                                             | 输出                                      | 作用               |
| -------------------- | ------------ | ------------------------------------------------ | ----------------------------------------- | ------------------ |
| parse_user_intent    | L6 / 解析工具 | user_query                                       | short_term_intent                         | 解析用户当前需求   |
| get_user_profile     | L1           | user_id                                          | long_term_preference、negative_preference | 获取用户长期画像   |
| search_song_metadata | L2           | title / artist / query                           | song profile                              | 查询歌曲信息       |
| retrieve_candidates  | L3           | short_term_intent、user_profile                  | candidate_songs                           | 召回候选歌曲       |
| rank_candidates      | L4           | candidate_songs、user_profile、short_term_intent | ranked_songs                              | 对候选歌曲排序     |
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


