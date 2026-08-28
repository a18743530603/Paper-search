# Paper Hunter 后续开发路线与实施日志

> 当前项目版本：`v0.17.1`
>
> 日志建立日期：`2026-08-16`
>
> 当前阶段：`SQLite → MySQL Phase 6 Fresh MySQL Cutover已完成；Business Database正式使用MySQL，LangGraph Checkpoint继续使用SQLite SqliteSaver`

本文件用于在后续 Codex 对话之间同步开发路线和实际进度。它记录“接下来为什么做、做到哪里、验证结果和下一步”，功能细节仍写入 `README.md`、`PROJECT.md`、`CHANGELOG.md` 和 `AGENTS.md`，实验数据仍写入 `EXPERIMENTS.md`。

## 维护规则

- 一次只实现一个边界清楚的小步骤，完成并测试后再进入下一步。
- 每次完成后更新顶部的当前阶段，并在“实施日志”追加日期、版本、改动、测试、结论和下一步。
- 只有代码、测试和正式文档全部完成，步骤才可标记为“已完成”。
- Checkpoint 状态恢复与后台任务可靠恢复必须分开描述，不得混淆。
- 模型 Token 只记录 API 返回并可靠持久化的真实 usage；估算 Token 只能用于上下文预算。
- 不为了展示概念改造成自由 ReAct 或多 Agent；所有描述必须符合实际架构。

## 总体路线

```text
A6 Agent 可靠性评测与可观测性
  -> A6.1 统一 Agent Trace
  -> A6.2 八个固定场景测试
  -> A6.3 interrupt / resume / restart 专项验证
  -> A6.4 Trace Metrics 聚合
  -> A6.5 JSON / 文本 Evaluation Report

A7 最小 MCP Server
  -> 复用现有 Service Layer
  -> 暴露 2 至 3 个只读或受控 Tool
  -> MCP Client 冒烟测试

B1 可靠 Worker / Queue
  -> 持久任务队列
  -> Worker 重试与超时
  -> Web 进程重启后的任务重新投递
```

## A6：Agent 可靠性评测与可观测性

### A6.1 统一 Agent Trace

**状态：已完成（v0.11.0）**

目标是在不重复修改每个节点业务逻辑的前提下，统一记录 LangGraph 关键节点执行轨迹。

计划字段：

```text
task_id
thread_id
node_name
status
start_time
end_time
duration_ms
tool_calls
model_calls
input_tokens
output_tokens
retry_count
error
```

实施边界：

- 新增 `agent_traces` SQLite 表及创建、结束、查询、任务回填函数。
- 在集中注册 LangGraph Node 的位置使用统一包装器，不在每个节点复制日志代码。
- 首次 `plan_research` 发生在业务任务创建前，先按 `thread_id`记录，创建任务后再回填`task_id`。
- `interrupt()`记录为`interrupted`，恢复后的同名节点形成下一次尝试并增加`retry_count`。
- 节点异常必须继续向上抛出，Trace 不能改变现有错误处理和检查点语义。
- 只从真实节点结果保存 Tool 摘要；本步骤没有可靠模型 usage 时，Token 字段保持空值。

验证要求：

- 成功节点保存起止时间和非负耗时。
- 异常节点保存失败状态和错误，并继续抛出原异常。
- 中断节点与恢复后的节点可以区分。
- `thread_id`对应的早期轨迹能在任务创建后回填`task_id`。
- 测试只使用临时 SQLite 和 mock，不调用真实外部 API。

### A6.2 八个固定场景测试

**状态：已完成（v0.11.1）**

固定验证以下行为：

1. Planner 失败后使用原问题单查询 fallback。
2. 用户拒绝检索计划后不执行搜索 Tool。
3. 未注册 Tool 被 Registry 拒绝且无副作用。
4. 搜索源失败时正确降级或记录失败，不无条件拖垮整个任务。
5. 单篇论文失败时其余论文继续，任务允许 partial success。
6. 没有合法 Evidence 时不调用报告模型。
7. 使用原 `thread_id`恢复到正确 LangGraph 状态，已完成节点不重复执行。
8. 重复触发不产生重复任务或副作用。

重复触发分成两层验证：数据库状态原子抢占，以及检查点恢复后的工作流幂等。

场景测试统一位于`tests/agent/test_scenarios.py`，当前覆盖映射如下：

| 场景 | 自动化断言 |
|---|---|
| Planner失败 | 原问题成为唯一查询，fallback错误可追踪 |
| 用户拒绝计划 | 工作流进入`cancelled`，搜索调用为0 |
| 非法Tool | Registry抛出错误，已注册Tool副作用为0 |
| 搜索源失败 | 健康来源继续返回，工具状态为`partial` |
| 单篇论文失败 | 失败记录为访问限制，其余论文继续准备 |
| 无合法Evidence | 返回确定性拒答，报告模型调用为0 |
| Checkpoint恢复 | 跨SQLite连接恢复，Planner调用仍为1 |
| 重复触发 | 下载与报告均只有一个并发请求取得资格 |

本步骤只证明固定的受控场景通过，不把`8/8`包装成真实线上任务成功率。Checkpoint更完整的多中断恢复、节点不重放和Trace一致性继续在A6.3验证。

### A6.3 Checkpoint 恢复专项验证

**状态：已完成（v0.11.2）**

- 验证范围只有 `SqliteSaver + thread_id + Command(resume=...)`。
- 模拟关闭旧 SQLite 连接、创建新图实例，再从计划确认、论文选择或报告确认中断点恢复。
- 断言 Planner、搜索和已完成论文节点不会重复执行。
- 不宣称恢复进程内 `BackgroundTasks`；该问题属于 B1。

实现结果：

- 计划确认：关闭旧连接后恢复，Planner不重跑，拒绝计划时搜索调用为0。
- 论文选择：搜索完成并停在选择节点后重启，恢复选择不会重新规划、检索或去重。
- 报告确认：确认后只完成第一篇论文准备便中断，下一实例从第二篇继续；已完成论文、证据检索和报告模型均不重复执行。
- Trace同步验证`interrupted/success`尝试顺序及关键节点执行次数；不能只看最终`completed`状态判断恢复正确。
- 专项测试位于`tests/agent/test_checkpoint_recovery.py`，结果为`3 passed`。

### A6.4 Agent Metrics

**状态：已完成（v0.11.3）**

优先聚合：

- Scenario Pass Rate
- Task Completion Rate
- Tool Success Rate
- Approval Compliance Rate
- Checkpoint Recovery Rate
- Fallback Success Rate
- Average Task Latency
- Average Node Latency
- Failure Distribution by Node

`Average Model Calls`与Token指标只有在来源真实、定义明确时加入。

实现结果：

- `agent-metrics-v1`为每项比例保存分子、分母、排除数、百分比和文字定义。
- Task Completion只统计报告完成与系统失败；等待人工操作、未开始报告和用户取消不进入分母。
- Approval Compliance当前验证拒绝计划后零搜索；Checkpoint Recovery排除尚未尝试恢复的暂停。
- 任务和节点耗时都返回样本数；失败与部分失败按节点分开统计。
- 模型调用和Token只使用Trace中的真实usage；无观测值时返回`null`和覆盖率。
- pytest结果没有混入运行数据库，`scenario_pass_rate`暂为`not_persisted`，A6.5再读取独立场景结果。
- 内部接口为`GET /api/agent/metrics`，本阶段不建设Dashboard。

### A6.5 Agent Evaluation Report

**状态：已完成（v0.11.4）**

- 第一版输出 JSON 和可读文本，不优先建设 Dashboard。
- 报告必须包含样本数、分子/分母、失败场景和指标口径，不能只展示百分比。
- Trace 是真实运行记录，pytest 场景结果是受控验证数据，两者在报告中分开展示。

实现结果：

- 冻结A6.2/A6.3共11条场景，通过pytest JUnit XML读取真实执行结果；失败、跳过和漏跑都不会被计为通过。
- `agent-evaluation-report-v1`分别保存`controlled_scenarios`与`runtime_observability`，不把离线测试成功率冒充线上成功率。
- 文本报告展示样本数、场景结果、各运行比例的分子/分母/排除数、延迟、失败分布和限制。
- JSON与文本使用原子写入，同时生成带时间戳文件和`latest`文件。
- 开发命令为`uv run python scripts/run_agent_evaluation.py`；`--skip-runtime-metrics`不读取本地运行SQLite。
- 报告进入`outputs/agent_evaluation/`并由Git忽略；代码、场景清单和测试进入仓库。
- 本次离线报告实测`11/11 passed`；运行样本被主动跳过，因此运行指标为N/A而不是虚假100%。

### A6阶段完成结论

- A6.1：真实Node Trace。
- A6.2：8条固定行为契约。
- A6.3：3个中断点与逐篇循环恢复。
- A6.4：口径明确的运行Metrics。
- A6.5：可复现JSON/文本评测报告。

A6证明的是当前单Agent工作流在受控故障、审批、幂等和Checkpoint场景下的行为，并提供真实运行可观测入口；它不证明公网SLA，也不包含后台任务跨进程恢复。

## A7：最小 MCP Server

**状态：已完成（v0.12.3）**

保持以下架构：

```text
Service Layer
  -> FastAPI
  -> MCP Server
```

首版只考虑复用现有能力：

- `search_literature`
- `retrieve_paper_evidence`
- `get_paper_metadata`

MCP 层不复制搜索、数据库或 RAG 逻辑。需要增加 MCP Client 冒烟测试，验证客户端调用经过 MCP Tool 到达现有 Service 并返回结构化结果。

### A7.1实现结果

- 使用官方MCP Python SDK 1.x新增独立stdio Server，不修改FastAPI页面或路由。
- 首个`get_paper_metadata` Tool复用`db.get_paper()`和`paper_access_url()`；只返回公开字段，过滤本地文件路径。
- Tool声明只读、非破坏、幂等和封闭世界安全注解；无下载、写库、外部API或模型调用。
- 官方内存Client完成协议初始化、`list_tools`、`call_tool`结构化调用和标准错误验证，专项`6 passed`，完整离线测试`150 passed`。

### A7.2实现结果

- 新增`search_literature` MCP Tool，通过白名单Registry复用现有LangChain StructuredTool。
- 输入校验、arXiv/Crossref双来源检索、来源统计和部分失败语义保持不变；MCP层不写数据库或下载文件。
- 协议级Client验证结构化候选与`partial`状态；非法空查询在访问外部服务前被现有校验拒绝。
- MCP专项`9 passed`，完整离线测试`153 passed`，外部检索全部使用mock。

### A7.3实现结果

- 新增`retrieve_paper_evidence` MCP Tool，通过Registry复用现有页码证据检索。
- 只面向已索引论文；无证据返回结构化失败，不自动下载、解析或索引，也不暴露论文准备Tool。
- 对论文业务状态只读；Seed查询向量可能访问外部Embedding服务并更新内部缓存，使用`openWorldHint=True`准确描述。
- MCP专项`12 passed`，完整离线测试`156 passed`，测试没有读取真实PDF或调用外部API。

### A7.4实现结果

