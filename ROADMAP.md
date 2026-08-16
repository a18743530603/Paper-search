# Paper Hunter 后续开发路线与实施日志

> 当前项目版本：`v0.11.4`
>
> 日志建立日期：`2026-08-16`
>
> 当前阶段：`A7 最小MCP Server（下一步）`

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

**状态：未开始**

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

## B1：可靠 Worker / Queue

**状态：未开始**

该阶段解决 `BackgroundTasks`不能覆盖的问题：

- Web 进程重启后任务仍能重新投递。
- 下载、解析、索引和报告任务由独立 Worker 执行。
- 支持持久化、超时、有限重试、死信或最终失败状态。
- 数据库原子抢占继续保留，消息队列不能替代幂等状态转换。

## 暂不纳入当前主线

- 多 Agent：当前复杂度和项目收益不匹配。
- GraphRAG：当前论文证据问题主要是单段和页码级检索。
- Dashboard：A6 第一版先保证 Trace、场景和指标口径正确。
- 长期用户记忆：等 Agent 可靠性、MCP 和任务队列稳定后再评估。

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
