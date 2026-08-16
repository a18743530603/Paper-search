# Paper Hunter 版本更新记录

本文件按时间倒序记录项目的功能变化。每次功能更新都必须先确定版本号，并同步更新 `README.md`、`PROJECT.md`、`AGENTS.md`、`pyproject.toml` 和 FastAPI 应用版本。

## `v0.11.4` - 2026-08-16

### A6.5 Agent Evaluation Report

- 新增`AgentScenario`与11条冻结场景清单，覆盖A6.2固定可靠性行为和A6.3 Checkpoint恢复；`scenario_test_nodeids()`只运行清单中的测试。
- 新增pytest JUnit XML解析，逐场景保存状态、耗时与错误；`failed/skipped/not_run`均不能冒充通过。
- 新增`agent-evaluation-report-v1` JSON和可读文本报告，受控pytest结果与A6.4运行Metrics分区展示，并保留版本、生成时间、样本、指标口径和限制。
- 新增原子文件保存，向`outputs/agent_evaluation/`写入带时间戳和`latest`的JSON/文本文件；`outputs/`继续由Git忽略。
- 新增`scripts/run_agent_evaluation.py`开发入口；默认组合本地运行Metrics，`--skip-runtime-metrics`可避免读取运行SQLite，FastAPI接口不执行pytest。
- 修复Windows评测命令打印中文路径时的输出编码，复用`configure_utf8_stdio()`。
- 本次使用跳过运行Metrics模式真实执行冻结场景，结果`11/11 passed`；运行样本为0时报告明确显示N/A。
- 新增4项报告清单、JUnit解析、结构隔离和文件保存测试；完整离线测试`144 passed`，没有调用外部API。A6.1至A6.5至此完成。

## `v0.11.3` - 2026-08-16

### A6.4 Agent Metrics

- 新增`list_agent_metric_tasks()`与`list_agent_metric_traces()`只读快照函数，以及`agent_metrics_service.build_agent_metrics()`指标聚合服务。
- 新增Task Completion、Tool Success、Approval Compliance、Checkpoint Recovery和Fallback Success；所有比例返回分子、分母、排除数、百分比和定义。
- 等待审批、等待选择、未生成报告和用户取消不作为系统失败；尚未发起恢复的`interrupt()`不进入恢复率分母。
- 新增任务与节点平均耗时、按节点失败/部分失败分布，以及Tool部分成功计数。
- 模型调用与Token只聚合Trace中真实保存的usage；无观测时返回`null`与覆盖率，不使用上下文估算值。
- 新增内部JSON接口`GET /api/agent/metrics`；pytest场景结果未写入运行数据库，Scenario Pass Rate明确标为`not_persisted`，本版不建设Dashboard。
- 新增4项Metrics专项测试；完整离线测试`140 passed`，没有调用真实外部API。

## `v0.11.2` - 2026-08-16

### A6.3 Checkpoint恢复专项验证

- 新增`tests/agent/test_checkpoint_recovery.py`，分别从计划确认、论文选择和报告确认三个`interrupt()`暂停点关闭旧SQLite连接并创建新图恢复。
- 计划确认恢复验证Planner只调用一次，拒绝后搜索Tool调用为0；论文选择恢复验证Planner、搜索与去重节点均不重放。
- 报告恢复测试在第一篇论文准备完成后主动中断，再由第三个图实例从第二篇继续；两篇论文各准备、检索一次，报告模型只调用一次。
- 调用计数与`agent_traces`共同验证恢复正确，避免只检查最终状态而漏掉重复副作用。
- 本版没有修改生产业务逻辑，也没有调用真实外部API；A6.3专项测试`3 passed`，完整离线测试`136 passed`。
- 验证边界仅为`SqliteSaver + thread_id + Command(resume=...)`；进程内`BackgroundTasks`的服务重启恢复仍由B1可靠Worker/Queue解决。

## `v0.11.1` - 2026-08-16

### A6.2固定Agent场景测试

- 新增`tests/agent/test_scenarios.py`，把Planner失败、人工拒绝、非法Tool、搜索源失败、单篇论文失败、无合法Evidence、Checkpoint恢复和重复触发固定为8条独立场景。
- Planner失败场景验证原问题单查询fallback；人工拒绝场景验证搜索Tool零调用；非法Tool场景验证Registry在执行前拦截且无副作用。
- 来源失败场景验证健康搜索源继续返回并标记`partial`；单篇论文失败场景验证其余论文继续处理；Evidence门禁场景验证无证据时不调用报告模型。
- Checkpoint场景跨SQLite连接使用原`thread_id`恢复并断言Planner不重跑；重复触发场景并发验证下载和报告均只有一个请求取得数据库执行资格。
- 本版没有修改生产业务逻辑，也没有调用真实外部API；A6.2场景测试`8 passed`，完整离线测试`133 passed`。
- A6.3仍需专项验证三个`interrupt()`位置的恢复、已完成节点不重放和Trace一致性；`BackgroundTasks`跨进程恢复仍属于B1。

## `v0.11.0` - 2026-08-16

### A6.1统一Agent Trace

- 新增`agent_traces`表与创建、结束、任务回填和查询函数，保存节点状态、起止时间、耗时、Tool摘要、重试次数和错误。
- 新增`agent_trace_service.py`；`trace_node()`统一包装两个LangGraph中的所有节点，避免在业务节点中重复写日志代码。
- 首次规划在业务任务创建前按`thread_id`记录，创建`research_tasks`后通过`attach_agent_traces_to_task()`回填`task_id`；后续恢复在LangGraph配置中显式传递任务ID。
- `GraphInterrupt`记录为`interrupted`并继续交给LangGraph处理；恢复后同名节点新增一次尝试并递增`retry_count`。普通异常记录为`failed`后原样抛出，Trace存储异常不会中断业务节点。
- Tool返回包含失败或降级时节点可记录`partial`及错误摘要；`model_calls/input_tokens/output_tokens`字段暂不填充，等待模型API真实usage贯通。
- 新增`GET /api/research-tasks/{task_id}/traces`结构化查询接口；本版不实现Metrics和Dashboard。
- 新增4项Trace专项测试并修正一个旧mock对新增`task_id`关键字的兼容性；完整离线测试为`125 passed`，没有调用真实外部API。

## `v0.10.1` - 2026-08-06

### PDF下载任务原子抢占