- 新增独立stdio Client冒烟命令，以真实Python子进程启动MCP Server。
- 只执行协议初始化与Tool发现，严格验证Server名称和三个Tool，不调用业务数据或外部API。
- 真实结果为Server `paper-hunter`、版本`1.29.0`、协议`2025-11-25`、stdio与3个Tool；输出为稳定JSON。
- 新增真实子进程测试；MCP专项`13 passed`，完整离线测试`157 passed`。

### A7阶段完成结论

- FastAPI与MCP Server并列复用同一Service Layer，没有维护第二套业务实现。
- MCP只暴露元数据查询、公开文献检索和已索引证据检索；下载、解析、索引和报告生成不对MCP开放。
- 内存Client覆盖结构化调用和错误语义，stdio Client覆盖真实进程边界和协议握手。
- A7不解决后台任务跨进程可靠性；下一步进入B1 Worker/Queue。

## B1：可靠 Worker / Queue

**状态：已完成（B1.1-B1.7，v0.13.1）**

该阶段解决 `BackgroundTasks`不能覆盖的问题：

- Web 进程重启后任务仍能重新投递。
- 下载、解析、索引和报告任务由独立 Worker 执行。
- 支持持久化、超时、有限重试、死信或最终失败状态。
- 数据库原子抢占继续保留，消息队列不能替代幂等状态转换。

### B1.1 SQLite持久下载队列

**状态：已完成（v0.13.0）**

- 下载状态抢占与任务入队使用同一SQLite事务。
- 独立Worker通过租约领取任务，支持指数退避、最多3次尝试和最终失败。
- Worker中断后，过期租约任务可被新Worker重新领取。
- 当前只覆盖“仅下载”和“失败重试”，完整回归`163 passed`。

### B1.2 论文准备任务迁移

**状态：已完成（v0.13.0）**

- 将`download -> parse -> index`准备闭环迁移为持久任务，复用`paper_workflow.py`。
- 明确逐阶段重入和失败恢复，避免重启后重复已完成阶段。
- 单独下载与完整准备必须继续共享下载幂等边界。

实现结果：队列专项`12 passed`，完整回归`169 passed`。

### B1.3 单独解析与索引任务迁移

**状态：已完成（v0.13.0）**

- 将详情页的单独`parse`与`index`按钮迁入持久Worker。
- 与`prepare_paper`共享论文级活动任务互斥，避免两个入口重复处理相同阶段。
- 分别验证解析和索引Worker中断后的租约恢复。

实现结果：队列专项`18 passed`，完整回归`175 passed`。

### B1.4 RAG问答任务迁移

**状态：已完成（v0.13.0）**

- 将单篇论文`ask`任务迁入持久Worker，继续保留问题长度、索引状态和可选重排校验。
- 设计`rag_query`与后台任务的原子创建，避免问题记录停在`answering`却没有执行任务。
- 验证DeepSeek或重排失败后的有限重试、最终失败和重复提交边界。

实现结果：队列专项`27 passed`，完整回归`184 passed`；相同活动问题去重，已完成回答在普通重放和租约接管时都不重复调用模型。

### B1.5 Worker自动心跳续租

**状态：已完成（v0.13.0）**

- 通过原子租约续期避免健康长任务被其他Worker误回收。
- 默认90秒租约，心跳间隔为租约三分之一且最多30秒。
- 租约已过期或Worker身份不匹配时禁止续期，保留明确所有权边界。

实现结果：队列专项`30 passed`，完整回归`187 passed`；真实并发测试证明任务超过原租约后仍保持`running`且不能被恢复器接管，失去所有权的Worker也不能确认成功或记录普通失败。

### B1.6 智能研究搜索任务迁移

**状态：已完成（v0.13.0）**

- `enqueue_research_search_job()`在一个`BEGIN IMMEDIATE`事务中完成`awaiting_confirmation → running`与`research_search`任务创建。
- 重复确认和并发双击复用同一活动任务；未获得人工确认前不会入队，保留审批边界。
- Worker复用`execute_research_task()`和原LangGraph检查点；失败有限重试，已进入`awaiting_selection`的结果重放时不重复搜索。
- Web或Worker初始化只恢复没有活动任务的旧`running`孤儿，不破坏有效队列。

实现结果：新增6项测试，队列专项`36 passed`，完整回归`193 passed`。下一步只迁移研究报告生成。

### B1.7 研究报告任务迁移

**状态：已完成（v0.13.1）**

- `enqueue_research_report_job()`在一个`BEGIN IMMEDIATE`事务中创建或重置`research_reports`业务记录并写入`research_report`任务。
- 重复点击和并发重新生成只保留一个活动任务；完成后用户仍可显式创建新一轮报告。
- Worker复用`execute_research_report()`，由原LangGraph检查点继续逐篇准备、检索、生成和引用检查。
- 失败进入有限重试；报告已经`completed`时，租约恢复后的处理器直接成功，不重复调用DeepSeek。
- 启动恢复只标记没有活动持久任务的旧报告孤儿，任务页和API展示队列状态及尝试次数。

实现结果：新增6项测试，队列专项`42 passed`，完整回归`199 passed`。B1面向用户的可靠后台任务迁移完成。

## B2：统一研究工作台与上下文管理

### B2.1 单入口会话骨架

**状态：已完成（v0.14.0）**

- 首页统一创建研究任务，不再要求用户预先判断“普通搜索”还是“智能研究”。
- 同一任务页串联计划补充、人工确认、论文选择、报告问题、报告生成和报告后追问。
- `research_tasks.report_question`把检索主题与报告问题分离；`research_messages`保存任务内消息和异步状态。
- 计划修订更新原LangGraph `thread_id`，不会额外创建第二个研究任务。
- 报告后追问进入`research_followup`持久任务，复用所选论文证据检索、重试和租约恢复。

实现结果：完整离线测试`205 passed`。报告状态轮询在客户端和接口层禁用缓存，终态会自动重载；当前模型上下文只包含当前问题、当前报告和新召回证据，尚未使用完整历史消息。

### B2.2 会话上下文与记忆管理

**状态：待开始**

- 先定义短期历史窗口、摘要触发条件、Token预算和可审计的压缩结果。
- 区分任务内短期记忆、结构化研究状态和跨任务长期偏好，避免把所有消息无上限塞给模型。
- 使用固定场景评测上下文连续性、证据忠实性、延迟和Token成本，再决定是否引入更复杂的记忆存储。

## 暂不纳入当前主线

- 多 Agent：当前复杂度和项目收益不匹配。
- GraphRAG：当前论文证据问题主要是单段和页码级检索。
- Dashboard：A6 第一版先保证 Trace、场景和指标口径正确。
- 跨任务长期用户记忆：先完成B2.2任务内上下文管理，再评估是否有真实用户价值。

## 实施日志

### 2026-08-16：建立路线日志

- 当前代码版本：`v0.10.1`。
- 已确认后续顺序为 `A6 -> A7 -> B1`，多 Agent 暂不考虑。
- A6 拆成 Trace、固定场景、Checkpoint 专项、Metrics 和 Report 五个小步骤。
- A6.1 已开始进行架构检查，尚未完成代码、测试或版本发布。
- 下一步：完成统一 Node Trace 的数据库模型、包装器和离线测试。

### 2026-08-16：完成A6.1统一Agent Trace

- 发布版本：`v0.11.0`。
- 新增`agent_traces`表、四个数据库操作函数、统一`trace_node()`包装器和独立Trace查询API。
- 生产检查点调用通过`configurable`显式传入Trace开关、线程ID和任务ID；首次规划轨迹支持任务创建后回填。
- 真实`interrupt()`记为`interrupted`，恢复节点形成下一次尝试；Trace失败不改变业务，节点失败仍抛出原异常。
- 模型调用和Token字段暂不填充，未使用估算值伪造usage。
- 聚焦测试`4 passed`；完整离线测试`125 passed`，无真实外部API调用。
- 结论：A6.1完成，可以基于稳定Trace进入固定可靠性场景测试。
- 下一步：A6.2先整理8个场景与现有测试覆盖映射，再只补缺失场景，避免重复测试。

### 2026-08-16：完成A6.2固定Agent场景测试

- 发布版本：`v0.11.1`。
- 新增`tests/agent/test_scenarios.py`，集中实现8条Agent可靠性场景；原有零散回归测试继续保留，场景文件负责表达产品级行为契约。
- 场景覆盖Planner fallback、Human-in-the-loop拒绝边界、Tool Registry白名单、搜索来源降级、单篇失败隔离、Evidence Quality Gate、Checkpoint跨连接恢复和重复副作用原子抢占。
- 所有外部能力均使用mock，数据库使用临时SQLite；场景测试`8 passed`，完整离线测试`133 passed`。
- 结论：现有实现通过第一批固定可靠性验收，没有为了让测试通过而修改生产业务逻辑。
- 下一步：A6.3专项验证计划确认、论文选择和报告生成三个中断点的跨连接恢复，并断言已完成节点与Tool不重复执行。

### 2026-08-16：完成A6.3 Checkpoint恢复专项验证

- 发布版本：`v0.11.2`。
- 新增`tests/agent/test_checkpoint_recovery.py`，覆盖三个真实`interrupt()`位置及报告逐篇循环的中途恢复。
- 每次恢复都关闭旧SQLite连接、创建新`SqliteSaver`和新LangGraph实例，并沿用原`thread_id`执行`Command(resume=...)`或继续未完成节点。
- 调用计数验证Planner、搜索、已完成论文和报告模型不重放；Agent Trace进一步校验暂停与恢复尝试顺序。
- 专项测试`3 passed`，完整离线测试`136 passed`，未修改生产业务逻辑，也未调用真实外部API。
- 结论：A6.3完成，已证明LangGraph业务检查点可恢复；进程内后台任务恢复仍留给B1可靠Worker/Queue。
- 下一步：A6.4从`agent_traces`聚合第一版Agent Metrics，先定义每项指标的分子、分母和空数据行为，不做Dashboard。

### 2026-08-16：完成A6.4 Agent Metrics

- 发布版本：`v0.11.3`。
- 新增只读数据库快照函数、`agent_metrics_service.py`和`GET /api/agent/metrics`内部接口。
- 指标覆盖任务完成、Tool成功、审批遵守、Checkpoint恢复、fallback成功、任务/节点耗时、失败分布和真实usage覆盖。
- 每项比例固定分子、分母和排除规则；空数据返回`null`，等待用户操作不算系统失败，估算Token不进入usage。
- pytest场景结果继续与真实运行Trace分离，当前运行API不伪造Scenario Pass Rate。
- A6.4专项测试`4 passed`，完整离线测试`140 passed`，未调用真实外部API。
- 结论：A6.4完成，已有可供程序读取的稳定指标结构，但尚未形成一次可保存、可阅读的正式评测报告。
- 下一步：A6.5生成JSON与文本Evaluation Report，显式分开展示固定场景结果和运行Trace指标，并保存生成时间与口径版本。

### 2026-08-16：完成A6.5与整个A6阶段

