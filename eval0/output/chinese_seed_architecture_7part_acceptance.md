# Cross-Agent 七大部分架构验收说明（基于中文 22 轮真实观测）

## 1. 文档范围与事实来源

本说明只基于以下已落地且已运行的数据与代码，不引入未验证的推断：

- 对话与 API 交互记录：[chinese_seed_api_conversation.json](/Users/mac/workspace/Cross-Agent/eval/output/chinese_seed_api_conversation.json)
- 逐轮状态观测：[chinese_seed_memory_observation.md](/Users/mac/workspace/Cross-Agent/eval/output/chinese_seed_memory_observation.md)
- 本次运行状态库：[chinese_seed_observation.sqlite3](/Users/mac/workspace/Cross-Agent/eval/output/chinese_seed_observation.sqlite3)
- 种子输入：[chinese_user_only_conversation_seed.json](/Users/mac/workspace/Cross-Agent/eval/output/chinese_user_only_conversation_seed.json)
- 代码实现：
  - [extractor.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/writer/extractor.py)
  - [governor.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/governor/governor.py)
  - [write_policy.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/policies/write_policy.py)
  - [privacy.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/policies/privacy.py)
  - [memory_reader.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/reader/memory_reader.py)
  - [scoring.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/reader/scoring.py)
  - [sqlite_store.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/store/sqlite_store.py)
  - [response_guard.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/guard/response_guard.py)
  - [default.json](/Users/mac/workspace/Cross-Agent/configs/default.json)

本次实际运行链路为：

`search_existing_memory -> API answer -> ingest_current_turn`

这意味着当前轮回答使用的是“本轮写入前”的历史长期记忆，不会把当前用户刚说的话先写入再拿来回答。

## 2. 七大部分结论总览

| 部分 | 本次验收结论 | 证据来源 |
|---|---|---|
| 抽取 | 已实现并在 22 轮数据中多次触发 | round 1/2/3/4/5/7/8/11/12/15 |
| 更新 | 已实现并触发 create、supersede；reinforce 已实现但本次未触发 | round 11/12/15；`sqlite_store._reinforce()` |
| 遗忘 | 已实现“状态失效/旧值退役”这一层；未实现定时物理清除与连续时间衰减落库 | round 11/12/15；`valid_to`、`superseded` |
| 检索 | 已实现混合检索、反馈词扩展、去重、证据包构造 | `memory_reader.py`，round 17/18/21/22 |
| 使用 | 已实现 EvidenceBundle 驱动回答，且记录 used_memory_ids | `response_guard.py`，conversation JSON |
| 安全 | 已实现写入安全门与披露安全门基础版 | round 14/21；`privacy.py` |
| 拒答 | 已实现“无证据拒答”策略；本次数据中更明显触发的是“无相关记忆时如实说明没有” | round 22；`response_guard.py` |

## 3. 槽位定义与状态模型

### 3.1 本次 demo 实际观测到的结构化槽位

| predicate | scope | 示例值 | 首次出现轮次 | 最终状态 |
|---|---|---|---|---|
| `name` | `identity` | `{"text": "林澈"}` | round 1 | active |
| `current_residence` | `current_residence` | `{"text": "杭州滨江"}` | round 2 | active |
| `preferred_programming_language` | `coding_examples` | `{"text": "java"}` -> `{"text": "python"}` | round 3 / 11 | python active，java superseded |
| `preferred_drink` | `daily_drink` | `{"text": "咖啡"}` -> `{"text": "茶"}` | round 4 / 12 | 茶 active，咖啡 superseded |
| `user_task` | `task:cross_agent_architecture_demo` | `{"text": "整理 Cross-Agent 的架构演示材料", "state": "open"}` -> `{"state": "done"}` | round 5 / 15 | done active，open superseded |
| `preferred_device_ecosystem` | `device_ecosystem` | `{"text": "苹果生态"}` | round 7 | active |
| `disliked_food` | `fruit` | `{"text": "苹果"}` | round 7 | active |
| `preferred_environment` | `work_or_writing` | `{"text": "安静"}` | round 8 | active |
| `preferred_environment` | `social_gathering` | `{"text": "热闹"}` | round 8 | active |
| `session_evidence` | `actmem_session` | 每轮原始摘要、transcript、keywords | 多轮 | active |