- `db.py`新增`claim_download()`：使用单条条件`UPDATE`完成状态检查与`downloading`写入，通过受影响行数返回是否取得执行资格。
- 普通下载只允许`available → downloading`，失败重试只允许`failed → downloading`；未抢占成功的请求直接返回，不会创建重复`BackgroundTasks`。
- `paper_workflow.py`的问答准备入口复用同一抢占规则；后台函数通过`download_claimed`区分已经取得资格的任务，避免重复抢占或重复下载。
- 修复`update_download_status()`定义处的拼写残留，保留下载完成时间、失败原因和后续状态更新行为。
- 新增双线程并发抢占、非法起始状态和失败重试清错测试；完整离线测试为`121 passed`，不调用真实外部API。
- 本次不引入消息队列；`BackgroundTasks`仍不保证Web进程重启后的任务持久化，公网部署时需要另行引入独立Worker与可靠队列。

## `v0.10.0` - 2026-08-04

### A5-3/A5-4结构化上下文与质量门禁

- `research_report_service.py`新增`build_research_context_payload()`，将研究问题、证据和访问限制序列化为`research-context-json-v1`；证据使用稳定`E1...En`编号，并保留论文ID、标题、物理页、块ID和原文。
- 报告系统Prompt明确把JSON中的问题、标题、正文、访问原因和URL全部视为不可信数据，不执行其中的角色切换、工具调用或输出格式命令；正文原样保留，不用关键词删除改变学术证据。
- `context_service.py`新增`evidence_validation_errors()`和`context-quality-gate-v1`：缺少论文ID、块ID、标题、正物理页码或正文的证据在模型调用前剔除。
- 门禁检查可用上下文、字段完整性、Token预算、最终计数和最低论文配额，输出`passed/warning/failed`与`model_call_allowed`；全部非法或预算无法容纳时确定性拒答，不调用DeepSeek。
- `ResearchSearchState`新增`report_context_quality`，通过LangGraph检查点、业务SQLite、状态API和研究任务页面完整持久化；页面以绿/橙/红展示门禁状态。
- 保留“本来没有全文证据”的`deterministic-no-evidence`语义；只有存在输入但质量门禁失败时使用`deterministic-context-gate`。
- 新增不可信JSON转义、Prompt注入文本、非法证据剔除、模型阻断、检查点持久化和页面回归测试；完整离线测试为`119 passed`，没有调用真实外部API。
- A1-A5单Agent LangGraph主线至此完成；Prompt结构只能降低指令混淆风险，不能宣称完全消除Prompt Injection。MCP、长期记忆和多Agent属于后续独立阶段。

### A5-2保守证据去重

- `context_service.py`新增`normalize_evidence_text()`、`evidence_overlap_ratio()`和`deduplicate_paper_evidence()`；去重发生在公平预算分配之前，不调用DeepSeek、Seed或其他外部API。
- 精确重复使用NFKC、大小写折叠和空白归一化识别；高重叠重复使用5-token shingle containment，阈值固定为`0.85`，且只允许同一论文的同页或相邻物理页互相去重。
- 不同论文永不互相去重；短于12个shingle的证据不执行高重叠删除；候选按检索分数排序，始终优先保留分数更高的证据。
- 删除审计新增`exact_duplicate`、`overlap_duplicate`、`overlap_ratio`和`duplicate_of`；`context_stats`新增去重后证据数及两类重复计数，页面同步展示。
- 新增精确重复、相邻页高重叠、远距离相似、跨论文相同文本和统计回归测试；完整离线测试为`115 passed`。
- 当前属于可解释的文本重叠去重，不是Embedding语义去重；跨页超过一页的相似内容会保留，避免误删论文中重复出现但作用不同的定义或结论。

### A5-0/A5-1上下文基线与公平预算打包

- 新增`context_service.py`、`ContextConfig`、`estimate_text_tokens()`和`pack_research_context()`，在报告生成前执行不调用模型的确定性Context Engineering。
- 默认策略`fair_budget_v1`使用6000估算Token总预算、每篇目标至少1条且最多2条；论文内按检索分数排序，跨论文按轮次选择，优先避免单篇证据垄断上下文。
- 原始召回继续保存在`details_json.evidence`，最终模型输入保存在`context_evidence`；生成Prompt和A4引用校验只使用后者。
- `context_stats`记录压缩前后证据数、字符数、估算Token、压缩比例、论文覆盖、每篇配额、最低配额满足情况及删除审计。
- 超出配额和预算的证据分别记录`paper_quota`与`token_budget`；极端预算无法覆盖所有论文时保持硬预算并显式标记，不伪造100%覆盖。
- 研究任务页面和状态API新增上下文证据、估算Token、压缩率和论文覆盖展示。
- 新增中英文Token估算、公平配额、预算轮询、最低配额失败、A3/A4接入、状态落库和页面展示测试；完整离线测试为`110 passed`，没有调用真实外部API。
- 当前未使用DeepSeek官方Tokenizer；A5-2只实现确定性文本重叠去重，Embedding语义去重仍未实现。

### A4可恢复的端到端LangGraph报告阶段

- 生产图在论文选择后新增第三个`interrupt()`；用户点击“生成研究报告”才通过`Command(resume=...)`恢复同一`thread_id`，不会因选择论文自动下载或消耗模型额度。
- 新增逐篇自循环节点`prepare_report_paper`和`retrieve_report_evidence`；索引位置、证据、访问限制及工具摘要随每篇论文写入`SqliteSaver`检查点。
- 新增`begin_report_generation`、`synthesize_research_report`、`validate_report_citations`和`request_report_regeneration`节点；模型失败可从生成节点重试，完成后可在同一线程重新生成。
- 新增`stream_checkpointed_research_report()`和`execute_research_report()`；图状态逐节点同步到`research_reports`，旧A3任务没有报告检查点时保持兼容执行。
- `research_reports.details_json`新增`checkpoint_progress`与`citation_validation`；页面和状态API展示已准备/已检索论文数、有效引用数与警告状态。
- `validate_research_report_citations()`只用代码检查`[论文标题, p. 页码]`是否属于召回证据，不调用额外大模型，也不宣称验证事实语义。
- 应用启动时把遗留的报告、解析和索引运行状态改为可重试失败状态；重新提交后由图检查点和现有幂等准备服务跳过已完成工作。
- 新增第三中断、逐篇节点、跨连接重启续跑、业务状态同步、引用校验和中断报告恢复测试；完整离线测试为`106 passed`，没有调用真实外部API。

### A3证据驱动的跨论文研究报告

