# Cross-Agent

Cross-Agent 是一个跨 Session 长期记忆 Agent 的本地 MVP。它将用户记忆表示为类型化状态与时序事件，通过混合抽取、写入治理、混合检索和证据约束回答，让模型能够记住有用信息，同时避免把每句话都当成事实写入。

核心原则：**模型只能提出候选，规则决定是否写入；抽取不等于记住，检索不等于使用，没有可靠证据就不编造。**

## 核心能力

- 类型化记忆：支持 `fact`、`preference`、`task`、`relation`、`event`、`summary` 和 `sensitive`。
- 状态生命周期：支持 `active`、`tentative`、`superseded`、`archived` 和 `rejected`。
- 混合写入：规则抽取与可选 LLM 语义抽取统一进入候选解析和治理链路。
- 时间冲突处理：旧事实不会覆盖新状态；未决冲突先进入 tentative，后续证据可将其提升为 active。
- 混合检索：组合 embedding、token cosine、词法、时间、重要性和置信度信号。
- 安全控制：支持不保存、遗忘、敏感信息拒绝、披露过滤和拒答。
- 可审计存储：SQLite 同时保存记忆状态 `memory_state` 与操作事件 `memory_events`。

## 系统流程

```text
用户会话
  -> 规则控制面 + 可选 LLM 语义候选层
  -> CandidateResolver 过滤、合并、评分与 Top K
  -> WritePolicy / PrivacyPolicy
  -> Governor 状态迁移
  -> SQLite 状态与事件存储

用户问题
  -> REQUIRED / BENEFICIAL / NONE 查询规划
  -> 混合召回与重排
  -> 可选 LLM 证据充分性验证
  -> EvidenceBundle
  -> 证据约束回答
  -> ResponseGuard
```

## 写入策略

规则层是不可绕过的控制面，负责安全预扫描、明确槽位抽取，以及“不保存”和“忘记”等用户指令。LLM 只负责开放语义理解，不能直接修改数据库。

- 明确、低敏感的字面陈述可以由规则层抽取；在线模式下，语义层可补充结构化候选。
- 玩笑、反讽、假设和夸张表达不生成规则候选，只交给语义层结合完整语境判断。
- 语义层仅保留解释后仍成立、具有跨 Session 价值的持久字面主张。
- 非字面会话未启用语义层或语义抽取失败时，系统 fail closed：不写入，也不回退为规则事实。
- 密码、验证码、私钥及其他敏感或禁止内容停留在本地规则和隐私策略路径，不发送给语义模型。
- 所有候选都必须经过 `CandidateResolver`、`WritePolicy`、`PrivacyPolicy` 和 Governor 后才能改变记忆状态。

完整的数据模型、评分公式、冲突策略和状态迁移见 [长期记忆策略设计](docs/长期记忆策略设计.md)。

## 项目结构

```text
configs/
  default.json                  # 基础默认配置
  yunai.json                    # 在线 LLM + Embedding 配置
docs/
  长期记忆策略设计.md            # 完整策略说明
src/cross_agent/
  writer/                       # 规则与 LLM 候选抽取、候选解析
  governor/                     # 写入治理与状态迁移
  store/                        # SQLite 状态和事件存储
  reader/                       # 查询规划、混合召回与证据验证
  embedding/                    # Embedding 客户端与 SQLite 向量缓存
  policies/                     # 写入与隐私策略
  answer/                       # 证据约束回答
  guard/                        # 回答校验与拒答
  pipeline.py                   # 应用装配与编排
scripts/                        # 中文种子观测、报告生成与评测工具
tests/                          # 单元测试
eval0/ eval1/ eval2/           # 已生成的分阶段评测快照
```

## 环境要求

- Python 3.9+
- 在线模式需要 OpenAI-compatible Chat Completions 和 Embeddings 服务

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## 中文 Seed 在线评测

当前项目只使用仓库内自建的 22 轮中文用户 seed 进行正式评测，不依赖任何外部评测数据集。评测覆盖记忆写入、更新、冲突、遗忘、查询规划、混合召回、证据验证和受约束回答的完整链路。

复制环境变量模板并填入 API key：

```bash
cp .env.example .env
# 编辑 .env：YUNAI_API_KEY=...
```

`configs/yunai.json` 当前启用：

- OpenAI-compatible LLM：语义候选抽取、记忆意图分类、证据充分性验证和 grounded answer。
- Gemini Embedding：写入时建立向量缓存，查询时参与混合检索。
- SQLite：记忆状态、审计事件和 embedding 缓存。

运行严格在线评测：

```bash
python3 scripts/run_chinese_seed_observation.py \
  --strict-online \
  --config configs/yunai.json \
  --seed eval2/chinese_user_only_conversation_seed.json \
  --sqlite eval/output/chinese_seed_observation.sqlite3 \
  --json eval/output/chinese_seed_api_conversation.json \
  --markdown eval/output/chinese_seed_memory_observation.md
```

`--strict-online` 要求语义抽取、LLM 记忆意图、LLM 证据验证和 Embedding 全部成功；服务重试后仍失败会终止运行，不生成降级结果。运行中断后可在同一组路径上追加 `--resume`，从 checkpoint 和现有 SQLite 状态继续。

评测会生成：

```text
eval/output/chinese_seed_observation.sqlite3
eval/output/chinese_seed_api_conversation.json
eval/output/chinese_seed_api_conversation.checkpoint.json
eval/output/chinese_seed_memory_observation.md
```

将逐轮结果汇总为评测报告：

```bash
python3 scripts/summarize_chinese_seed_eval.py \
  --input eval/output/chinese_seed_api_conversation.json \
  --json eval/output/chinese_seed_eval_summary.json \
  --markdown eval/output/chinese_seed_eval_summary.md
```

汇总报告会记录 strict-online 审计、架构检查、记忆意图分布、证据召回、写入操作和最终记忆状态，并明确标记 `external_dataset_used=false`。

API key 只从 `YUNAI_API_KEY` 读取，不应写入 JSON 配置或源码；`.env` 已被 `.gitignore` 忽略。

## 配置覆盖

所有阈值、权重、路径和能力开关都集中在 `configs/*.json`。支持以下环境变量覆盖：

```text
CROSS_AGENT_SQLITE_PATH
CROSS_AGENT_TENANT_ID
CROSS_AGENT_USER_ID
CROSS_AGENT_LLM_MODEL
CROSS_AGENT_LLM_BASE_URL
CROSS_AGENT_EMBEDDING_BASE_URL
CROSS_AGENT_EMBEDDING_MODEL
```

## License

见 [LICENSE](LICENSE)。