### 3.2 状态迁移模型

本次已观测到的状态：

- `active`：当前有效
- `superseded`：被同槽新值覆盖
- `reject`：不进入 `memory_state`，只在 `memory_events` 中留拒绝事件

本次实际发生的状态迁移：

1. `create -> active`
2. `active -> superseded`，同时新记录 `create -> active`
3. `candidate -> reject event`

可验证实例：

- round 11：`preferred_programming_language: java -> python`
- round 12：`preferred_drink: 咖啡 -> 茶`
- round 15：`user_task.state: open -> done`
- round 14、21：凭证类内容被拒绝写入

## 4. 七大部分逐项验收

### 4.1 抽取

抽取层由 `HeuristicSessionExtractor` 完成，当前是“规则抽取 + 原始会话证据保留”，不是 LLM 自由抽取。

#### 已实现

- 每轮先生成一条 `session_evidence`
- 对高确定性表达抽取结构化槽位
- 敏感模式在抽取期即标出高风险内容
- 中文规则已覆盖本次 demo 需要的主要槽位

#### 本次 demo 中实际触发的关键变量变化

| 轮次 | 用户表达 | 抽取结果 | 变量变化 |
|---|---|---|---|
| 1 | “我叫林澈” | `name=林澈` + `session_evidence` | `extracted=2 created=2` |
| 2 | “我现在住在杭州滨江” | `current_residence=杭州滨江` + `session_evidence` | `active_memories 2 -> 4` |
| 3 | “代码示例先默认用 Java” | `preferred_programming_language=java` + `session_evidence` | 新增代码语言槽位 |
| 4 | “我早上一般喝咖啡” | `preferred_drink=咖啡` + `session_evidence` | 新增饮品槽位 |
| 5 | “这周五前我要整理...” | `user_task.state=open` + `session_evidence` | 新增任务槽位 |
| 7 | “苹果那套生态...水果里面苹果不太爱吃” | `preferred_device_ecosystem=苹果生态`，`disliked_food=苹果`，`session_evidence` | 同轮抽出两个不同槽位 |
| 8 | “写文档喜欢安静...聚会想去热闹点” | `preferred_environment(work_or_writing)=安静`，`preferred_environment(social_gathering)=热闹`，`session_evidence` | 同 predicate 不同 scope 并存 |
| 11 | “以后...优先用 Python，不要再默认 Java” | `preferred_programming_language=python` + `session_evidence` | 为更新阶段准备 supersede |
| 12 | “我先改喝茶” | `preferred_drink=茶` + `session_evidence` | 为更新阶段准备 supersede |
| 15 | “材料已经整理完了” | `user_task.state=done` + `session_evidence` | 为更新阶段准备 supersede |

#### 中文 demo 示例

- 输入：`代码示例先默认用 Java 吧，我们团队以前主要是 Java 服务，接口例子也比较好对齐。`
- 结构化输出：`predicate=preferred_programming_language`，`scope=coding_examples`，`value={"text":"java"}`

#### 边界说明

- 已实现：姓名、居住地、编程语言偏好、饮品偏好、任务状态、环境偏好、设备生态、厌恶食物、原始 session evidence。
- 已建模但未完整落地：开放域复杂事件、多跳关系、旅行计划这类弱承诺内容的稳定建模。round 9 的“冰岛极光”在本次未进入结构化长期记忆。

### 4.2 更新

更新阶段由 `RuleBasedMemoryGovernor + SQLiteStore` 完成。这里的“更新”包括新建、强化、覆盖。

#### 决策规则

- 同一 `source_session_id` 的 `session_evidence` 再次写入：`reinforce`
- 同槽位无旧值：`create`
- 同槽位同值：`reinforce`
- 同槽位新值：`supersede`

#### 本次 demo 中实际触发的关键变量变化

