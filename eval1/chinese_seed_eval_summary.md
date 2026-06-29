# 中文自建种子评估汇总

- 评估范围：仅用户自建中文种子（22 轮）
- 外部数据集：未使用
- 模型：`deepseek-v4-pro:floor`
- Embedding：`gemini-embedding-2-preview`
- 严格在线模式：`True`
- 原始观测：`/Users/mac/workspace/Cross-Agent/eval/eval1/chinese_seed_api_conversation.json`

## 汇总

```json
{
  "memory_intent": {
    "counts": {
      "required": 14,
      "beneficial": 4,
      "none": 4
    },
    "decision_source_counts": {
      "rule": 5,
      "llm": 17
    },
    "skipped_rounds": 4,
    "retrieval_rounds": 18
  },
  "strict_online_audit": {
    "passed": true,
    "rule_fallback_count": 0,
    "embedding_error_count": 0,
    "token_fallback_item_count": 0,
    "empty_answer_count": 0,
    "completed_answer_api_calls": 22
  },
  "evidence": {
    "rounds_with_evidence": 17,
    "total_evidence_items": 127,
    "probe_counts": {
      "applied": 4,
      "accepted": 4
    }
  },
  "writes": {
    "operation_counts": {
      "create": 26,
      "reject": 4,
      "supersede": 3
    }
  },
  "final_memory": {
    "record_count": 29,
    "status_counts": {
      "active": 26,
      "superseded": 3
    },
    "type_counts": {
      "fact": 2,
      "event": 14,
      "preference": 8,
      "task": 5
    },
    "event_count": 33
  }
}
```

## 逐轮门控结果

| 轮次 | intent | 来源 | rule score | confidence | 跳过检索 | evidence | probe |
|---:|---|---|---:|---:|---|---:|---|
| 1 | required | rule | 0.9800 | 0.9800 | 否 | 0 | - |
| 2 | required | llm | 0.4500 | 0.9500 | 否 | 1 | - |
| 3 | beneficial | llm | 0.5800 | 0.9000 | 否 | 2 | 0.6104/0.20 (通过) |
| 4 | beneficial | llm | 0.7200 | 0.8500 | 否 | 3 | 0.6372/0.20 (通过) |
| 5 | required | llm | 0.4500 | 0.9000 | 否 | 4 | - |
| 6 | beneficial | llm | 0.4500 | 0.8000 | 否 | 5 | 0.6513/0.20 (通过) |
| 7 | required | llm | 0.4500 | 0.9500 | 否 | 5 | - |
| 8 | none | llm | 0.7200 | 0.9500 | 是 | 0 | - |
| 9 | none | rule | 0.0500 | 0.9500 | 是 | 0 | - |
| 10 | none | rule | 0.0500 | 0.9500 | 是 | 0 | - |
| 11 | required | llm | 0.5200 | 0.9500 | 否 | 8 | - |
| 12 | required | llm | 0.4500 | 0.9500 | 否 | 9 | - |
| 13 | required | llm | 0.5200 | 0.9000 | 否 | 10 | - |
| 14 | none | llm | 0.4500 | 0.9000 | 是 | 0 | - |
| 15 | required | llm | 0.4500 | 0.9000 | 否 | 10 | - |
| 16 | beneficial | llm | 0.4500 | 0.9000 | 否 | 10 | 1.0000/0.20 (通过) |
| 17 | required | rule | 0.8800 | 0.8800 | 否 | 10 | - |
| 18 | required | llm | 0.4500 | 0.9500 | 否 | 10 | - |
| 19 | required | llm | 0.4500 | 0.9500 | 否 | 10 | - |
| 20 | required | llm | 0.4500 | 0.9500 | 否 | 10 | - |
| 21 | required | llm | 0.4500 | 0.9000 | 否 | 10 | - |
| 22 | required | rule | 0.9800 | 0.9800 | 否 | 10 | - |

### 指标解释

- **轮次**：当前用户输入在这组 22 轮连续对话中的顺序编号。门控判断和检索都针对该轮输入执行；随着轮次增加，前面各轮成功写入的 active 记忆可能成为后续轮次的检索候选。
- **intent**：系统对“本轮是否需要使用跨 Session 长期记忆”的最终三态判断。
  - `required`：回答依赖历史事实、偏好、任务、关系、事件或既有工作；即使暂时没有召回证据，也仍会执行检索。
  - `beneficial`：不用长期记忆也能回答，但相关记忆可以带来实质性的个性化或上下文补充；检索后还要经过 `probe` 相关性门控。
  - `none`：本轮属于通用知识、翻译/总结当前输入、当前 Session 上下文已经足够，或明确不应使用记忆；直接跳过长期记忆检索。
