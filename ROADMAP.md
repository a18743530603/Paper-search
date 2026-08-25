# Paper Hunter 后续开发路线与实施日志

> 当前项目版本：`v0.15.0`
>
> 日志建立日期：`2026-08-16`
>
> 当前阶段：`B1可靠Worker阶段已完成，下一阶段待确认`

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