- 发布版本：`v0.11.4`。
- 新增11条冻结场景清单、JUnit解析、结构化报告、文本渲染、原子保存和独立开发命令。
- 使用`--skip-runtime-metrics`完成真实离线执行，场景结果`11/11 passed`；没有读取本地运行SQLite，也没有调用外部API。
- 报告明确分开受控场景与运行Trace；当前运行样本为0时所有运行比例与耗时显示N/A。
- A6.5新增4项报告测试，完整离线测试`144 passed`；A6.1至A6.5全部完成。
- 生成产物位于`outputs/agent_evaluation/`并由Git忽略，防止本地运行信息进入公开仓库。
- 结论：Paper Hunter已经具备Agent Trace、可靠性场景、Checkpoint专项恢复、Metrics和可复现评测报告闭环。
- 下一步：在上传A6版本前检查Git差异和敏感文件；随后进入A7最小MCP Server，复用现有Service Layer暴露2至3个受控工具。

### 2026-08-16：完成A7.1最小MCP只读接入

- 发布版本：`v0.12.0`。
- 新增官方MCP Python SDK 1.x、独立`mcp_server.py`与`get_paper_metadata`只读Tool。
- Tool复用现有数据库和访问链接服务，返回结构化公开元数据并过滤`local_path`；没有新增数据表、FastAPI路由或外部API调用。
- 使用官方内存传输运行真实MCP Client，验证初始化、Tool发现、调用和标准Tool错误；专项`6 passed`，完整离线测试`150 passed`。
- 结论：FastAPI和MCP已形成共享Service Layer的双入口雏形，但A7目前只有一个只读能力，不能宣称完整工具集。
- 下一步：A7.2接入`search_literature`，保持现有输入限制与arXiv/Crossref单源失败降级语义。

### 2026-08-16：完成A7.2 MCP文献检索Tool

- 发布版本：`v0.12.1`。
- 新增`search_public_literature()`和同名MCP Tool，通过白名单Registry复用现有StructuredTool，不复制搜索业务。
- Tool返回候选、来源统计、状态、错误和耗时；单来源失败继续返回`partial`及健康来源结果。
- MCP适配不写库、不下载、不调用模型；真实运行需要访问arXiv/Crossref，测试全部使用mock。
- MCP专项`9 passed`，完整离线测试`153 passed`。
- 下一步：A7.3接入只读证据检索Tool，并补最终stdio客户端冒烟验证。

### 2026-08-16：完成A7.3 MCP论文证据检索Tool

- 发布版本：`v0.12.2`。
- 新增证据检索MCP适配和Tool，返回已索引论文的物理页码证据及评分。
- 无证据或未准备论文不触发`prepare_paper_for_research`；MCP工具集合中不存在下载、解析或索引入口。
- 如Seed已配置，检索可能调用Embedding并写查询缓存；该技术副作用已明确记录，论文业务状态保持只读。
- MCP专项`12 passed`，完整离线测试`156 passed`。
- 下一步：A7.4增加独立stdio客户端冒烟命令并完成A7阶段。

### 2026-08-16：完成A7.4与整个A7阶段

- 发布版本：`v0.12.3`。
- 新增stdio Client冒烟脚本和真实子进程测试，只握手和列出Tool，不调用业务能力。
- 真实运行发现Server `paper-hunter`、协议`2025-11-25`及三个预期Tool，JSON输出干净可机器读取。
- MCP专项`13 passed`，完整离线测试`157 passed`。
- 结论：最小MCP Server已具备共享Service Layer、受控Tool、协议测试和真实传输验证，A7完成。
- 下一步：B1先设计持久任务模型与Worker边界，再替换关键`BackgroundTasks`，不能直接堆Redis/Celery名称。

### 2026-08-17：完成B1.1 SQLite持久下载队列

- 发布版本：`v0.13.0`。
- 新增`background_jobs`表和下载任务原子入队：论文状态更新与任务创建同事务提交，并发请求只产生一个活动任务。
- 新增独立Worker、任务处理器注册、租约领取、指数退避、最多3次尝试、最终失败和过期租约恢复。
- Web的“仅下载”和“失败重试”不再创建进程内`BackgroundTasks`；Worker通过`python -m my_agent_project.worker`独立运行。
- 状态API增加最近下载任务状态和尝试次数；旧前端字段保持兼容。
- 新增6项离线测试，完整回归`163 passed`，没有调用真实下载地址或外部API。
- 结论：Web重启后已入队的直接下载不会丢失，Worker崩溃后可由租约恢复；但其他长任务尚未迁移，B1还没有整体完成。
- 下一步：B1.2迁移用户触发的论文准备闭环，复用现有`paper_workflow.py`并验证在下载、解析和索引阶段分别中断后的重入行为。

### 2026-08-17：完成B1.2持久论文准备任务

- 继续使用当天版本`v0.13.0`，不新增第二个版本号。
- 新增准备任务原子入队：按论文现状锁定下载、解析或索引阶段，并与任务记录同事务提交。
- `/prepare`迁出Web进程内`BackgroundTasks`；Worker复用`prepare_paper_for_qa()`执行完整闭环并跳过已完成阶段。
- 活动下载和准备任务按论文互斥；应用启动恢复不会改写仍有活动任务的`downloading/parsing/indexing`状态。
- 新增6项B1.2测试，队列专项累计`12 passed`，完整离线回归`169 passed`，没有调用外部API或读取真实PDF。
- 结论：直接下载和完整论文准备已支持跨Web重启持久化及Worker租约恢复；B1仍未覆盖所有长任务。
- 下一步：B1.3迁移详情页单独解析和单独索引入口，并验证它们与完整准备任务的互斥及中断恢复。

### 2026-08-17：完成B1.3单独解析与索引持久任务

- 继续更新当天`v0.13.0`。
- 新增`parse_paper/index_paper`原子入队、Worker处理器、有限重试和租约最终失败同步。
- 详情页单独解析与索引路由不再创建`BackgroundTasks`；处理器分别复用原PDF和RAG索引服务。
- 四类论文任务共享活动任务互斥；完成后仍保留用户显式重新解析或索引能力，并复用现有缓存。
- 新增6项B1.3测试，队列专项累计`18 passed`，完整离线回归`175 passed`，外部PDF、Seed和DeepSeek均未调用。
- 结论：论文下载和知识库准备相关入口已全部进入持久Worker；模型问答和研究任务仍未迁移。
- 下一步：B1.4迁移单篇RAG问答，优先解决`rag_queries.answering`记录与任务入队的事务一致性。

### 2026-08-17：完成B1.4单篇RAG问答持久任务

- 继续更新当天`v0.13.0`。
- `rag_queries`记录与`answer_rag_query`任务改为同一事务创建；相同论文、问题和重排模式的活动重复提交复用原任务。
- Worker复用原RAG服务并根据真实终态判断成功；失败有限重试，租约最终过期同步`answer_failed`。
- 已完成回答具备重放幂等保护，避免Worker恢复时再次调用DeepSeek；旧`answering`孤儿记录可在启动时恢复为明确失败。
- `background_jobs.rag_query_id`提供任务关联并包含旧库迁移测试；默认租约提高到600秒，自动心跳续租尚未实现。
- 新增9项B1.4测试，队列专项累计`27 passed`，完整离线回归`184 passed`，没有调用真实DeepSeek或Seed。
- 结论：单篇论文从下载到RAG回答的用户闭环已进入持久Worker；智能研究搜索和报告仍待迁移。
- 下一步：B1.5优先补Worker自动租约续期，再迁移智能研究长任务。

### 2026-08-17：完成B1.5 Worker自动心跳续租

- 新增数据库原子续租函数，只允许当前Worker在租约到期前续期。
- `run_one_job()`用守护线程维护租约，结束时通过`Event`立即停止，不让心跳线程阻塞Worker退出。
- 默认租约从600秒恢复到90秒，健康任务持续续期，崩溃任务不再固定等待10分钟。
- 新增3项测试，队列专项累计`30 passed`，完整离线回归`187 passed`。
- 结论：Worker执行层已具备持久领取、有限重试、过期恢复和自动续租；下一步迁移智能研究搜索任务。

### 2026-08-17：完成B1.6 智能研究搜索持久任务

- 继续更新当天`v0.13.0`，没有新增第二个版本号。
- 确认路由不再创建`BackgroundTasks`；研究任务状态与`research_search`任务原子提交，重复或并发确认只保留一个活动任务。
- Worker恢复原`thread_id`和LangGraph检查点执行检索；异常重试期间业务任务保持`running`，达到上限才进入`failed`。
- 任务结果已经保存为`awaiting_selection`时，即使Worker在确认队列成功前中断，恢复后也会直接完成任务，不重复访问搜索源。
- 任务详情和状态API增加队列状态、当前尝试次数与最大尝试次数。
- 新增6项离线测试，队列专项累计`36 passed`，完整离线回归`193 passed`，没有调用真实DeepSeek、arXiv或Crossref。
- 结论：用户确认后的智能研究搜索已支持跨Web重启持久化和Worker恢复；B1.7继续迁移研究报告。

### 2026-08-18：完成B1.7 研究报告持久任务

- 发布版本升级为`v0.13.1`。
- 报告创建或重新生成与`research_report`任务在同一事务中提交，重复及并发请求复用活动任务。
- Worker继续使用原LangGraph `thread_id`和报告检查点；准备、检索、上下文门禁、模型生成与引用检查职责不变。
- 失败按队列策略重试，租约最终过期同步报告失败；报告内容已完成落库时恢复任务不会再次调用模型。
- 页面和状态API展示报告队列状态及尝试次数；Web/Worker初始化不会破坏活动报告任务。
- 新增6项离线测试，队列专项累计`42 passed`，完整离线回归`199 passed`，没有调用真实DeepSeek、Seed或外部搜索源。
- 结论：B1可靠Worker阶段完成；后续应先确定部署、生产数据库或可观测性增强中的下一条主线。

### 2026-08-23：完成B2.1统一研究工作台骨架

- 发布版本升级为`v0.14.0`，首页从双模式切换改为单一研究任务入口。
- 计划确认前的补充消息会重新生成计划并更新原LangGraph检查点，不创建平行任务。
- 论文选择后新增独立报告问题；只有用户明确报告目标后才允许生成报告。
- 报告完成后新增持久化证据追问，复用冻结论文、RAG召回与Worker可靠性能力。
- 消息全部落入`research_messages`供展示和审计，但当前模型不读取完整对话历史。
- 新增6项流程与轮询测试并更新1项旧页面契约，完整离线回归`205 passed`。
- 下一步：B2.2先设计可评测的短期上下文窗口与摘要策略，再实现长期记忆。

### 2026-08-24：生产PDF解析迁移到MinerU云端

- 发布版本升级为`v0.15.0`，没有增加新的用户按钮；现有解析和准备问答流程默认调用MinerU精准解析API。
- 在Service层新增独立MinerU适配器，可靠Worker继续负责任务执行、重试、租约与恢复，Web层只入队和轮询状态。
- 解析输出从字符边界块升级为带章节、内容类型、物理页码和bbox的结构化块；RAG层只补充结构上下文，不改召回权重或重排策略。
- 用PDF哈希与模型版本缓存云端结果；远程批次ID写入SQLite，进程中断后可继续轮询，避免重复上传和重复占用额度。
- 生产链路禁止静默回退`pypdf`；历史分块实验通过显式策略继续复现，保证产品一致性和实验可比性。
- 新增4项MinerU专项测试，完整离线测试`209 passed`，没有读取真实Token或上传用户PDF。
- 下一步：用户配置`MINERU_API_TOKEN`后，经明确授权用一篇非敏感PDF做真实端到端验收，再单独评估MinerU结构块对现有检索基准的影响，不覆盖冻结历史基线。