- 新增`prepare_paper_for_research`和`retrieve_paper_evidence`两个LangChain `StructuredTool`；分别复用已有`prepare_paper_for_qa()`与`retrieve_relevant_chunks()`，不复制下载、解析、索引或召回逻辑。
- 新增`research_report_service.py`；用户选定论文后显式点击生成，后台按`prepare → retrieve → synthesize`执行，每篇默认检索3条带物理页码的证据。
- 新增`research_reports`表及开始、查询、状态更新函数；状态为`preparing/retrieving/generating/completed/failed`，事务锁防止同一任务重复启动。
- 新增`POST /research-tasks/{task_id}/report`；研究任务页面展示报告进度、模型、证据数、访问限制、报告正文和重新生成入口，状态接口支持每2秒轮询。
- 报告Prompt只接收已召回证据，要求事实使用`[论文标题, p. 物理页]`引用；付费或无直链论文只记录合法访问链接，不推断未读取内容。
- 全部论文都没有证据时由代码生成拒答报告，不调用DeepSeek；单篇准备或检索异常转换为该论文的限制说明，不中断其余论文。
- 新增工具调用、数据库状态锁、证据/受限来源合并、无证据零模型调用、单篇失败隔离和报告页面测试；完整离线测试为`101 passed`，没有调用真实外部API。

### A2标准工具层与调用审计

- 新增`langchain-core>=1.5.3`与`research_tools.py`；`search_literature`是具有`query/max_results`参数Schema的标准`StructuredTool`，内部复用现有`search_all()`。
- 新增`RESEARCH_TOOL_REGISTRY`和`invoke_research_tool()`白名单入口；未知工具名直接拒绝，DeepSeek仍只生成计划，不能任意选择函数、下载PDF或写数据库。
- LangGraph检索节点改为通过工具注册表执行；`ResearchSearchState/ResearchSearchResult`新增`tool_calls`，保存每路查询的输入、状态、有效结果数、来源分布、错误和耗时，不在可重放节点内写业务库。
- 新增`research_tool_calls`表、`replace_research_tool_calls()`和`list_research_tool_calls()`；以任务内调用序号唯一并整体替换，保证检查点恢复后的审计记录幂等。
- 研究任务详情页新增工具调用轨迹，`GET /api/research-tasks/{task_id}`同步返回轨迹；`partial`与`failed`会展示来源级降级错误。
- 新增Tool Schema、结构化结果、工具轨迹持久化幂等、工作流落库和页面回放测试；完整离线测试为`95 passed`，没有调用真实外部API。

### LangGraph研究检索与人工确认闭环

- 新增`langgraph>=1.2.10`和`research_agent.py`，使用显式State、五个Node与条件Edge编排研究问题规划、人工确认、搜索和去重。
- DeepSeek通过现有`ask_deepseek_json()`生成2至4条结构化学术检索词；模型不负责下载、数据库写入或修改版权策略。
- 规划完成后先进入`await_confirmation`节点；缺少API Key、DeepSeek失败或计划校验失败时记录错误并保存原始问题单查询计划，不会在确认前调用搜索服务。
- 多查询与单查询节点复用`search_service.search_all()`；结果按DOI、网页URL和规范化标题去重，并限制为用户请求数量。
- 首页新增普通检索/智能研究分段控件，`POST /search`新增`search_mode`参数；智能研究返回持久化任务页，用户可确认或取消。
- 新增`research_tasks`表以及创建、查询、开始、取消、保存结果、选择论文和失败状态函数；保存计划、规划错误、候选/已选论文ID和阶段时间，应用启动时把中断的`running`任务恢复为待确认。
- 新增`research_workflow.execute_research_task()`，确认后通过FastAPI`BackgroundTasks`执行冻结计划，成功归档论文，异常只标记当前任务失败。
- 新增研究任务列表、详情、确认、取消和轻量状态接口；`research_task_poll.js`每2秒刷新运行结果。
- 检索完成后任务进入`awaiting_selection`而不是直接结束；详情页使用复选框让用户从当前任务候选中选择1至5篇，保存后状态为`papers_selected`。
- `research_tasks`新增`selected_paper_ids_json`、`selected_count`和`selected_at`；`init_db()`为旧数据库增量补列。
- 新增`select_research_task_papers()`和`POST /research-tasks/{task_id}/select`，校验数量、去重并拒绝不属于当前任务的论文ID；取消操作同时支持待确认和待选择状态。
- 论文选择只冻结后续输入，不触发下载、解析、Embedding或大模型报告，继续遵守用户触发与成本控制边界。
- 新增`langgraph-checkpoint-sqlite>=3.1.1`和`research_checkpoint.py`；检查点保存到`downloads/langgraph_checkpoints.db`，业务数据库新增唯一`checkpoint_thread_id`关联任务。
- 计划确认与论文选择均改为LangGraph动态`interrupt()`；恢复使用同一`thread_id`和`Command(resume=...)`，已完成的规划/搜索节点不会因等待或应用重启而重复运行。
- 中断节点在`interrupt()`前不执行写库等非幂等副作用；候选论文入库后通过`update_state()`把ID同步回暂停图，再由图内和业务层双重校验选择范围。
- `GET /api/research-tasks/{task_id}`新增检查点ID、待执行节点和工作流状态，便于审计原生恢复位置。
- 设置`LANGGRAPH_STRICT_MSGPACK=true`限制检查点反序列化类型；SQLite Checkpointer仅定位为本地/演示环境，公网多实例部署应替换PostgresSaver。
- 尚未实现跨论文报告、MCP或长期记忆；本版不引入多Agent。
- 新增跨SQLite连接模拟进程重启测试，连续恢复两个中断且搜索节点只执行一次；完整自动化测试为`93 passed`，测试不调用真实外部API。

## `v0.9.0` - 2026-08-03

### 论文问答准备闭环