1. round 11，语言偏好更新

- 旧值：`preferred_programming_language = java`
- 新值：`preferred_programming_language = python`
- 结果：
  - 旧记录 `status: active -> superseded`
  - 旧记录 `valid_to = 2026-06-11`
  - 新记录 `status = active`
  - 新记录 `supersedes = old_memory_id`

2. round 12，饮品偏好更新

- 旧值：`咖啡`
- 新值：`茶`
- 结果：同上

3. round 15，任务状态更新

- 旧值：`state=open`
- 新值：`state=done`
- 结果：同上

#### 强化公式

代码已实现，位置：[sqlite_store.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/store/sqlite_store.py)

```text
confidence' = min(0.99, confidence + 0.02)
importance' = min(0.99, importance + 0.02)
updated_at' = now
```

#### 本次是否观测到强化

- 代码已实现
- 本次 22 轮 demo `reinforced = 0`
- 结论：强化逻辑已落地，但本条演示数据没有触发“同槽同值重复表达”或“同 session 重复写入”

#### 边界说明

- 已实现：`create / reinforce / supersede / reject`
- 已建模但未完整落地：跨表达归一、近义值合并、复杂实体对齐

### 4.3 遗忘

本项目当前的“遗忘”是“状态层失效治理”，不是物理删除优先。

#### 本次已证实的遗忘能力

- 旧值在 supersede 后不再是当前值
- 旧值 `valid_to` 被写入
- 检索阶段对 `valid_to` 非空记录施加陈旧惩罚
- 回答提示词要求模型优先使用 `active` 结构化记忆，不把 superseded 旧值当当前值

#### 本次 demo 中实际触发的关键变量变化

| 轮次 | 旧值 | 新值 | 遗忘表现 |
|---|---|---|---|
| 11 | Java | Python | Java 退役为 `superseded` |
| 12 | 咖啡 | 茶 | 咖啡退役为 `superseded` |
| 15 | task open | task done | open 退役为 `superseded` |

#### 已实现的衰减相关事实

- `temporal_score(record)`：
  - `valid_to` 为空时返回 `1.0`
  - `valid_to` 非空时返回 `0.0`
- 总分中还会额外减去 `staleness_penalty = 0.30`

也就是说，当前系统已经有“失效记录降权”，但不是连续时间函数，不会按天逐步衰减。

#### 衰减公式建议

以下是建议公式，不是当前代码中的既有实现：

```text
freshness(t) = exp(-lambda * age_days)
score' = base_score * freshness(t)
```

建议使用方式：

- 对 `event/session_evidence` 使用连续时间衰减
- 对结构化当前槽位仅在进入 `superseded` 后快速降权
- 对 `task=open` 可叠加截止日期紧迫度，而不是单纯时间衰减

#### 边界说明

- 已实现：状态失效、旧值退役、检索降权
- 已建模但未完整落地：TTL 到期删除、归档迁移、连续时间遗忘、用户显式删除记忆

### 4.4 检索

检索由 `MemoryReader` 完成，当前是混合检索，不是纯向量。

#### 检索流程

1. `QueryPlanner` 判断本轮是否需要长期记忆
2. 取全部 `active` 记录
3. 初排：词法分 + 语义分 + 时间分 + 重要性 + 置信度 + 覆盖度
4. 从初排前 `feedback_top_n` 条提取反馈词
5. 二次重排
6. 经过隐私披露过滤
7. 以 `source_session_id` 去重
8. 形成 `EvidenceBundle`

#### BM25 词法分

代码实现位置：[scoring.py](/Users/mac/workspace/Cross-Agent/src/cross_agent/reader/scoring.py)

```text
lexical_score = min(
  1.0,
  (Σ idf(term) * (tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avg_len)))) / 8.0
)

k1 = 1.4
b = 0.75
avg_len = 120.0
idf(term) = log(1 + (doc_count - df + 0.5) / (df + 0.5))
```

#### 余弦语义分

代码实现位置：`CorpusScorer.semantic_score()`

