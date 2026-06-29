# Cross-Agent

跨 Session 长期记忆会话 Agent 的本地 MVP，实现技术报告中的“类型化状态 + 时序事件 + 混合检索 + 安全治理”主线。

当前版本采用“规则控制面 + 可选 LLM 语义候选层”的混合写入架构。两路候选进入统一 `CandidateResolver`，经过硬过滤、跨来源合并、含时间衰减的写入评分、槽位冲突消解后取全局 Top 10，再交给状态治理层。Governor 使用硬时间门阻止旧值覆盖新状态，将未决冲突保存为 tentative，并在后续独立证据确认后提升为 active。默认配置不需要 API key；在线配置可启用 LLM 语义抽取，但模型没有直接写库权限。

## 项目结构

```text
configs/
  default.json                  # 所有阈值、路径、权重、策略开关
eval/
  run_actmem_eval.py            # 前 5 条 ActMemEval 验收入口
src/cross_agent/
  config.py                     # 配置加载与环境变量覆盖
  models.py                     # 领域模型：Candidate/Operation/Record/Evidence
  pipeline.py                   # 应用装配层
  writer/                       # 规则/LLM 抽取、统一重排与 Top 10 候选解析
  governor/                     # 写入治理与幂等决策
  store/                        # 存储抽象与 SQLite 实现
  reader/                       # 查询规划、混合召回、重排、证据包
  embedding/                    # Gemini Embedding 客户端与 SQLite 向量缓存
  policies/                     # 写入与披露策略
  answer/                       # 回答生成接口与本地实现
  guard/                        # 证据约束与拒答
  utils/                        # 文本、JSON 等纯工具
scripts/
  generate_architecture_report.py
reports/
  Cross-Agent架构设计报告.docx
```

## 运行验收

```bash
cd /Users/mac/workspace/Cross-Agent
python3 eval/run_actmem_eval.py
```

输出文件：

```text
eval/output/actmem_eval_summary.json
eval/output/actmem_eval_results.json
eval/output/cross_agent_eval.sqlite3
```

当前前 5 条验收结果：

```text
recall@5 = 1.0000
MRR      = 1.0000
```

## 配置优先

所有路径、阈值、召回权重、同义扩展、敏感策略和评测数量都在 `configs/default.json` 中配置。业务模块只接收 typed settings，不直接读取本地路径或硬编码常量。

支持的环境变量覆盖：

```text
CROSS_AGENT_DATASET
CROSS_AGENT_SQLITE_PATH
CROSS_AGENT_TENANT_ID
CROSS_AGENT_USER_ID
CROSS_AGENT_EMBEDDING_BASE_URL
CROSS_AGENT_EMBEDDING_MODEL
```

## API key 接入点

当前默认验收不需要 API key，使用 `ExtractiveAnswerGenerator` 离线跑通检索和证据链。若要接入在线模型，使用 `configs/yunai.json`：

```bash
cd /Users/mac/workspace/Cross-Agent
cp .env.example .env
# 编辑 .env，填入 YUNAI_API_KEY
python3 eval/run_actmem_eval.py --config configs/yunai.json
```

`configs/yunai.json` 已配置：

```text
base_url = https://yunai.chat/v1/chat/completions
model    = deepseek-v4-pro:floor
auth     = Authorization: Bearer ${YUNAI_API_KEY}

embedding_url   = https://yunai.chat/v1/embeddings
embedding_model = gemini-embedding-2-preview
dimensions      = 3072
```

API key 不写入源码或 JSON，只从 `YUNAI_API_KEY` 读取；`.env` 已被 `.gitignore` 忽略。

在线配置会同时启用 grounded answer、语义候选抽取、三态规则 + LLM 检索前置门控、Gemini Embedding 混合检索和 LLM 证据充分性验证。门控输出 `REQUIRED / BENEFICIAL / NONE`；召回候选经过统一重排后，只保留验证器确认能直接支持当前问题的记忆。文档 embedding 会缓存到 SQLite。在线配置要求意图模型、语义抽取、Embedding 和证据验证全部成功，重试失败后终止本次请求，不生成降级结果。核心能力均通过协议隔离：

```text
writer.extractor.MemoryExtractor        # LLM 候选抽取
answer.generator.AnswerGenerator        # LLM grounded answer
reader.memory_reader.MemoryReader       # 可替换向量检索实现
embedding.client.EmbeddingProvider      # 可替换 Embedding 服务
embedding.sqlite_index.VectorIndex      # 可替换 pgvector/HNSW 索引
```

模型供应商、模型名、temperature、timeout、API key 环境变量名应放入配置文件，再由装配层注入。

只评估自建 22 轮中文种子并禁止任何模型降级：

```bash
python3 scripts/run_chinese_seed_observation.py \
  --strict-online \
  --config configs/yunai.json \
  --seed eval/eval1/chinese_user_only_conversation_seed.json \
  --sqlite eval/eval1/chinese_seed_observation.sqlite3 \
  --json eval/eval1/chinese_seed_api_conversation.json \
  --markdown eval/eval1/chinese_seed_memory_observation.md
```

严格模式要求意图 LLM、语义抽取、Gemini Embedding 和 LLM 证据验证成功；允许 API 重试，但禁止 `rule_fallback`、token-only 结果和带错误的正式结果。显式规则直判及敏感内容在 LLM 前拦截仍按正常安全策略执行。