- 新增 `paper_workflow.py`，使用 `prepare_paper_for_qa()` 依次调用已有 `download_paper()`、`parse_paper_pdf()` 和 `index_paper_chunks()`，没有复制下载、解析或索引逻辑。
- 新增 `paper_preparation_status()`，基于现有 `papers` 与 `paper_documents` 推导 `available`、`downloading`、`parsing`、`indexing`、`ready` 及各阶段失败状态，不新增数据库表。
- 新增 `mark_preparation_started()` 和 `POST /papers/{paper_id}/prepare`；用户明确点击后才加入 `BackgroundTasks`，搜索行为本身仍不会自动下载文件。
- 工作流能够跳过已经下载、解析或索引完成的步骤；下载、解析或索引失败时立即停止，再次提交可从未完成阶段继续。
- 搜索结果、历史页和论文详情页新增统一“问答准备”状态；保留“仅下载”以及原解析、索引按钮，方便单独使用和调试。
- `GET /api/papers/statuses` 新增 `preparation_status`，`status_poll.js` 每2秒更新统一状态并在完成或失败时刷新详情。
- 没有明确 PDF 直链的 Crossref 记录继续保持 `link_only`，不会进入全文处理流程；Seed失败时仍由原索引服务降级为TF-IDF。
- 新增首次完整执行、下载失败停止和从索引阶段恢复三项回归测试；完整自动化测试为 `81 passed`，测试未调用真实外部API。

## `v0.8.3` - 2026-08-03

### M1独立泛化验证

- 在冻结的`heldout_v2`上完成固定边界基线`#1`与M1多粒度融合`#2`的一次性对照；测试集包含8篇从未参与策略设计的论文和40道页码级证据题。
- 基线`Hit@1/3/5=50.00%/82.50%/90.00%`、`MRR=0.6675`、覆盖率`87.04%`；M1为`55.00%/77.50%/90.00%`、`MRR=0.6925`、覆盖率`88.66%`，两组均为40题、0错误。
- 10,000次配对bootstrap显示，M1的`Hit@3`差值为`-5.00`个百分点，95%区间`[-12.50,0.00]`个百分点；预注册主门槛未通过，其余三条保护门槛通过，因此`adopt_m1=false`。
- 逐题有6题改善、5题变差、29题不变，Top-5新增2题并丢失2题；M1提高首位排序与平均覆盖，但没有稳定提高前三名召回。
- 首次M1索引期间两次遇到Seed SSL EOF，严格索引一致性门禁拒绝TF-IDF静默降级；保持相同运行、数据和参数重试后完成，异常没有污染正式指标。
- M1继续作为可复现实验选项，普通解析默认仍为`length_boundary`；`heldout_v2`正式关闭，不得基于这40题继续调参后重新宣称独立验证。
- README新增策略与结果总览，区分固定边界默认召回、DeepSeek独立集重排成果和M1自定义实验边界；所有展示数字均可追溯到冻结运行记录。
- 版本收尾不新增算法参数；完整实验、缓存、耗时和采用门槛见`EXPERIMENTS.md`与`HELDOUT_V2_DATASET.md`，完整离线测试为`78 passed`。

## `v0.8.2` - 2026-08-02

### M1独立测试集预注册与数据冻结

- 新增`benchmarks/heldout_v2_manifest.csv`与`HELDOUT_V2_DATASET.md`，预先选定8篇开放获取且与既有20篇论文标题零重叠的新论文。
- 冻结固定边界与M1的唯一变量、40题标注方案、隔离运行边界、配对bootstrap方法和正式采用门槛；选样校验为8篇、既有标题重叠0、重复0。
- 用户授权后下载并验证8篇PDF，共308个物理页；新增`benchmarks/heldout_v2.csv`保存40道平衡的中文问题、参考答案、物理页码和连续英文证据，新增`benchmarks/heldout_v2_queries_en.csv`保存一一对应的冻结英文查询。
- 40段证据均由指定PDF物理页的原文起止标记自动截取；每篇五种题型各1题，五种题型各8题，问题与英文查询均无重复或缺失。
- 已在`HELDOUT_V2_DATASET.md`记录三个基准文件的SHA-256；从冻结起不得根据测试结果修改案例或查询。PDF、页文本和构建脚本保留在Git忽略的`downloads/`中。
- 本步骤只使用公开PDF，没有调用Seed或DeepSeek，也没有运行基线或M1指标；新增2个数据完整性测试，完整离线测试`78 passed`。下一步才执行一次性独立对照。

### 论文多粒度父子融合候选策略

- 新增 `paper_multi_granularity`：固定父块与完整句子子块同时索引，使用同一问题分别召回Top-20，再按共同父块ID做RRF融合并返回父块正文。
- `paper_chunks` 新增 `chunk_role`，旧数据库自动迁移；布局、索引和评测页显示父块/子块角色及冻结候选池参数。
- M1只改变候选来源，不修改英文查询、`1200/150`、子块规则、Seed/TF-IDF `0.75/0.25`、Top-5、RRF常数或重排器。
- 运行`#38/#39`结果一致：相对固定基线`#21`，Hit@3从`66.67%`提高到`76.67%`，Hit@5保持`88.33%`，MRR从`0.6036`提高到`0.6219`，覆盖率从`85.96%`小幅降至`85.28%`。
- 10,000次配对bootstrap的Hit@3区间仍跨0，因此该策略暂不替换默认固定分块；`heldout_v1`保持关闭，后续必须使用`heldout_v2`或预注册交叉验证。
- 新增旧库迁移、多粒度布局、父子双路融合及页面入口测试；完整离线测试为`76 passed`，实验与复跑没有产生新的Seed或DeepSeek请求。

## `v0.8.1` - 2026-08-02

### 实验体系正式归档

- 将 E3a/E3b、C1-C5、E3c、E4、G1-G3a 的代码、自动化测试和完整实验记录统一纳入正式版本；实验策略保留为可复现选项，不代表全部启用为线上默认策略。
- 新增 8 篇论文、40 道问题的 `heldout_v1` 留出测试集及冻结英文检索问题，配合原 12 篇、60 题开发集验证策略在未见论文上的泛化表现；PDF、SQLite 和模型缓存仍只保留在本地。
- 新增生成质量评测页面与服务，记录 Faithfulness、Answer Relevance、Citation Correctness、延迟、失败原因和逐题证据快照，并完成原始召回与 DeepSeek 重排的端到端对照。
- 本次发布累计覆盖分块、召回、重排、生成和评测链路；最终完整离线自动化测试为 `73 passed`。

### 普通论文问答可选重排