```text
semantic_score = cosine_from_counts(query_term_counts, doc_term_counts)
```

这里不是 embedding cosine，而是基于词频向量的计数余弦。

#### 总分公式

配置来自 [default.json](/Users/mac/workspace/Cross-Agent/configs/default.json)：

- `semantic_weight = 0.42`
- `lexical_weight = 0.18`
- `temporal_weight = 0.14`
- `importance_weight = 0.10`
- `confidence_weight = 0.10`
- `query_coverage_weight = 0.00`
- `staleness_penalty = 0.30`
- `privacy_penalty = 0.40`

代码总分公式：

```text
score =
  0.42 * semantic_score +
  0.18 * lexical_score +
  0.14 * temporal_score +
  0.10 * importance +
  0.10 * confidence +
  0.00 * coverage

if memory_type != event:
  score += 0.12

if valid_to is not null:
  score -= 0.30

if sensitivity in {high, sensitive}:
  score -= 0.40

final_score = max(score, 0.0)
```

#### 本次 demo 中实际观测

- round 1：`candidate_count = 0`，`EvidenceBundle = []`
- round 2：已能检索到 round 1 的 `name`
- round 17：能够同时召回 `name`、`current_residence`、`preferred_programming_language=python`、`preferred_drink=茶`、`user_task=done`
- round 18：能够回答“以前是不是用过 Java”，说明检索和回答链路可以利用历史轨迹解释“为什么现在不再按 Java”
- round 21：能列出安全可用记忆，但不能取出验证码/临时密码
- round 22：无日本酒店记忆，回答明确表示没有

#### 边界说明

- 已实现：词法 + 计数余弦 + 时间分 + 重要性 + 置信度 + 反馈词扩展 + 去重
- 已建模但未完整落地：向量库、embedding 相似度、学习排序、图检索、多跳推理

### 4.5 使用

“使用”指的是：检索出来的记忆是否真的进入回答链路，并在回答后可追踪。

#### 已实现

- `EvidenceBundle` 被显式拼入 API 请求
- `ResponseGuard` 记录 `used_memory_ids`
- 回答提示词要求只把 `EvidenceBundle` 里的内容当长期记忆证据

#### 本次 demo 中实际观测

1. round 1

- `EvidenceBundle = EMPTY`
- API 回答没有声称“我记得你以前……”
- 证明“无历史证据”路径正常

2. round 17

- 用户要求汇总长期记忆
- 回答正确给出：
  - `林澈`
  - `杭州滨江`
  - `Python`
  - `茶`
  - `架构演示材料已完成`

3. round 18

- 回答不仅给出“以前用过 Java”，还说明为什么现在不应该继续用 Java
- 说明系统不仅能取当前 active，也能利用历史轨迹解释状态更新

#### 关键变量

- `messages_sent_to_api`
- `EvidenceBundle.items`
- `used_memory_ids`
- `source_sessions`

#### 边界说明

- 已实现：证据驱动回答、回答后留痕
- 已建模但未完整落地：逐句证据对齐、span-level attribution、生成后事实回写

### 4.6 安全

安全分为“写入安全门”和“披露安全门”。

#### 写入安全门

由 `PrivacyPolicy.can_store()` 执行。若 `candidate` 的 subject/predicate/scope/value 命中敏感模式，则拒绝写入。

#### 披露安全门

由 `PrivacyPolicy.can_disclose()` 执行。高敏感记忆即使存在，也默认不进入对外可见证据包。

#### 本次 demo 中实际观测

1. round 14

- 用户发送验证码 `123456` 和临时密码 `apple-pass-9988`
- 回答明确表示不会保存、不会写入长期记忆
- `memory_events` 中出现 `reject`
- `memory_state` 中没有对应 active 记录

2. round 21

- 用户追问“还能查到验证码或临时密码吗”
- 系统回答：查不到，并列出仍可见的安全记忆
- 说明拒写结果在后续检索层也成立

#### 关键变量

- `sensitive_denied_patterns`
- `candidate.sensitivity`
- `operation = reject`
- `allow_sensitive`
- `sensitivity`