### 2026-08-24：多来源搜索MCP接入第1步

- 当前仍使用`v0.15.0`，本步骤只建立适配边界，尚未替换生产`search_all()`，前端、数据库和LangGraph还没有接入来源选择。
- 新增`paper_search_mcp_service.py`，冻结`openags/paper-search-mcp`统一`search_papers`当前支持的21个来源ID；“全部来源”始终展开为明确ID列表，禁止向第三方发送`sources="all"`。
- 新增`normalize_source_ids()`和`normalize_queries()`，对来源、检索词、重复项和长度执行本地白名单校验；升级前旧任务的回退来源固定为`arxiv/crossref`。
- 新增`search_queries_via_mcp_async()`与`search_queries_with_session()`：一次研究搜索只打开一个stdio MCP Session，在同一Session内顺序执行2至4条Planner query，第一版每来源独立召回预算固定为`3`。
- 新增`paper_candidate_from_mcp()`和`parse_search_papers_result()`，在适配层完成第三方Paper到`PaperCandidate`的字段归一化、来源约束、单条坏记录隔离及单来源错误保留；`pdf_url`只做字符串和绝对URL格式处理，不发起HEAD/GET验证。
- Wrapper只调用`search_papers`，没有调用或暴露第三方下载Tool；默认外部命令固定为`uvx --from paper-search-mcp==0.1.4 paper-search-mcp`，避免运行版本漂移。
- 新增5项完全离线契约测试，覆盖明确来源、拒绝`all`、零网络PDF映射、部分来源失败、单Session复用和坏记录隔离；完整离线回归`214 passed`。
- 下一步：第2步让现有`search_all()`委托批量Wrapper，同时保持任务级`max_results`最终上限和现有跨query去重边界。

### 2026-08-24：多来源搜索MCP接入第2步

- 当前仍使用`v0.15.0`；本步骤只替换生产搜索底座，未接入前端来源选择，也未修改Planner、去重、下载、报告和后续工作流。
- `search_service.search_all()`不再直接请求arXiv与Crossref REST API，而是把单条query交给`paper_search_mcp_service.search_queries_via_mcp()`，由stdio MCP Session调用第三方统一`search_papers`。
- 在来源配置尚未贯通前，生产链路显式传递`LEGACY_SOURCE_IDS = ("arxiv", "crossref")`，保证当前产品可见来源不被本次内部替换意外扩大；每来源召回预算固定为`3`，任务最终结果仍由`search_all(max_results)`截断。
- MCP返回继续在适配层转换为现有`PaperCandidate`，因此`research_tools`、LangGraph候选去重、论文选择、下载与后续处理接口均保持不变。
- 单个来源失败时保留健康候选，并追加当前项目既有的`failed`占位记录；MCP进程、协议或整个调用失败时返回单条`paper_search_mcp failed`记录，避免异常穿透并中断研究任务。
- 旧`search_arxiv()`、`search_crossref()`及其直接HTTP请求已删除，不作为fallback保留；`parse_arxiv_feed()`与`parse_crossref_items()`仅作为纯解析函数和离线测试夹具继续存在，不参与当前生产搜索。
- Wrapper已经支持同一批queries复用一个Session，但现有LangGraph仍按query逐次调用稳定接口`search_all()`；由于本步骤明确不修改Planner和工作流，生产多Query暂时仍是一次query对应一次MCP Session，后续在任务来源贯通时再把调用边界提升到批量搜索阶段。
- 新增和调整离线测试覆盖`search_all()`委托参数、最终数量上限、部分来源失败和整体MCP失败；定向回归`136 passed`，完整离线回归`215 passed`，未启动真实MCP Server或调用外部论文API。
- 下一步：贯通任务级明确`sources`列表，并在不改变Planner输出的前提下让一次研究搜索的2至4条query共享同一MCP Session，随后再实现前端来源多选。

### 2026-08-24：多来源搜索MCP接入第3步

- 当前仍使用`v0.15.0`；本步骤只贯通后端`sources`和任务级批量Session，未增加前端checkbox，也未修改Planner Prompt、候选去重、下载、报告或其他后续工作流。
- `research_tasks`新增`sources_json`；它是任务来源配置的canonical value。新任务创建时保存经过白名单归一化的明确source ID列表，LangGraph State中的`sources`只作为执行副本。
- 数据库迁移为升级前任务补充`["arxiv", "crossref"]`；任务记录来源缺失、空值或不可解析时，读取层也保守回退为`arxiv/crossref`。
- `POST /search`后端已接受可重复的`sources`表单参数；当前页面尚未提供控件，因此普通网页创建任务仍使用旧默认来源。无来源、`all`或未知来源继续由现有白名单规则拒绝或回退。
- `start_checkpointed_research()`把明确来源写入初始State；Worker每次恢复前调用`update_checkpoint_sources()`，用业务库canonical值校准Checkpoint副本，再执行`Command(resume=...)`，不会重新启动Planner。
- 新增`search_service.search_many()`和`research_tools.search_literature_batch()`；`multi_query_search`与`single_query_search`都使用同一批量入口。一次任务将2至4条Planner queries整体交给现有`search_queries_via_mcp()`，只建立一个stdio Session，再在Session内逐条调用`search_papers`。
- 每条query仍保存独立的`search_literature` Tool Trace，输入中新增明确`sources`；每来源候选预算仍固定为`3`，每query上限和任务最终上限继续由Paper Hunter控制。
- MCP返回的单来源错误保留为该query的`partial` Tool结果，健康候选继续参与现有去重；MCP进程或协议整体失败时转换为每条query的`failed` Tool结果，不让异常越过现有搜索错误边界。
- `search_all(query, max_results)`继续兼容旧单query调用，并默认使用`arxiv/crossref`；MCP下载Tool仍未调用或暴露，`PaperCandidate`未改变。
- 新增并调整来源持久化、旧任务兼容、批量委托、部分/整体失败、Checkpoint恢复和Planner不重放测试；相关回归`192 passed`，完整离线回归`221 passed`，最终测试未启动真实MCP Server或访问外部论文API。
- 下一步：第4步只实现前端来源多选、全选/清空与0来源提交拦截，复用本步骤已完成的后端链路。

### 2026-08-24：多来源搜索MCP接入第4步

- 当前仍使用`v0.15.0`；本步骤只增加首页来源选择和提交校验，没有修改搜索服务、Planner Prompt、候选去重、下载、报告或其他工作流节点。
- `GET /`把后端唯一白名单派生的`SEARCH_SOURCE_OPTIONS`和`DEFAULT_SOURCE_IDS`传给Jinja；模板循环渲染复选框，HTML和JavaScript都不维护第二套source ID列表。
- 新任务默认明确勾选全部21个来源，支持全选、清空和实时已选数量；清空后前端显示错误并禁用提交按钮。
- `POST /search`对可重复的`sources`字段再次执行`normalize_source_ids()`白名单校验；0个来源、`all`和未知ID不能进入Planner，避免绕过浏览器校验。
- 有效来源继续沿既有链路写入初始Checkpoint和`research_tasks.sources_json`；业务表仍是canonical source，Worker恢复前用它校准State副本。旧任务仍回退`["arxiv", "crossref"]`。
- 浏览器验收确认默认`21/21`、清空后`0/21`且不可提交、全选后恢复；新增页面契约、零来源拒绝和选定来源传递测试，完整离线回归`224 passed`。
- 多来源搜索MCP接入的四个最小步骤已完成；这些改动尚未在本次任务中提交或推送。

### 2026-08-24：第5步真实联网验收与第5.1步返回结构兼容

- 首轮真实任务`#10/#11/#12`均完成DeepSeek Planner、人工确认、Worker、搜索节点和去重节点，但没有候选进入Paper Selection。根因是`paper-search-mcp==0.1.4`把业务对象放在`structuredContent.result`，旧Adapter错误地从`structuredContent.papers`读取。
- 第5.1步只修改Adapter响应边界：兼容真实`result` envelope、旧直接payload和文本JSON回退；严格校验`papers/sources_used/source_results/errors/raw_total/total`；嵌套`ExceptionGroup`现在保留叶子根因。Planner、sources、Mapper、去重、下载、报告、工作流和Reranker均未修改。
- `McpSearchBatch`保存`sources_used/source_results/raw_total/total`，现有Tool Trace在Checkpoint中携带这些验收元数据；没有修改业务数据库表或候选排序。
- 新增真实0.1.4形状、文本回退、畸形结构、异常根因和Trace元数据契约测试；完整离线回归`233 passed`。
- 保留失败任务`#10/#11/#12`作为前后对照，使用相同问题和来源配置创建复验任务`#13/#14/#15`。三项任务均只执行一次Planner，每个任务只建立一个MCP Session并连续完成4次`search_papers`调用。
- `#13`全21来源：4条query进入Paper Hunter的候选数均被每query预算截为5，合并前20、现有去重后5，任务进入`awaiting_selection`；第1/4条query记录Zenodo日期类型错误，因此搜索节点为`partial`。最终5篇均明显偏离土壤肥力主题，说明第三方多源结果顺序不能直接作为最终排序。
- `#14`仅`semantic/crossref/openalex`：4条query的`source_results`均为`semantic=0/crossref=3/openalex=3`，每条`raw_total=total=6`；合并前20、去重后18，进入Paper Selection。Semantic Scholar真实日志持续429，但第三方payload没有写入`errors`。
- `#15`仅`arxiv/semantic/crossref/openalex`：4条query的`raw_total/total`分别为`8/8、9/9、7/7、7/7`，合并前20、去重后19，进入Paper Selection；存在人类记忆论文和宽泛LLM综述等明显噪声。
- 三项复验任务的后台队列均只有`succeeded research_search`，未创建下载、解析、索引或报告任务，也未调用第三方MCP下载Tool。
- 第5.1步兼容修复通过。下一步必须单独决定来源健康与候选质量治理；不得把Semantic 429、SSRN 403、OpenAIRE/CiteSeerX、Zenodo错误或Reranker混入本步骤结论。

### 2026-08-25：P2 Rerank Prompt冻结回归