- 论文详情页新增默认关闭的 `DeepSeek 重排` 复选框；开启后先使用现有 Seed + TF-IDF 召回 Top-20，再复用经过 G2-R/H2-R 验证的 `rerank_candidates_with_deepseek()` 输出 Top-5 证据，最后生成答案。
- `POST /papers/{paper_id}/ask` 新增 `use_reranker` 表单参数；关闭时保持原始问答行为不变。
- `rag_queries` 新增 `reranker` 与 `rerank_error` 字段，`init_db()` 为旧 SQLite 自动迁移；问答历史展示原始排序/DeepSeek重排、初召回名次与重排名次。
- 重排调用或结构化输出失败时，系统记录错误并平滑退回原始 Top-5，仍继续调用生成模型回答，不把单次重排异常升级为整条问答失败。
- 新增数据库迁移、20→5重排、异常降级和页面开关测试；聚焦测试 `4 passed`，完整离线测试 `73 passed`，测试未调用真实外部 API。

### 生成评测证据一致性修复

- `evaluation_service.run_evaluation()` 保存逐题 `retrieved_json` 时不再截断 `content` 和 `retrieval_content`，新建检索运行会持久化完整 Top-K 文本块。
- `generation_evaluation_service` 继续读取检索运行保存的证据，因此修复后生成器、Hit@K 和证据覆盖率使用同一份完整文本，不修改分块、召回、重排、生成 Prompt 或裁判 Prompt。
- 普通网页 RAG 在 `rag_service.answer_rag_query()` 中原本就直接使用完整检索结果，本次缺陷和修复只涉及实验评测链路。
- 不修改数据库结构，也不覆盖旧实验。旧 `evaluation_results.retrieved_json` 已丢失的尾部内容无法恢复，必须按冻结配置新建检索运行后再重跑生成评测。

### 验证与范围

- 新增 `test_run_evaluation_persists_complete_evidence_for_generation`，验证 800 字符之后的关键证据仍会落库，并能够进入 RAG Prompt。
- 聚焦测试为 `6 passed`，完整自动化测试为 `70 passed`。
- 代码收尾本身未调用外部 API；随后按原冻结配置完成 G2-R 与 H2-R。开发集重排使 Answer relevance 从 `97.00%` 提高到 `99.83%`，区间 `[+0.50,+6.83]` 个百分点；8篇/40题未见集从 `99.00%` 提高到 `100.00%`，区间 `[+0.25,+2.00]` 个百分点。两套新检索运行的完整证据命中排名均为 `0 mismatch`。

## `v0.8.0` - 2026-07-28

### 章节上下文前缀消融

- 新增 `academic_section_no_prefix` 评测策略，与 `academic_section` 共用完全相同的章节识别、块边界、页码和正文，只在建立 Seed 与 TF-IDF 索引时移除 `[Section: ...]` 前缀。
- 新增 `rag_service.chunk_index_text()`，统一决定块的索引文本；页面展示和证据匹配继续使用原始文本块，避免消融实验改变标准证据。
- `cache_active_chunk_embeddings()` 按实际索引文本生成内容哈希，防止有前缀与无前缀向量写入错误缓存键。
- 评测页增加“论文章节无前缀”对照选项；该策略只用于实验，不替换正式 `academic_section`。

### 实验结果

- 12 篇论文、60 道问题、794 个文本块参与实验；逐块核对结果为 `mismatched_papers=[]`，证明唯一变量是索引文本是否包含章节路径。
- 有前缀运行 `#8/#11`：`Hit@1=51.67%`、`Hit@3=66.67%`、`Hit@5=75.00%`、`MRR@5=0.6033`、覆盖率 `74.34%`。
- 无前缀运行 `#9/#10`：`Hit@1=43.33%`、`Hit@3=63.33%`、`Hit@5=66.67%`、`MRR@5=0.5389`、覆盖率 `65.47%`。
- 章节前缀使 Hit@5 提高 `8.33` 个百分点、MRR 提高约 `11.95%`、覆盖率提高 `8.87` 个百分点；14 题排名改善、4 题变差、42 题不变。
- 正式结论是保留章节路径前缀；不能声称章节感知整体超过固定边界，因为固定边界运行 `#5` 的 Hit@5 仍为 `81.67%`。

### 验证与范围

- 新增相同块布局和正文-only索引测试，完整自动化测试为 `37 passed`。
- 运行 `#11` 已恢复正式 `academic_section` 有前缀索引，指标与 `#8` 完全一致。
- 本版本未实现中英文查询、BM25、RRF、重排、父子块或表格专用分块；下一步按单变量原则先进行固定英文查询消融。
- 本次正式收尾未调用 Seed、DeepSeek 或其他外部 API。

## `v0.7.0` - 2026-07-13

### 实验增量缓存

- 新增 `pdf_text_cache` 表，按 PDF SHA-256 和物理页码缓存 `pypdf` 提取结果；不同分块策略共享相同页文本。
- 新增 `embedding_cache` 表，按 Seed 服务/模型/接口模式命名空间与文本 SHA-256 缓存稠密向量；文本块与评测问题使用同一套缓存机制。
- `paper_documents` 新增 `source_hash`、`chunk_strategy`、`chunk_size`、`chunk_overlap` 和 `embedding_namespace`，用于精确判断分块和索引是否可复用。
- `pdf_service.extract_pdf_pages()` 与 `build_pdf_chunks()` 将 PDF 提取和分块解耦；`parse_paper_pdf()` 在配置未变化时直接复用当前块，换策略时复用页文本。
- `rag_service.cache_active_chunk_embeddings()` 自动回填升级前已有的 Seed 向量；`index_paper_chunks()` 只请求缓存中不存在的块；`retrieve_relevant_chunks()` 复用问题向量。
- `evaluation_service.run_configured_experiment()` 按参数差异跳过无关步骤：只改 Top-K/权重不再重建分块和索引，换策略只重新分块并补算新向量。
- 评测指标新增 `cache_stats`、准备耗时、评测耗时和总耗时；页面显示 PDF 页、分块配置、块向量、问题向量和论文索引的命中情况。

### 验证结果

- 新增 PDF 页缓存、分块配置缓存、块向量缓存和问题向量缓存离线测试。
- 完整自动化测试：`35 passed`。
- 本版本只优化实验计算路径，不修改分块、召回和证据判定算法；`v0.6.1` 的运行 `#5/#6` 仍是当前正式效果基线。
- 真实预热运行 `#7` 用时 `105.876` 秒；同配置全缓存运行 `#8` 用时 `4.406` 秒，约快 `24.0` 倍。
- `#8` 命中 794 个分块、12 篇论文索引和 60 个问题向量，Seed 请求为 0；Hit@K、MRR 和覆盖率与 `#7` 完全一致。

### 后续实验顺序