#### 边界说明

- 已实现：凭证类拒写、敏感披露过滤基础版
- 已建模但未完整落地：更细粒度 PII 分类、脱敏摘要、基于角色/租户/场景的分级披露

### 4.7 拒答

拒答包括两层：

1. `ResponseGuard` 的“无证据拒答”
2. 检索/回答链路的“无相关记忆时如实说明没有”

#### 已实现

`response_guard.py` 中，当 `require_evidence_for_personalization = true` 且 `evidence.items` 为空时，返回固定拒答：

```text
I do not have reliable memory evidence for that.
```

#### 本次 demo 中实际观测

- round 1：虽然 `EvidenceBundle` 为空，但用户是在做当前轮自我介绍，不需要依赖历史个性化记忆，因此回答是基于当前消息自然回应，不属于记忆拒答场景。
- round 22：用户问“我上次去日本住的是哪家酒店？”
  - 系统没有相关长期记忆
  - 回答明确表示“目前没有找到相关记忆”
  - 这是本次最清晰的“无相关记忆时拒绝编造”

#### 关键变量

- `require_evidence_for_personalization = true`
- `abstain_message = "I do not have reliable memory evidence for that."`
- `evidence.items`
- `abstained`

#### 边界说明

- 已实现：无证据拒答开关、无相关记忆不编造
- 已建模但未完整落地：更细的拒答类型区分，例如“证据冲突拒答”“证据过时拒答”“权限不足拒答”

## 5. 关键变量的完整变化轨迹

### 5.1 计数层

本次最终结果：

- `round_count = 22`
- `final active memories = 32`
- `final events = 34`
- `superseded_count = 3`
- `reject_event_count = 2`

### 5.2 语言偏好槽位

1. round 3：创建 `java`
2. round 11：创建 `python`，并将 `java` 标为 `superseded`
3. round 17/18：回答阶段使用 `python` 作为当前值

### 5.3 饮品偏好槽位

1. round 4：创建 `咖啡`
2. round 12：创建 `茶`，并将 `咖啡` 标为 `superseded`
3. round 17/21：回答阶段使用 `茶` 作为当前值

### 5.4 任务槽位

1. round 5：创建 `state=open`
2. round 15：创建 `state=done`，旧 `open` 标为 `superseded`
3. round 17：回答阶段输出“已完成”

### 5.5 安全拒写轨迹

1. round 14：验证码、临时密码触发拒写
2. round 21：用户追问仍查不到，说明未进入长期记忆

## 6. 已实现 / 已建模但未完整落地

### 已实现

- 原始 `session_evidence` 留存
- 结构化槽位抽取
- `create / reinforce / supersede / reject`
- `memory_state + memory_events`
- 混合检索与证据包
- 写入隐私门与披露门基础版
- 无相关记忆不编造
- 同槽改口后的当前状态投影

### 已建模但未完整落地

- 连续时间衰减
- TTL 到期自动删除
- 更细粒度敏感信息分类
- 复杂计划/弱承诺记忆建模
- 向量检索与 embedding 相似度
- 多跳关系图与知识图谱
- 基于学习的重排与冲突裁决
- 生成后事实级归因与逐句证据绑定

## 7. 本次验收结论

基于这 22 轮中文真实交互，Cross-Agent 已经把七大部分中的核心主链打通：

- 抽取：可把高确定性中文表达转成结构化槽位和原始 session evidence
- 更新：可做 create、supersede，强化逻辑已实现
- 遗忘：可通过 `superseded + valid_to + 检索降权` 退役旧值
- 检索：可构造 EvidenceBundle，支持混合打分
- 使用：回答阶段真实使用证据包，并保留使用痕迹
- 安全：凭证类内容被拒绝写入与后续披露
- 拒答：无相关记忆时不会编造

当前最清楚的边界不是“七大部分没做”，而是“遗忘、安全、检索增强还停留在第一版工程化实现”，尤其连续时间衰减、复杂弱承诺记忆、向量检索和更细粒度权限控制还没有完整落地。