- 本步骤只测试当前`RERANK_SYSTEM_PROMPT`，没有修改Prompt、生产重排代码、RAG、LangGraph或模型配置。
- 新增`benchmarks/rerank_p2_cases.json`，冻结15个案例：事实、方法、参数、结果、原因各2例，完整回答对部分回答1例，关键词/长文本陷阱2例，Candidate Prompt Injection 2例。
- 新增`scripts/evaluate_rerank_p2.py`，复用生产`_rerank_prompt()`和`ask_deepseek_json()`，按当前`deepseek-v4-pro`、`temperature=0`、thinking关闭和JSON Object配置逐例独立调用P2；不执行搜索、Embedding、RAG或Agent节点。
- 输出契约15/15通过：所有响应均为合法JSON且只有`ranking`字段，候选ID完整、唯一、无越界，没有回答Question或输出Markdown；Top1为15/15正确，关键词陷阱2/2和注入2/2通过。
- 严格总体结果为14 PASS、1 FAIL，通过率`93.33%`。唯一失败是结果型`result_01_model_comparison`：期望关键次序`1 > 2 > 3`，实际`[1, 3, 2]`；直接包含比较数值和结论的Candidate 1仍正确排第一，失败仅来自预先冻结的次级关键关系`2 > 3`。
- 当前不建议立即修改P2：它已稳定识别直接答案、完整答案、原因证据和注入文本；应先保留该失败作为回归样本，后续只有在更多真实证据案例重复出现同类次级排序问题时再调整Prompt。
- 原始运行结果保存于Git忽略的`outputs/rerank_p2_results.json`；后续P2改动必须复用同一冻结集，不得重新生成更容易的案例。

### 2026-08-25：P3 RAG Prompt冻结回归

- 本步骤只测试当前`RAG_SYSTEM_PROMPT`，没有修改Prompt、生产RAG代码、LangGraph、下载或向量检索。`benchmarks/rag_p3_cases.json`冻结15例，覆盖事实、方法、参数、结果、原因、部分证据、完全不足、相邻信息诱导、多页证据和两种Evidence Injection。
- 独立脚本`scripts/evaluate_rag_p3.py`复用生产`build_rag_prompt()`和`ask_deepseek()`；当前真实P3配置为`deepseek-v4-pro`、thinking开启、temperature未显式设置而沿用服务端默认。不得把P2的`temperature=0/thinking=disabled`误写成P3配置。
- 自动检查回答非空、论文标题、证据来源、输入/预期页码、必要内容、禁止陈述、部分回答和“当前证据不足”；原始回答保存在Git忽略的`outputs/rag_p3_results.json`，`--recheck-existing`可在不重复调用模型时重跑确定性检查。
- 人工最终结果为`14 PASS / 1 WARN / 0 FAIL`，Strict Pass Rate=`93.33%`；Unsupported Claim Cases=`0`，Citation Error Cases=`1`，证据不足处理=`4/4`，Prompt Injection=`2/2`。
- 唯一WARN为`insufficient_01_dropout`：模型正确说明当前证据不足、没有猜测dropout值，并指出需要训练/正则化参数；正文提到第5页，但末尾“证据来源”为空且缺论文标题，属于无事实错误的引用结构问题。
- 建议后续对P3做一次很小的不足分支约束：即使结论为证据不足，只要使用了片段判断不足，末尾仍必须列出论文标题和页码；修改前不重写其他规则，修改后复用同一15例回归。本轮按要求不自动修改Prompt。

### 2026-08-25：P3证据不足来源规则回归

- 用户在现有P3中只增加一条规则：即使结论为“当前证据不足”或无法确定，只要依据某个片段判断其缺少所需信息，该片段仍必须列入末尾“证据来源”。本次没有继续修改Prompt或生产代码。
- 使用完全相同的`benchmarks/rag_p3_cases.json`和真实生产P3配置重新调用15例，新输出另存为`outputs/rag_p3_results_after_source_rule.json`，未覆盖首次基线。
- Case 12由WARN修复为PASS：回答继续拒绝猜测dropout rate，并在证据来源中明确列出`Neural Architecture Description，第5页`。
- 原14个PASS全部保持PASS，回归`0/14`；人工复核没有发现新增外部知识、unsupported claim、数值变更、错误页码或Evidence Injection执行。
- 新结果为`15 PASS / 0 WARN / 0 FAIL`，Strict Pass Rate=`100%`、Unsupported Claim Cases=`0`、Citation Error Cases=`0`、证据不足处理=`4/4`、Prompt Injection=`2/2`。当前新增规则可以保留。

### 2026-08-25：P4跨论文研究报告Prompt冻结回归

- 本步骤只测试当前`RESEARCH_REPORT_SYSTEM_PROMPT`，没有修改Prompt、生产报告代码、LangGraph、搜索、下载或RAG。新增`benchmarks/research_report_p4_cases.json`与独立脚本`scripts/evaluate_research_report_p4.py`，固定15个真实`research-context-json-v1`形状案例。
- 覆盖共同研究问题、共同方法、方法横向比较、数据来源、单篇结论泛化、多篇一致结论、冲突结论、不可比/可比数值、禁止重新计算、部分证据、访问受限、跨论文污染及两种JSON Injection。
- P4真实配置为`deepseek-v4-pro`、thinking开启、temperature和max_tokens均未显式设置而使用服务端默认；不得套用P2配置。原始报告保存在Git忽略的`outputs/research_report_p4_results.json`。
- 自动检查固定四部分、必要/禁止内容、合法URL和`[标题, p.页码]`引用契约；人工复核每份报告的事实归属、泛化、冲突解释、数值比较和Injection。最终结果为`10 PASS / 5 WARN / 0 FAIL`，Strict Pass Rate=`66.67%`。
- 5个WARN为`compare_01_method_routes`、`numbers_01_incomparable_datasets`、`numbers_02_comparable_protocol`、`numbers_03_no_recalculation`和`injection_02_fake_system_format`。它们的事实、标题和页码均正确，但综合段落把多个来源合写为`[A, p.x; B, p.y]`，违反每条引用独立使用`[标题, p.页码]`的格式；没有跨论文错配，因此不判FAIL。
- 指标：Unsupported Claim Cases=`0`、Cross-paper Attribution Error=`0`、Unsupported Generalization=`0`、Invalid Numerical Comparison=`0`、Citation Error Cases=`5`（仅格式契约）、Conflict Handling=`1/1`、Partial Evidence Handling=`1/1`、Prompt Injection=`2/2`。
- 建议只对P4引用规则做小幅强化并给出多来源正确示例：多个来源必须写成`[A, p.x][B, p.y]`，不得放进同一对方括号。不要重写已通过的证据边界、数值比较、访问限制或Injection规则；修改后必须复用同一15例回归。本轮按要求不自动修改Prompt。

### 2026-08-25：P4中文纯文本与引用规则回归

- 用户修改生产`RESEARCH_REPORT_SYSTEM_PROMPT`后，继续复用完全相同的`benchmarks/research_report_p4_cases.json`，没有修改Prompt、生产报告逻辑或冻结案例。
- `scripts/evaluate_research_report_p4.py`只更新输出契约检查：移除固定四部分Markdown要求，改为校验中文单篇引用`[论文标题，第3页]`和多篇引用`[论文A，第3页；论文B，第5页]`，并拦截Markdown标题、表格、代码块及无内容的`【...】`章节。
- 使用当前真实生产配置`deepseek-v4-pro`、thinking开启、temperature和max_tokens沿用服务端默认重新调用15例；新原始输出另存为Git忽略的`outputs/research_report_p4_results_plaintext.json`，未覆盖旧基线。
- 自动契约检查与人工语义复核均为`15 PASS / 0 WARN / 0 FAIL`，Strict Pass Rate=`100%`。旧版5个Citation WARN全部消失，原10个PASS全部保持PASS。
- 指标：Unsupported Claim Cases=`0`、Cross-paper Attribution Error=`0`、Unsupported Generalization=`0`、Invalid Numerical Comparison=`0`、Citation Error Cases=`0`、Conflict Handling=`1/1`、Partial Evidence Handling=`1/1`、Prompt Injection=`2/2`。
- 15份报告均未出现`# / ## / ###`标题、Markdown表格、代码块、空章节或机械补齐的固定四部分；多篇共同引用经人工核对，每个引用项均真实支持其相邻事实。
- 结论：取消固定四段结构没有造成内容能力回归，当前P4 Prompt可以正式保留；后续修改必须继续复用同一冻结集，避免只优化格式却破坏证据边界。

### 2026-08-25：v0.16.0报告后Realtime Chat v1

- 范围严格限制为报告完成后的Follow-up Chat；Planner、搜索、论文选择、下载、解析、检索核心、上下文打包、引用校验、LangGraph前半段和Checkpoint均未修改。
- 新增`stream_deepseek()`真实消费DeepSeek的`stream=true`响应；`prepare_research_followup_context()`从旧回答函数中提取并复用原证据准备逻辑，没有改变Evidence Context策略。
- 新增`POST /research-tasks/{task_id}/chat`，先通过数据库短事务创建User与pending Assistant消息，再以`status/delta/done/error`事件持续响应。成功后保存完整回答、证据和模型，失败或断开保存明确失败状态。
- 新增`research_followup_chat.js`，采用`fetch POST + ReadableStream`更新同一个助手气泡；不刷新页面，完成后立即恢复输入，手动刷新由SQLite历史恢复消息。
- 普通追问不会修改已有报告。旧`research_followup`持久Worker链路继续作为无JavaScript回退，本版没有引入WebSocket、摘要、近期消息注入或长期记忆。
- 完整离线回归`237 passed`，Python编译与JavaScript语法检查通过。真实浏览器在任务`#9`连续发送两次追问，观察到`retrieving -> generating -> streaming -> completed`、内容多次增长、URL不变、完成后可继续发送、刷新前后消息数一致且报告内容不变。
- 后续独立问题：DeepSeek开启深度思考时，首个正文delta前可能停留在`generating`数秒至数十秒；这是真实模型推理等待，不是假流式。本版不改变模型配置或展示隐藏思考内容。

### 2026-08-26：v0.17.0 Recent Messages v1

- 固定窗口定义为当前研究任务最近10条`phase=followup/status=completed/role=user或assistant`消息，先截取最新记录再按`created_at/id`升序交给模型。
- `create_research_followup_messages()`原User与pending Assistant同事务设计保持不变；实时路由显式传递两个消息ID，DB窗口查询排除本轮，当前问题只在`followup_question`出现一次。
- Worker回退payload同步记录`user_message_id`，与实时路径复用同一Service规则；pending、retrieving、generating、streaming、retry_wait和failed Assistant均不进入历史窗口。
- `conversation_history`进入原有不可信JSON，不进入System Prompt；历史只帮助指代消解，不作为论文证据。
- Evidence Retrieval、`pack_research_context()`、Citation Validation、Report、`stream=true`和前端ReadableStream均未修改。
- 新增5项定向测试，连同原实时流式、持久化和报告不变测试全部通过。

### 2026-08-26：v0.17.0 Conversation Summary v1

- 保持最近10条有效消息原文窗口，将刚退出窗口且尚未覆盖的旧消息与旧摘要交给DeepSeek非流式滚动合并；不重复处理整个历史。
- `research_tasks`持久化摘要、覆盖游标、错误和更新时间，原始`research_messages`不删除；重启后从原游标继续。
- 摘要更新使用预期旧游标的CAS写入，避免并发覆盖；模型返回空白或调用失败时保留旧摘要、记录错误并继续Follow-up Streaming。
- Follow-up Context升级为不可信`research-followup-context-v3`，摘要和近期消息只提供对话语境，事实仍必须由报告或本轮Evidence支持。
- 新增9项摘要专项测试，原Streaming与Report不变契约继续通过；完整离线回归`251 passed`。
- 下一阶段可设计Active Constraints，把语言、范围、输出形式等硬约束从非权威摘要中独立提取；当前没有跨任务长期记忆。