- 先做章节前缀消融，再测试冻结的中英双路查询与 BM25/RRF，之后加入 Top-20 到 Top-5 重排。
- 父子块返回和表格专用分块保留为后续实验；原子命题、Late Chunking 和 RAPTOR 暂缓。

## `v0.6.1` - 2026-07-13

### 数据集扩展

- `benchmarks/chunking_baseline.csv` 从 3 篇论文、15 题扩展到 12 篇论文、60 题。
- 新增论文编号 4 至 12 的 45 条人工标注案例，每篇固定包含事实型、方法型、参数型、结果型和原因型各 1 题。
- 将临时 `paper_id` 转换为正式论文标题，并使用标题与本地 PDF 文件建立关联；CSV 继续保持 `paper_title` 公共接口不变。
- 使用 `pypdf` 按物理页逐条验证新增证据，45 条证据覆盖率均高于 `0.65` 命中阈值，最低覆盖率为 `0.8333`。
- 新基准已导入本地 SQLite，共注册 12 篇论文和 60 条 `evaluation_cases`；本步骤未调用 Seed 或 DeepSeek。

### 验证结果

- 新增基准数据完整性测试，约束总题数、论文数、每篇题数、题型平衡、问题唯一性和正数页码。
- 完整自动化测试：`33 passed`。
- 完成 12 篇、60 题的同参数对照实验；两组实验均使用 `chunk_size=1200`、`overlap=150`、`top_k=5`、Seed 权重 `0.75` 和 TF-IDF 权重 `0.25`，12 篇论文均使用 Seed + TF-IDF 混合索引且无评测错误。
- 固定边界运行 `#5` 生成 `785` 块：`Hit@1=43.33%`、`Hit@3=66.67%`、`Hit@5=81.67%`、`MRR@5=0.5747`、平均最佳证据覆盖率 `79.25%`。
- 章节感知运行 `#6` 识别 `195` 条章节路径并生成 `794` 块：`Hit@1=50.00%`、`Hit@3=66.67%`、`Hit@5=75.00%`、`MRR@5=0.5950`、平均最佳证据覆盖率 `74.34%`。
- 逐题排名比较中，章节策略改善 `17` 题、变差 `12` 题、持平 `31` 题；Top-5 新增命中 `3` 题、丢失命中 `7` 题。丢失的 7 条证据仍完整存在于章节语料，属于排序变化，不是正文被删除。

### 当前结论

- 章节感知分块提升了首位命中和 MRR，方法型、参数型问题改善最明显；但 Top-5 与平均证据覆盖率下降，事实型和结果型受到影响，因此暂不替换固定边界默认策略。
- 下一步应在保持章节元数据的基础上测试跨语言查询扩展或重排序器，而不是继续凭少量样例手工调整章节规则。
- `v0.6.0` 的旧 3 篇、15 题指标只保留为历史记录，不参与本次 12 篇、60 题结论。

## `v0.6.0` - 2026-07-13

### 新增功能

- 新增 `academic_section` 论文章节感知分块，并保留 `length_boundary` 作为可复现实验基线。
- `pdf_service.detect_section_heading()` 支持阿拉伯数字、多级编号、罗马数字、IEEE 字母小节和 `A.1` 附录编号。
- `pdf_service.split_academic_page_text()` 跨页继承章节层级，为子块增加 `[Section: 父章节 > 子章节]` 前缀，并在每个物理页内继续按大小与重叠切分。
- 支持合并 PDF 提取器拆开的标题，例如 `II. M` 与 `ATERIALS AND METHODS`；过滤表格数字、正文编号步骤等常见误判。
- 进入 `References`、`Bibliography` 或致谢后停止索引，遇到明确 `Appendix` 后恢复，避免参考文献作者名污染召回并保留附录证据。
- 评测页开放分块策略选择；运行记录额外保存 `detected_section_count`，页面展示识别章节数。

### 修改内容

- `extract_pdf_chunks()` 与 `parse_paper_pdf()` 新增 `strategy` 参数；默认仍为 `length_boundary`，不改变普通论文解析行为。
- `ExperimentConfig` 新增 `chunk_strategy`，配置校验、运行创建、后台重建和 SQLite 快照均使用用户选择的策略。
- 前端切换策略时自动更新实验名称，便于历史运行区分固定边界与章节实验。

### 真实章节实验

- 固定边界基线 `1200/150`：约 `163` 块，`Hit@1=66.67%`、`Hit@5=93.33%`、`MRR@5=0.7667`、覆盖率 `91.49%`。
- 初版章节实验 `#3`：`132` 块、`40` 个章节路径，`Hit@1=66.67%`、`Hit@5=80.00%`、`MRR@5=0.7333`、覆盖率 `80.80%`。诊断发现 AOD 附录被参考文献锁误过滤。
- 修正版章节实验 `#4`：`148` 块、`44` 个章节路径，`Hit@1=60.00%`、`Hit@5=80.00%`、`MRR@5=0.6889`、覆盖率 `83.30%`。
- 结论：章节策略减少了参考文献噪声并保留结构上下文，但不能单独解决中文问题到英文证据的排序；两道完整证据分别位于第 6、7 名，后续应加入查询翻译/扩展或重排序器，而不是只调字符数。

### 验证结果

- 新增标题编号、跨行合并、跨页层级、参考文献锁和附录恢复等回归测试。
- 使用三篇真实 PDF 校验，最终识别约 `44` 条唯一章节路径并生成 `148` 个活动文本块。
- 完整自动化测试：`32 passed`。

### 尚未实现

- 尚未加入章节标题独立向量、父子召回、查询翻译和 Cross-Encoder/大模型重排序。
- 尚未加入 Faithfulness、Answer Correctness 等生成层指标，本版本仍只评测检索层。

## `v0.5.0` - 2026-07-13

### 新增功能

- 评测页新增实验配置表单，可设置实验名称、块大小、重叠、Top-K 和 Seed 语义权重；TF-IDF 权重自动取补数。
- 新增不可变 `ExperimentConfig`，以及 `validate_experiment_config()`、`create_experiment_run()` 和 `run_configured_experiment()`。
- 每次实验会对标准论文强制重新提取文本块、重新调用 Seed 建立稠密向量，并保存该次参数、总文本块数、指标和逐题召回快照。
- 历史运行表新增块大小与重叠列；运行详情新增 Top-K 和总文本块数。

### 修改内容