- **来源**：产生最终 `intent` 的决策器。
  - `rule`：规则分已经落在确定区间，直接由规则决策。本次配置中 `rule score >= 0.80` 判为 `required`，`rule score <= 0.20` 判为 `none`。
  - `llm`：规则分位于 `(0.20, 0.80)` 的模糊区间，由 LLM 结合当前请求、当前 Session 的近期上下文和规则分进行三态分类；本次配置要求 LLM 置信度至少为 `0.65`。
  - 系统实现还可能产生 `policy`（显式禁用记忆或当前 Session 指代被硬规则直接处理）和 `rule_fallback`（LLM 不可用、输出无效或置信度不足后降级），但本表没有出现这两种来源。
- **rule score**：规则层估计“当前请求对长期记忆的依赖程度”，范围为 `0～1`，越高表示越可能需要记忆。它取命中规则中的最高分，例如显式提及历史为 `0.98`、个人记忆对象为 `0.88`、个人状态表达为 `0.72`、一般第一人称上下文为 `0.45`，无明显信号时默认为 `0.05`。该分数只负责规则直判或为 LLM 提供参考，**不是**最终 `intent` 的概率，也不是召回证据的相关性分数。
- **confidence**：最终 `intent` 判断的置信度，范围为 `0～1`。
  - 来源为 `rule` 且判断为 `required` 时，置信度等于 `rule score`。
  - 来源为 `rule` 且判断为 `none` 时，置信度为 `1 - rule score`，因此规则分 `0.05` 对应置信度 `0.95`。
  - 来源为 `llm` 时，该值是 LLM 对其三态分类结果给出的置信度。
  - 它只描述“是否应使用记忆”这一门控决策的可信程度，不等于记忆记录自身的 `confidence`，也不等于检索排序分。
- **跳过检索**：是否在门控阶段就终止长期记忆读取。
  - `是`：`intent = none`，不会读取 active 记忆、生成向量、执行混合排序或构建 EvidenceBundle，因此 `evidence` 为 `0`。
  - `否`：进入检索流程；但不保证一定能得到证据，例如第 1 轮尚无历史记忆，所以执行了检索而 `evidence = 0`。
- **evidence**：本轮最终 EvidenceBundle 中的证据条数，不是初始候选数。它是在租户/用户隔离、active 状态读取、记忆类型偏好加分、Embedding + Token Cosine + BM25 混合排序、隐私披露过滤、`beneficial` 探测门控、按来源 Session 去重和 Top-K 截断之后的最终数量。本次种子观测请求设置 `top_k = 10`，所以该列最大为 `10`。
- **probe**：仅对 `intent = beneficial` 应用的“证据是否足够相关”探测，格式为 `top_score/threshold (结果)`。
  - `top_score`：初排且通过披露过滤的候选中，所有候选的 Embedding、Token Cosine 和 BM25/lexical 单通道相关性分数的最大值；它不是最终加权综合排序分。
  - `threshold`：本次配置的最低接受阈值 `0.20`。
  - `通过`：`top_score >= 0.20`，保留后续重排和去重得到的证据；若未通过，则最终 EvidenceBundle 置空。
  - `-`：没有应用探测。`required` 直接使用检索结果，`none` 则在检索前已经跳过。

### 读表示例

- 第 3 轮的规则分 `0.5800` 位于模糊区间，因此交给 LLM；LLM 以 `0.9000` 置信度判断为 `beneficial`。随后探测最高相关性为 `0.6104`，高于 `0.20`，因此通过并最终返回 2 条证据。
- 第 8 轮虽然规则分为 `0.7200`，但仍未达到规则直判 `required` 的 `0.80` 阈值；LLM 最终判断为 `none`，所以跳过检索，证据数为 0。
- 第 9、10 轮的规则分均为 `0.0500`，低于 `0.20`，直接由规则判为 `none`；其门控置信度按 `1 - 0.05` 计算为 `0.9500`。