### 2026-08-26：v0.17.0 Task Memory v1

- 新建任务级Memory表和四个粗粒度Category；完整Raw Messages继续保留，Memory只表示当前active状态，取消项标记removed。
- Memory Extractor仅接收当前active Memory、最近4条有效消息和当前User Message，不接收Report、Evidence、Summary或完整历史；DeepSeek固定JSON、temperature 0、thinking disabled。
- LLM只提出ADD/UPDATE/REMOVE，Validator与DB事务负责字段、长度、事实过滤、任务归属、active状态和幂等；普通问题与一次性要求快速NOOP。
- 当前消息先更新Memory再构造`research-followup-context-v4`，语言和格式优先级为System > 当前明确要求 > Task Memory > Summary/Recent。
- Memory失败不阻断Streaming，不改变Evidence、Citation或Report；17个新增测试实例使完整回归达到`268 passed`。
- 真实浏览器验收发现并修复旧System Prompt强制中文冲突，最终验证英文持久偏好、中文UPDATE、一次性英文覆盖及下一轮恢复中文全部通过。
- 收紧论文事实Validator：删除命中论文事实后基于宽泛任务词的豁免，定向离线边界测试`4 passed`；12次真实Extractor调用中论文事实、合法focus和合法decision各`3/3`，Recent来源越权仍有`2/3`错误原始输出，生产前置过滤可确定性跳过该普通问题。
- 混合来源封版测试`0/3`：当Current含合法持久focus而必须调用Extractor时，模型三次都把Recent英文要求与当前focus一起输出，Validator也全部接受；这不是前置过滤可覆盖的场景，Task Memory v1暂不封版。
- 当前明确不包含跨任务User Memory、向量记忆、TTL、Semantic Dedup、Memory UI或Report Update。

### 2026-08-26：v0.17.0 Unified Follow-up ContextBuilder v1

- 新增`followup_context.py`纯装配层，统一接收上游已经准备好的Memory、Summary、Recent、Current Question、Report、Evidence和Access Limitations，输出稳定v4不可信JSON。
- 原Prompt入口保留兼容签名；Builder不接收task ID，不查库、不Retrieval、不调用DeepSeek，不更新四层Memory、Report或Citation状态。
- 当前问题在结构中只出现一次，pending/failed Assistant被防御性过滤；各来源继续独立，事实优先级仍为Evidence > Existing Report > Conversation Context。
- 新增15项专项测试并全部通过；完整离线回归`287 passed`。本阶段不实现Dynamic Token Budget、Context Ranking、Vector Memory或其他优化。

### 2026-08-27：v0.17.1 Context / Memory Eval v1

- 冻结29个场景并用当前`deepseek-v4-pro`、temperature 0完成56次真实调用；原始输入、最终Context、模型输出和判定保存到E盘`outputs/`。
- 严格通过`26/29`；8场景With Context=`8/8`、Without Context=`2/8`，目标能力提高75个百分点。
- Recent指代、Summary、Memory持续偏好、临时覆盖、Evidence隔离、Task隔离、重启恢复和Prompt Injection均达到本轮100%目标能力。
- Task Memory混合输入Recent来源隔离连续`0/3`，Validator同样接受错误操作；英文引用契约为`30/33`。
- 结论：ContextBuilder与四层上下文组合无需扩大重构；下一步只应先做Task Memory Current来源可验证的最小修复，再复用同一场景回归。Token Budget、向量记忆和跨任务Memory继续暂停。

### 2026-08-27：SQLite → MySQL Phase 1/2基础层

- Phase 1引入PyMySQL、`BUSINESS_DB_BACKEND=sqlite/mysql`及独立Business Database连接配置；MySQL连接固定`utf8mb4`、UTC Session、InnoDB默认引擎、`autocommit=False`和`DictCursor`，新增`read_connection()`与显式`transaction()`，默认SQLite行为不变。
- LangGraph `SqliteSaver`、`LANGGRAPH_CHECKPOINT_PATH`和`research_checkpoint.py`保持独立SQLite，未纳入Business Database迁移。
- Phase 2新增`migrations/mysql/001_initial_schema.sql`，按真实FK依赖顺序覆盖20张业务表；全部表使用InnoDB和utf8mb4，运行时间字段使用UTC `DATETIME(6)`，ID/FK使用`BIGINT UNSIGNED`，指标浮点使用`DOUBLE`。
- 哈希字段使用`CHAR(64)`；cache namespace、checkpoint thread和dedupe key使用受控`VARCHAR`；长问题、报告、消息、分块和现有JSON字符串保持`LONGTEXT`。`evaluation_cases`使用生成的SHA-256问题哈希维持长问题唯一约束。
- `background_jobs`使用只在`queued/running/retry_wait`时返回dedupe key的stored generated column，并对该列建立UNIQUE，等价保留SQLite partial unique index的active job约束。
- 新增独立migration runner、`schema_migrations`版本/checksum记录和手动CLI；FastAPI与Worker启动不会自动执行MySQL DDL，重复执行已应用版本会安全跳过，已应用文件被修改会拒绝。
- MySQL真实集成测试使用随机临时数据库并在结束后删除，通过`PAPER_HUNTER_MYSQL_TEST=1`显式启用；未配置MySQL Server时离线测试自动跳过。
- 本阶段未修改现有SQLite业务SQL、队列claim/lease/retry/recover、Task Memory事务、Conversation Summary CAS或任何业务服务。下一步Phase 3只做DB-API SQL参数、UPSERT和row/cursor兼容，不提前进入队列并发语义迁移。

### 2026-08-27：SQLite → MySQL Phase 3 Business SQL Compatibility

- `db.py`新增最小MySQL Connection/Cursor Adapter，集中处理qmark `?` 到PyMySQL `%s`的安全转换、dynamic IN占位符、`execute/executemany/fetchone/fetchall/rowcount/lastrowid`以及Dict row兼容；参数值继续独立传入driver。
- 普通CRUD已覆盖papers、paper documents/chunks、research tasks/tool calls/reports/messages、traces、RAG、PDF/embedding/LLM caches和evaluation/generation evaluation。MySQL `DATETIME(6)`输入统一为UTC datetime，返回层保持既有ISO字符串API。
- 审计11处SQLite `ON CONFLICT`，迁移9处普通UPSERT为MySQL 8.0 row-alias `ON DUPLICATE KEY UPDATE`；剩2处位于Report/Job并发锁流程，明确留Phase 4。审计3处`INSERT OR IGNORE`，普通document get-or-create改为仅忽略MySQL 1062 duplicate-key，FK/NOT NULL/invalid data仍抛出；其余2处enqueue流程留Phase 4。
- 原16处`BEGIN IMMEDIATE`中，`revise_research_task_plan()`与`set_research_report_question()`只需普通原子性，已改用显式`transaction()`；余下14处依赖SQLite writer lock，MySQL路径以`PHASE_4_BLOCKED`失败关闭，未引入`FOR UPDATE`、`SKIP LOCKED`或deadlock retry。
- Conversation Summary CAS保持原单条conditional UPDATE算法；MySQL连接使用`CLIENT.FOUND_ROWS`保持SQLite matched-row rowcount语义。Task Memory的读-判断-写串行事务未迁移。
- 真实MySQL 8集成测试使用随机`paper_hunter_phase1/2/3_*`临时数据库并在结束后删除，结果`38 passed, 0 skipped`；数据库密集型SQLite回归`277 passed, 3 skipped`，完整canonical pytest `664 passed, 6 skipped`，`git diff --check`通过。
- LangGraph `SqliteSaver`、`research_checkpoint.py`、`LANGGRAPH_CHECKPOINT_PATH`保持不变；未迁移`downloads/metadata.db`，未修改正式`paper_hunter`数据库，未进入Phase 4。

### 2026-08-27：SQLite → MySQL Phase 4A Concurrent Worker Claim

- 本步仅迁移`claim_next_background_job()`。SQLite路径保留原`BEGIN IMMEDIATE → candidate SELECT → conditional UPDATE → commit`逻辑，候选条件仍为`queued/retry_wait`、`available_at <= now`、可选`job_types`，排序仍为`priority DESC, id ASC`。
- MySQL路径在一个短`READ COMMITTED`事务内执行相同candidate query，并在`LIMIT 1`后使用`FOR UPDATE SKIP LOCKED`；随后保留原status/available CAS，写入`running`、`worker_id`、`lease_expires_at`、`attempt_count + 1`、首次`started_at`和`updated_at`，在commit后才返回完整Job。真正Job执行期间不持有行锁。
- 首次8 Worker/100 Job真实压测在MySQL默认`REPEATABLE READ`下暴露1213 deadlock；修复为仅Claim短事务使用`READ COMMITTED`以减少ready-range gap lock，未加deadlock retry，未改其他事务默认隔离级别。
- 真实MySQL并发验证：2 Worker/1 Job只有1个成功Claim；8 Worker/100 Job全部Claim且duplicate claim=`0`；锁定最高优先级Job时另一Worker在2秒门限内跳过并领取下一Job；available/order/status/type过滤、lease/ownership/attempt与UPDATE后异常rollback均通过。
- Worker Claim + Task Queue SQLite回归`63 passed, 7 skipped`；Phase 1–4A真实MySQL DB套件`46 passed`；完整canonical pytest `666 passed, 20 skipped`。
- 本步未迁移renew lease、complete、fail/retry、expired recovery、enqueue active dedupe、Report/Follow-up enqueue、Task Memory或`claim_download`；未修改LangGraph Checkpoint、正式SQLite数据或正式`paper_hunter`数据库，未进入Phase 4B。

### 2026-08-27：SQLite → MySQL Phase 4B-1 Lease Renew + Job Complete

- 本步仅迁移`renew_background_job_lease()`与`complete_background_job()`；两者在MySQL均使用短单条conditional UPDATE/CAS，未使用`FOR UPDATE`、`SKIP LOCKED`、deadlock retry或全局isolation修改。
- Renew保留`id + running + worker_id + lease IS NOT NULL + lease > now`所有权条件，成功以`rowcount == 1`返回；新lease和`updated_at`使用CASE取单调最大值，防止同一Worker并发heartbeat因请求乱序使lease倒退。已过期lease不允许重新续活。
- 审计发现旧Complete只检查`id + running + worker_id`，没有验证lease是否有效，会允许已过期但尚未recovery的stale worker完成Job。按heartbeat/recovery的既有ownership定义，SQLite/MySQL Complete CAS均收紧为`id + running + worker_id + lease IS NOT NULL + lease > now`。
- Complete成功仍写`status='succeeded'`，清空`lease_expires_at`和`last_error`，写`completed_at/updated_at`，保留`worker_id`和attempt等其他字段；wrong worker、expired lease、changed owner和重复complete均返回False。
- Heartbeat架构保持原独立daemon thread + stop/join设计；Renew/Complete竞态依赖单行UPDATE串行化，最终稳定为`succeeded + lease NULL`，Complete后的Renew会因status失败。
- Renew/Complete + Task Queue SQLite回归`64 passed, 17 skipped`；Phase 4A + 4B-1真实MySQL专项`19 passed`；Phase 1–4B-1真实MySQL DB回归`55 passed`；完整canonical pytest `664 passed, 40 skipped`。
- 本步未迁移worker fail/retry、expired lease recovery、enqueue active dedupe、Report/Follow-up enqueue、Task Memory或`claim_download`；未改Phase 4A Claim算法、LangGraph Checkpoint、正式SQLite数据或正式`paper_hunter`数据库，未进入Phase 4B-2。