- `pdf_service.extract_pdf_chunks()`、`parse_paper_pdf()` 新增 `chunk_size` 和 `overlap` 参数，同时保留普通解析的默认值 `1200/150`。
- `rag_service.retrieve_relevant_chunks()` 新增语义/关键词权重参数，使用前自动归一化并拒绝无效权重。
- 当实验 `top_k` 大于 5 时，Hit@5、MRR@5 和平均证据覆盖率仍只按前 5 个结果计算，保证不同 Top-K 运行可比较；更多结果仅用于错误分析。
- 修复重叠输入 `step=50` 导致 `120` 被浏览器判定无效、无法提交的问题，步进改为 `10`。

### 真实参数实验

- 基线 `1200/150`：约 `163` 块，`Hit@1=66.67%`、`Hit@5=93.33%`、`MRR@5=0.7667`、覆盖率 `91.49%`。
- 实验 `900/120`：`217` 块，`Hit@1=53.33%`、`Hit@5=93.33%`、`MRR@5=0.6856`、覆盖率 `92.80%`。
- 结论：更小块提高了平均证据覆盖，但增加候选与排序噪声，不能单凭粒度变细判断分块策略更优。

### 验证结果

- 新增实验配置边界、权重推导、按论文去重重建、总块数统计和表单渲染测试。
- 完整自动化测试：`27 passed`。
- 已在桌面端和 `390 x 844` 窄屏下完成浏览器验收，配置表单、后台状态、双运行趋势曲线和响应式布局均正常。

### 尚未实现

- 页面策略目前仅有 `length_boundary`；语义分块、自适应分块和父子分块将在后续版本实现。
- 尚未加入 Faithfulness、Answer Correctness 等生成层指标，本版本仍只评测检索层。

## `v0.4.1` - 2026-07-13

### 新增功能

- 评测页新增“当前运行 Top-K 命中率”曲线，直观展示 Hit@1、Hit@3、Hit@5 随候选数量增加的变化。
- 评测页新增“历次运行趋势”曲线，按运行编号比较 Hit@1、Hit@3 和 Hit@5，便于后续对照不同分块及召回参数。
- `evaluation_poll.js` 新增 `drawLineChart()` 和 `renderEvaluationCharts()`，使用原生 Canvas 绘图，不依赖外部 CDN，也不会额外调用 Embedding 或 DeepSeek API。
- README 新增完整评测操作流程、基准 CSV 字段要求、物理页码说明和对照实验注意事项。

### 修改内容

- 评测运行表格增加指标数据属性，作为历史趋势图的数据来源。
- 图表会跟随窗口宽度重新绘制，并在窄屏下自动切换为单列布局。
- 项目版本由 `v0.4.0` 更新为 `v0.4.1`；本次与基线评测属于同日的小版本完善。

### 验证结果

- 新增评测模板指标数据与两个 Canvas 容器的渲染测试。
- 完整自动化测试：`24 passed`。
- 已在桌面端和 `390 x 844` 窄屏下完成浏览器验收，确认两张曲线正常绘制且布局无重叠。

## `v0.4.0` - 2026-07-13

### 新增功能

- 新增 `benchmarks/chunking_baseline.csv`，保存 3 篇论文、15 条人工标注问题、参考答案、PDF 物理页码和连续原文证据。
- 新增 `evaluation_service.py`，提供 `find_local_pdf()`、`import_benchmark_cases()`、`prepare_benchmark()`、`evidence_coverage()`、`create_baseline_run()` 和 `run_evaluation()`。
- 新增 `evaluation_cases`、`evaluation_runs`、`evaluation_results` 三张 SQLite 表，分别保存标准案例、运行参数/指标和逐题召回结果。
- 新增 `db.register_local_paper()`，支持将 `downloads/papers/` 中已有 PDF 注册为可解析论文记录。
- 新增 `GET /evaluation`、`POST /evaluation/prepare`、`POST /evaluation/run` 和 `GET /evaluation/runs/{run_id}`。
- 新增评测页面，展示运行参数、总体指标、历史运行、标准案例和逐题 Top-K 召回片段。
- 新增 `evaluation_poll.js`，评测运行期间每 2 秒自动刷新。

### 指标与匹配规则

- 使用“PDF 物理页码一致 + 规范化 token 覆盖率不低于 0.65”判断召回块是否命中标准证据。
- 计算 `Hit@1/3/5`、`Recall@1/3/5`、`MRR@5` 和平均最佳证据覆盖率。
- 第一版每题只有一条标准证据，因此 `Recall@K` 与 `Hit@K` 数值相同；后续多证据问题会让两者产生区别。
- 匹配逻辑容忍 PDF 提取造成的换行、断词和符号差异，不使用整句字符串全等。

### 真实基线结果

- 固定分块策略：`length_boundary`，`chunk_size=1200`，`overlap=150`，`top_k=5`。
- 混合召回：Seed1.6 语义权重 `0.75`，TF-IDF 关键词权重 `0.25`。
- 15 条问题实测：`Hit@1=66.67%`、`Hit@3=80.00%`、`Hit@5=93.33%`、`MRR@5=0.7667`、平均最佳证据覆盖率 `91.49%`，无 API 错误。
- 唯一未命中案例为 AOD 参数题；正确证据块内容完整、覆盖率 100%，但排名第 18，属于块级排序失败，不是文本被切断。

### 验证结果

- 新增证据断词容错、指标聚合和评测数据库持久化测试。
- 完整自动化测试：`23 passed`。

### 尚未实现

- 结构化分块、语义分块和父子层级分块。
- 多条标准证据及跨段问题评测。
- Cross-Encoder 重排序、查询翻译和分块策略对比图。

## `v0.3.0` - 2026-07-13

### 新增功能

- 接入 Seed1.6 `Doubao-embedding-vision-251215`，用于论文文本块和用户问题的稠密向量生成。
- `model_service._seed_api_mode()` 根据模型名称和 `SEED_EMBEDDING_API_MODE` 自动选择文本或多模态接口。
- `model_service._request_seed_embedding()` 统一处理火山方舟鉴权、超时、HTTP 错误和 JSON 响应。
- `model_service._extract_multimodal_vector()` 兼容多模态接口的嵌套向量响应，并校验向量有效性。
- `.env.example` 新增 `SEED_EMBEDDING_API_MODE`，推荐模型改为 `doubao-embedding-vision-251215`。

### 修改内容