### 2026-08-27：SQLite → MySQL Phase 4B-2 Worker Fail / Retry + Expired Lease Recovery

- 本步仅迁移`fail_background_job()`与`recover_expired_background_jobs()`。Fail使用单条conditional UPDATE/CAS按数据库中的`attempt_count < max_attempts`原子选择`retry_wait/failed`，所有权条件统一为`running + worker_id + lease IS NOT NULL + lease > now`；状态写入后只在同一短事务内读取结果，不使用`FOR UPDATE`。
- Fail继续由调用方传入既有指数backoff结果并写`available_at = now + retry_delay_seconds`，保存截断后的`last_error`、清空`worker_id/lease`，仅终态写`completed_at`；Fail和Recovery均不增加`attempt_count`，一次实际执行仍只在Claim时计数一次。wrong worker和expired owner的Fail返回`None`且不改当前状态。
- MySQL Recovery在短`READ COMMITTED`事务中以`running + lease IS NOT NULL + lease <= recovery_now`选择全部过期Job，并使用`FOR UPDATE SKIP LOCKED`让多个recovery executor跳过彼此已锁行；每条UPDATE再次保留相同expired CAS，防止已续租或已完成的Job被旧选择结果覆盖。
- Recovery保持原SQLite状态机：尚可尝试的过期Job回到`queued`并立即可用，达到max attempts时进入`failed`；两者都清空ownership/lease、记录固定lease-expired错误，仅failed写`completed_at`。SQLite仍保留`BEGIN IMMEDIATE`批量保护，并仅补齐统一ownership与UPDATE expired CAS。
- 真实MySQL竞态验证覆盖heartbeat/recovery、complete/recovery、fail/recovery和两个recovery executor竞争同一Job；每组只出现一个合法转换，重复recovery为0，未出现1213 deadlock，也未加入deadlock retry。
- SQLite Fail/Recovery + Task Queue专项`65 passed, 32 skipped`；Phase 1–4B-2真实MySQL DB回归`71 passed`；数据库密集型SQLite回归`278 passed, 35 skipped`；完整canonical pytest `666 passed, 70 skipped`。
- 本步未修改enqueue active dedupe、research/report/follow-up enqueue、follow-up message原子创建、Task Memory事务、`claim_download`、Phase 4A Claim、Phase 4B-1 Renew/Complete、全局隔离级别、LangGraph Checkpoint、RAG、Streaming、ContextBuilder、Citation、MinerU、Prompt、正式SQLite数据或正式`paper_hunter`数据库，未进入Phase 4C。

### 2026-08-28：SQLite → MySQL Phase 4C Enqueue Transaction + Active Dedupe

- 本步迁移8个Background Job入口：research search/report/follow-up、download/prepare/parse/index和RAG answer；同时迁移实时流式路径的`create_research_followup_messages()`短事务，但不改变真实Streaming协议或创建Worker Job。
- SQLite继续使用原`BEGIN IMMEDIATE`串行化。MySQL enqueue仅使用局部`READ COMMITTED`短事务，并用对应`research_tasks`或`papers`行的`SELECT ... FOR UPDATE`统一保护跨表状态读取、业务状态修改、消息/RAG记录创建和Job插入；Search仍保留`awaiting_confirmation`条件UPDATE/CAS，未锁Background Jobs表。
- Phase 2已有generated `active_dedupe_key`与`UNIQUE idx_background_jobs_active_dedupe`作为并发重复入队最终防线。正常路径仍先查active Job；MySQL 1062只有在错误明确命中该索引，且原事务回滚后可重新读到同dedupe活动Job时，才转换为当前API既有的复用、`None`或业务RuntimeError结果。
- 没有使用MySQL `INSERT IGNORE`，也没有吞掉其他IntegrityError。通用duplicate helper同步收紧为必须匹配指定unique key；FK、NOT NULL、DataError及unrelated 1062继续抛出并触发整笔事务回滚。
- Report的SQLite `ON CONFLICT(task_id)`增加等价MySQL `ON DUPLICATE KEY UPDATE`；Prepare/Parse的SQLite document `INSERT OR IGNORE`在MySQL只精确忽略`paper_documents.PRIMARY`重复，不忽略其他约束错误。
- Follow-up实时消息创建与fallback enqueue锁同一Task行；fallback新增现有active Assistant状态检查。两个入口竞争时只有一个创建User/pending Assistant，Worker Job若存在则payload关联同一对消息，不产生orphan pending或无消息Job。
- 真实MySQL覆盖全部8种Job的2线程同key竞争、8线程Research Search竞争、实时/fallback Follow-up竞争、Report与Paper的succeeded/failed历史复入队、真实active-dedupe 1062 loser回读，以及Search/Follow-up/Paper非duplicate异常回滚；active Job始终为1，未出现1213 deadlock，未增加deadlock retry。
- Phase 4C真实MySQL专项`17 passed`；Phase 1–4C真实MySQL DB回归`89 passed`；SQLite enqueue/task queue专项`74 passed, 18 skipped`；数据库密集型SQLite回归`279 passed, 52 skipped`；完整canonical pytest `668 passed, 104 skipped`。
- 本步未修改Worker Claim、Lease Renew、Complete、Fail/Retry、Expired Recovery、Task Memory事务、`claim_download`、全局隔离级别、LangGraph Checkpoint/SqliteSaver、RAG算法、Streaming、ContextBuilder、Citation、MinerU、Prompt、正式SQLite数据或正式`paper_hunter`数据库，未进入下一Phase。

### 2026-08-28：SQLite → MySQL Phase 4D-1 Task Memory Atomic Transaction

- 本步仅迁移`apply_research_task_memory_operations()`的数据库事务。审计确认所有生产ADD/UPDATE/REMOVE都经过该入口；`record_research_task_memory_error()`只写父Task错误元数据，不绕过入口修改Memory。
- SQLite继续保留`BEGIN IMMEDIATE`。MySQL使用显式短事务，第一条锁定`research_tasks.id = task_id FOR UPDATE`，随后按原顺序校验source follow-up User消息、读取锁后的最新active Memories、执行ADD查重、UPDATE/merge duplicate或REMOVE soft-delete，最后清空Task Memory错误并更新时间元数据后commit。
- 选择父Task主键行作为同Task mutation mutex：同一Task的批量ADD/UPDATE/REMOVE和metadata写入统一串行；不同Task锁不同父行，可并发执行。未锁整张Memory表，未新增UNIQUE、SKIP LOCKED、全局isolation修改或deadlock retry。
- 原业务语义保持：ADD按active `category + content`查重并返回`noop_duplicate`；UPDATE/REMOVE必须命中当前Task的active memory_id；UPDATE撞到等价active记录时soft-remove目标并返回`merge_duplicate`；REMOVE仍为soft-delete，失效或跨Task目标继续报错。
- DeepSeek Extractor与Validator仍在事务外完成，数据库锁内只应用已经生成并验证的operations。Prompt、category、trigger、paper fact/recent source规则和operation schema均未改变。
- 真实MySQL并发覆盖等价ADD、ADD/UPDATE、UPDATE/REMOVE、跨Task更新拒绝、父行锁阻塞同Task但不阻塞另一Task，以及Memory写入后metadata阶段异常的整体rollback；结果均等价于合法串行顺序，未出现lost update、removed Memory复活或1213 deadlock。
- Task Memory SQLite基线`21 passed`；Phase 4D-1真实MySQL专项`6 passed`；Phase 1–4D-1真实MySQL DB回归`95 passed`；Context/Memory回归`36 passed, 6 skipped`；数据库密集型SQLite回归`279 passed, 58 skipped`；完整canonical pytest `668 passed, 116 skipped`。

### 2026-08-28：SQLite → MySQL Phase 4D-2 claim_download + Phase 4 Concurrency Closeout

- `claim_download()`经真实代码审计确认原本已经是单条条件`UPDATE papers ... WHERE id = ? AND status IN (...)`，通过`rowcount == 1`判断唯一成功者；本轮只移除MySQL `PHASE_4_BLOCKED`入口，保留`downloading`、清空旧error和刷新`updated_at`的既有语义，不增加`SELECT ... FOR UPDATE`。
- 新增真实MySQL下载Claim并发测试：2 Worker/1 Paper与8 Worker/1 Paper均只有1次成功；非法`downloaded/link_only`状态保持不变；首次状态CAS成功后旧状态请求不能再次领取。
- Phase 4全局扫描发现并关闭两个额外生产入口 blocker：`start_research_report()`的MySQL路径改为`READ COMMITTED`短事务、`research_tasks FOR UPDATE`和MySQL UPSERT；Web与Worker启动改用backend-aware初始化，MySQL继续要求通过现有手动migration runner管理DDL，启动时不执行隐式MySQL DDL。
- 生产代码不再包含`PHASE_4_BLOCKED`或`Phase4BlockedError`。现有6处`BEGIN IMMEDIATE`全部只服务SQLite backend；对应MySQL路径分别使用parent row lock、短事务+CAS、`FOR UPDATE SKIP LOCKED`或单条ownership/lease CAS。
- Phase 4最终并发边界：enqueue使用`READ COMMITTED + task/paper FOR UPDATE + CAS + active_dedupe UNIQUE`；Worker Claim与Expired Recovery使用`READ COMMITTED + FOR UPDATE SKIP LOCKED + CAS`；Renew/Complete/Fail使用ownership与有效lease CAS；Task Memory使用`research_tasks FOR UPDATE`；Conversation Summary使用expected message id CAS；Download Claim使用单条status CAS。
- SQLite启动/Report/下载Claim专项`20 passed`；background_jobs SQLite回归`65 passed, 32 skipped`；Task Memory SQLite回归`21 passed, 6 skipped`；数据库密集型回归`253 passed, 64 skipped`；Phase 1–4D-2真实MySQL DB回归`100 passed`；完整canonical pytest `666 passed, 128 skipped`。真实并发运行未出现1213 deadlock，随机临时MySQL数据库全部清理。
- Phase 4 Exit Criteria全部满足；本轮未进入性能优化、正式数据迁移或Phase 5，未修改LangGraph Checkpoint、SqliteSaver、RAG、Streaming、ContextBuilder、Citation、MinerU或Prompt。
- 本步未修改Worker Claim、Renew、Complete、Fail/Retry、Recovery、Enqueue/Active Dedupe、`claim_download`、Conversation Summary CAS、LangGraph Checkpoint/SqliteSaver、RAG、Streaming、ContextBuilder、Citation、MinerU、Prompt、正式SQLite数据或正式`paper_hunter`数据库，未进入下一Phase。

### 2026-08-28：SQLite → MySQL Phase 5 Performance / Index / Production Readiness

- 按生产SQL逐项审计Worker Claim（含/不含job type）、active dedupe、Expired Recovery、Follow-up Recent Messages、Task Memory、papers/documents/chunks、RAG queries、reports、tool calls、traces、caches与evaluation；同时核对001已有主键、唯一键、外键索引和二级索引，未按表机械补索引。
- 新增真实MySQL代表性EXPLAIN测试，随机临时库包含200个Task、300篇Paper/Document、约1万条Message、约1万条Background Job、近1万Chunk及分布式RAG/Tool Call数据；记录`type/key/rows/filtered/Extra`并在测试结束删除临时库。
- 保持`001_initial_schema.sql`不变，新增可校验且幂等的`002_performance_indexes.sql`：Follow-up最近消息复合索引`(task_id, phase, status, created_at DESC, id DESC)`，Background Job按Task/Job Type和Paper/Job Type获取最近状态的两个复合索引。
- Recent Messages由原`idx_research_messages_task_id + filesort`切换到新复合索引且不再filesort；Task最近Job估算行数由50降至7，Paper最近Job由33降至5，均消除额外过滤/反向扫描。MySQL active dedupe查询改为直接命中既有generated unique key，计划由ready range约330行降为`const/1 row`；SQLite查询保持原样。
- Worker Claim继续使用既有`idx_background_jobs_ready`，代表性计划扫描约150行；Recovery仅约30个running候选。候选Recovery索引只把估算行数降到约15且仍filesort，生产Worker的job type过滤又覆盖全部handler类型，因此两者均不足以抵消队列高频写成本，本阶段拒绝新增Claim/Recovery索引。
- 既有chunk、RAG、Task Memory、Report、Tool Call与主键CAS计划均命中当前索引；未给LONGTEXT/JSON/向量正文创建普通索引或FULLTEXT，未删除FK自动索引，也未增加连接池、通用重试或deadlock retry。
- 事务扫描确认连接和事务内没有PDF下载、DeepSeek/Embedding调用、sleep或长轮询；Claim/Recovery短事务继续局部`READ COMMITTED`，其他CAS/parent-row lock继续保持Phase 4既有隔离与锁顺序。PyMySQL继续使用`utf8mb4`、UTC、InnoDB、`autocommit=False`、DictCursor、FOUND_ROWS和现有timeout，无生产阻断项。
- Phase 1–5真实MySQL回归`104 passed`；完整SQLite canonical pytest `672 passed, 130 skipped`，全部skip均为未启用真实MySQL integration gate。未修改正式SQLite数据、正式`paper_hunter`数据库或LangGraph Checkpoint，未执行迁移/Cutover，也未进入Phase 6。

### 2026-08-28：SQLite → MySQL Phase 6A Production Data Migration Rehearsal

- 新增独立`mysql_data_migration.py`和CLI脚本，职责仅为只读SQLite Source到已完成001+002且为空的非正式MySQL Target；目标名精确为`paper_hunter`或包含不安全字符时主动拒绝，不创建正式库、不修改`.env`、不执行Cutover。
- Source使用配置中的`downloads/metadata.db`并通过SQLite URI `mode=ro + query_only`打开；两次真实迁移演练各自前后SHA256均为`4140ac45c63ba8a7fee8346e1c2d57dafe6f807edff96b83accf2b9ff39d1207`，证明迁移工具本身只读。
- 真实Inventory确认20张Business Table均存在；实际非空表为`agent_traces=420`、`pdf_text_cache=327`、`embedding_cache=10591`和`llm_response_cache=540`，其余16表为0。Embedding以原始JSON LONGTEXT保存，单值约35–36KB，不是BLOB。
- Schema drift只有MySQL生成列`background_jobs.active_dedupe_key`、`evaluation_cases.question_hash`，以及SQLite旧`rag_queries`的`draft_answer/verification_*`五列；正式rag_queries当前为空。生成列不参与INSERT；任何没有Target映射但存在非NULL历史值的legacy列会fail-fast，不静默丢弃。
- 迁移按真实FK DAG父表优先，不关闭`FOREIGN_KEY_CHECKS`；以PK升序keyset分页、默认500行一批，全部参数化`executemany`，每批显式commit/rollback，禁止IGNORE、UPSERT、duplicate吞错和失败后继续。
- 时间转换覆盖带offset ISO、UTC Z、SQLite秒精度、微秒精度和NULL，统一写入UTC naive `DATETIME(6)`；失败会报告table/PK/column。严格拒绝空字符串数字、负UNSIGNED、字符串`None`、bool混用、NaN/Infinity和超长受限文本，不静默修复。
- 每表验证source/target count、单列PK MIN/MAX、完整PK集合SHA256；关键业务与缓存表再按迁移后值计算流式内容hash。真实演练20表行数、PK集合及关键内容全部一致，Trace保留420行及历史ID空洞`60–550`。
- Target保持19个InnoDB FK且全部启用、orphan均为0；active dedupe和question hash生成列错误为0，正式Source没有active dedupe冲突或同Paper重复question。全部15张AUTO_INCREMENT表均满足next ID > MAX(id)，临时Paper插入取得新ID后rollback。
- Application-level只读演练将Business DB连接临时指向随机Target，验证papers/tasks/report/messages/memories/jobs/document/chunks、420条Trace、PDF/Embedding/LLM cache和Evaluation读取；未调用外部LLM、PDF下载或Embedding。
- Failure injection在`embedding_cache`第2批INSERT后、commit前抛错：失败批完整rollback，已提交首批保持500行，后续`llm_response_cache`仍为0，工具立即停止且Source hash不变。非空Target重复迁移被拒绝，`validate-only`复验通过。
- Phase 1–6A真实MySQL回归`120 passed`；SQLite数据库密集回归`245 passed, 2 skipped`；完整canonical pytest `702 passed, 132 skipped`，skip均为真实MySQL/迁移环境门控。随机临时MySQL库全部删除，未访问正式`paper_hunter`，未修改LangGraph Checkpoint/SqliteSaver，未进入Phase 6B。
- canonical结束后的最终快照检查发现既有`test_checkpoint_resume_keeps_sources_and_batches_queries_once`缺少Business DB临时路径隔离；由于测试目录Junction被收集两次，它向正式`agent_traces`写入ID `551–562`共12条`checkpoint-source-resume`测试Trace，使Source由420行/哈希`4140ac...1207`变为432行/哈希`d85eec...f00b`。测试已补齐`tmp_path`隔离，单测前后正式哈希保持不变；但按“本轮不得写正式SQLite”约束，尚未自行删除这12条记录，Phase 6A Exit Criteria在用户明确授权恢复前不能标记全部满足。

### 2026-08-28：SQLite → MySQL Phase 6 Fresh MySQL Cutover

- 用户最终选择Fresh Cutover，历史SQLite Business Data明确不迁移。旧`downloads/metadata.db`以原尺寸509,149,184字节归档为`downloads/metadata_before_mysql.db`；Source与Archive SHA256均为`d85eec4f85f1b198817f2aafbe10cda0704f91127c01595ff700e7ca64cff00b`，归档未触碰`downloads/langgraph_checkpoints.db`。
- 正式MySQL `paper_hunter`执行前存在且完全无表，字符集/排序规则为`utf8mb4/utf8mb4_unicode_ci`；使用既有migration runner顺序执行`001_initial_schema.sql`与`002_performance_indexes.sql`。验收确认20张Business Table、`schema_migrations`、19个InnoDB FK、两个stored generated column及全部关键索引存在，初始化后20张业务表均为0行。
- 正式`.env`使用`BUSINESS_DB_BACKEND=mysql`与`BUSINESS_DB_NAME=paper_hunter`，secret只保存在gitignored `.env`；配置缺省backend改为MySQL，`.env.example`同步为MySQL且不包含密码。SQLite backend仅在显式`BUSINESS_DB_BACKEND=sqlite`时可用。
- MySQL启动初始化新增只读fail-fast schema验收：FastAPI和Worker启动均连接正式库并核对001/002版本、checksum和20张表；连接、认证、库缺失、migration不匹配或表集合不匹配都会抛出诊断错误，不执行SQLite DDL，也不存在MySQL到SQLite的自动fallback。专项保护测试确认MySQL连接失败时`sqlite3.connect()`调用次数为0。
- FastAPI正式配置启动成功并返回HTTP 200；Worker `--once`启动成功。真实Cutover E2E创建Task 1，DeepSeek Planner成功生成4条检索式，4次arXiv Tool Call全部成功并写入2篇Paper；选择Paper 2后完成PDF准备、MinerU 26页解析、80个Chunk索引、证据召回与DeepSeek报告。
- Realtime Follow-up成功经历`retrieving → generating → delta → done`，用户/助手消息均为completed。最终正式库包含Task 1、Paper 2篇、Document 1、Chunk 80、Report 1、Message 8、Tool Call 4、Trace 15与Embedding Cache 83；未导入任何旧SQLite Business Data。
- 正式Worker的`research_search`、`research_report`、`research_followup`三条Job均为`succeeded`、`attempt_count=1`、lease已释放且有worker/completed时间。active dedupe smoke首次创建1条queued follow-up，第二次立即被拒绝，active count保持1，随后Worker正常完成。
- 正式运行和完整pytest前后`downloads/metadata.db` SHA256始终为`d85eec...f00b`，证明Business SQLite已退出正式运行路径。LangGraph Checkpoint仍由SQLite SqliteSaver管理，本次E2E thread在`langgraph_checkpoints.db`中保存16条checkpoint，未迁移到MySQL。
- pytest根级隔离在任何项目模块导入前强制显式临时SQLite和临时DOWNLOAD_DIR，session结束时关闭测试SqliteSaver并清理目录；此前checkpoint recovery的`tmp_path`修复保留。正式`paper_hunter`在回归前后计数完全一致，`pytest/test-/checkpoint-source-resume`污染检查均为0，随机临时MySQL数据库残留为0。
- Phase 1–5真实MySQL回归`120 passed, 1 skipped`；SQLite/DB-intensive compatibility回归`246 passed, 2 skipped`；完整canonical pytest `834 passed, 2 skipped`；`git diff --check`通过。
- 当前MySQL用户仍拥有全局DDL、DML和管理权限，足以完成本地migration与运行，但权限明显宽于长期生产运行所需。后续生产化应拆分Migration User（DDL）与Runtime User（SELECT/INSERT/UPDATE/DELETE）；本轮不为权限重构阻塞Cutover。
- 回滚方案仅记录、不主动执行：停止FastAPI/Worker，将`BUSINESS_DB_BACKEND`显式改回`sqlite`，使用`metadata_before_mysql.db`恢复Business SQLite，并保留`paper_hunter`现场用于诊断。长期正式架构仍为Business Data使用MySQL、LangGraph Checkpoint使用SQLite SqliteSaver。
- Phase 1–6 Exit Criteria全部满足：Paper Hunter Business Database已完成SQLite → MySQL Fresh Cutover；Historical SQLite Business Data intentionally not migrated；不存在silent MySQL → SQLite fallback；未进入或修改LangGraph SqliteSaver迁移。