- `embed_with_seed()` 对 `embedding-vision` 模型调用 `/api/v3/embeddings/multimodal`，每个论文文本块使用一个纯文本输入对象。
- 继续兼容旧文本模型的 `/api/v3/embeddings` 批量请求；使用 `ep-...` 接入点时可显式设置 `text` 或 `multimodal`。
- 火山方舟请求失败时仍自动降级到本地 TF-IDF，不中断论文索引。
- `pyproject.toml` 与 FastAPI 应用版本同步升级为 `0.3.0`。

### 安全说明

- 真实 DeepSeek 与火山方舟 API Key 只能写入被 Git 忽略的 `.env`，不得写入代码、`.env.example`、测试或 Markdown 文档。
- 在聊天、截图或公开页面中暴露过的密钥必须撤销并重新生成。

### 验证结果

- 新增多模态 Embedding 请求结构、逐文本向量顺序和嵌套响应解析测试。
- 完整自动化测试：`20 passed`，覆盖旧文本接口、新版多模态接口、混合召回、DeepSeek 请求封装和 Web 页面流程。
- `2026-07-13` 完成真实 API 连通性验收：`doubao-embedding-vision-251215` 成功返回 1 个 2048 维向量，`deepseek-v4-pro` 成功返回预期短答。

### 尚未实现

- 暂未使用新模型的图片与视频向量能力，当前只向量化 PDF 提取后的文本。
- 尚未使用真实论文完成“下载、解析、索引、检索、生成答案”的浏览器全流程验收。

## `v0.2.0` - 2026-07-12

### 新增功能

- 新增 `pdf_service.py`，支持 PDF 文本清理、按页提取、重叠分块和后台解析。
- 新增 `rag_service.py`，支持论文访问链接、全文缺失提示和 RAG Prompt 构造。
- 新增 `POST /papers/{paper_id}/parse` 接口。
- 新增 `paper_documents` 数据表，保存解析状态、页数、文本块数量和错误原因。
- 新增 `paper_chunks` 数据表，保存论文 ID、页码、文本块顺序和正文。
- 论文详情页新增 PDF 解析按钮、解析状态、错误信息和文本块预览。
- 未取得全文时，页面和 Prompt 会提示论文可能需要购买或机构订阅，并提供论文网页或 DOI 链接。
- 新增 `pypdf` 依赖。
- 新增 `GET /api/papers/statuses`，提供下载与解析状态的轻量轮询数据。
- 新增 `status_poll.js`，每 2 秒自动更新页面状态，无需手动刷新。
- 新增 `downloaded_at` 字段，记录并展示论文实际下载成功日期。
- 新增 `clear_paper_history()` 和 `POST /papers/clear`，支持在历史页清空数据库记录。
- 清空历史记录前增加二次确认，并明确保留本地 PDF 文件。
- 将 PDF 获取方式由“搜索后自动下载”改为“发现可用 PDF 后显示下载按钮”。
- 新增 `available` 状态和 `POST /papers/{paper_id}/download` 接口。
- 用户点击下载后才进入 `downloading` 并由 `BackgroundTasks` 后台处理。
- 接入 DeepSeek `/chat/completions`，实现真实大模型论文问答。
- 新增 `model_service.py`，统一处理 DeepSeek 配置、鉴权、请求、超时和异常。
- 删除 `agent_service.py`，其确定性搜索词入口合并到 `model_service.py`。
- 新增本地 `local-tfidf-v1` 向量索引，不依赖 OpenAI 或其他 Embedding API。
- 新增 `index_paper_chunks()`、`retrieve_relevant_chunks()` 和 `answer_rag_query()`。
- 新增 `rag_queries` 表，保存问题、回答、页码证据、模型、状态和错误。
- `paper_chunks` 新增 `vector_json`，`paper_documents` 新增索引状态、向量器和索引时间字段。
- 新增 `POST /papers/{paper_id}/index` 和 `POST /papers/{paper_id}/ask`。
- 新增 `.env.example`，使用 `DEEPSEEK_API_KEY` 等环境变量，真实 `.env` 不提交 Git。
- 接入火山方舟 `/api/v3/embeddings`，支持 Seed1.5-Embedding 或对应 Endpoint ID。
- 新增 `is_seed_configured()` 和 `embed_with_seed()`，支持批量稠密向量生成与响应顺序校验。
- `paper_chunks` 新增 `dense_vector_json` 保存 Seed 稠密向量。
- 检索升级为 `0.75 × Seed语义相似度 + 0.25 × TF-IDF关键词相似度` 的双路混合召回。
- Seed 未配置或调用失败时自动降级为 TF-IDF，索引仍保持可用并记录降级原因。
- `.env.example` 新增 `SEED_API_KEY`、`SEED_BASE_URL`、`SEED_EMBEDDING_MODEL` 和 `SEED_TIMEOUT`。

### 规则与安全

- 大模型只能根据提供的元数据和已解析全文片段回答。
- 没有全文时不得假装已经阅读论文，也不得断言论文一定收费。
- 不提供绕过付费墙、登录验证或版权限制的方法。

### 文档与工程

- `README.md`、`PROJECT.md` 和 `AGENTS.md` 已同步记录新增模块、函数、路由、数据库表和状态。
- 新增版本号和更新日期维护规范。
- 新增 `CHANGELOG.md` 作为统一版本历史入口。
- 将 `outputs/` 加入 `.gitignore`，避免提交临时检查产物。

### 验证结果

- 自动化测试：`19 passed`（`2026-07-13` 复核），覆盖 DeepSeek 请求封装、Seed API 请求封装、Seed + TF-IDF 混合召回、问答入库和问答页面表单。
- FastAPI 首页、历史页和论文详情页响应检查：均返回 HTTP `200`。
- `/papers/{paper_id}/parse`、`/index` 和 `/ask` 等路由已完成注册检查。

### 尚未实现

- Cross-Encoder 或大模型重排序。
- 多论文联合问答。
- RAG 评测集和自动质量指标。

## `v0.1.x` - 日期未记录

### 已有功能

- 完成 FastAPI 搜索、历史记录、论文详情和 CSV/JSON 导出。
- 接入 arXiv 与 Crossref 检索。
- 使用 `BackgroundTasks` 后台下载 PDF。
- 实现 Crossref `link_only` 平滑降级策略。
- 使用 SQLite 保存论文元数据和下载状态。
- 增加出版商字段、旧数据库迁移及页面展示。
- 增加 Origin 数据导出和可选自动绘图功能。
- 将项目文档重写为中文。

> 这些功能来自项目早期迭代，但当时没有逐次记录可靠日期，因此统一保留为 `v0.1.x` 历史说明。
