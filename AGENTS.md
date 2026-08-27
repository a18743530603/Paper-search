# Paper Hunter 项目记忆

> 当前版本：`v0.17.1`
>
> 更新日期：`2026-08-27`
>
> 版本历史：见 [CHANGELOG.md](CHANGELOG.md)
>
> 完整实验步骤与待办：见 [EXPERIMENTS.md](EXPERIMENTS.md)
>
> 后续开发路线与实施日志：见 [ROADMAP.md](ROADMAP.md)
>
> 技术栈、系统架构与完整业务流程：见 [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)

> 发布状态：E3a/E3b、C1-C5、E3c、E4、G1-G3a 的代码、测试与实验记录已在 `v0.8.1` 统一归档发布。下文“实验完成时未单独提交”的描述仅保留当时的开发过程，不再表示这些内容禁止进入 Git。

本文件记录创建和维护本项目时形成的重要背景，供以后在本仓库中新开的 Codex 对话读取，避免丢失项目上下文。

## 用户目标

用户希望完成一个适合写入简历的文献检索项目，能够：

- 接收用户输入的关键词或论文标题。
- 从网络检索相关学术论文。
- 保存论文网页地址和元数据。
- 在数据源提供信息时展示出版商。
- 对明确免费或开放获取的论文显示下载按钮，由用户确认后下载 PDF。
- 将下载文件保存到本地目录。
- 让新手能够看懂项目结构、运行方式和实现思路。

项目名称为 **Paper Hunter**。

GitHub 仓库：

```text
https://github.com/a18743530603/Paper-search.git
```

## 已确定的产品方案

- 项目采用 **FastAPI Web 应用**，而不是只提供命令行脚本。
- 新任务可在首页从后端白名单提供的 21 个论文来源中多选，默认全部选中；升级前旧任务仍回退 arXiv 和 Crossref。
- arXiv 是主要的可下载 PDF 来源，因为它的 PDF 地址格式较稳定；实际下载必须由用户点击按钮触发。
- Crossref 采用保守策略：只有元数据提供以 `.pdf` 结尾的绝对直链时才尝试下载，否则保存 DOI 或网页地址并标记为 `link_only`。
- `POST /search`统一创建研究工作台任务和可调整计划；只有用户确认后才由后台Worker执行检索。
- 明确可下载的论文标记为`available`；用户点击“仅下载”或“准备问答”后写入SQLite持久队列，由独立Worker执行。
- 使用 SQLite 在本地保存论文元数据、研究任务和下载状态。
- 大模型使用 DeepSeek API；本地搜索下载功能保持独立，没有 API Key 时仍可运行。
- 不接入 Sci-Hub、付费数据库或必须登录的下载来源。

## 当前项目结构

主要代码位于 `my_agent_project/`。

- `main.py`：FastAPI 入口，注册页面和导出接口，连接检索、数据库与后台下载服务。
- `config.py`：配置运行路径和LangGraph检查点路径，创建下载目录，并处理 Windows UTF-8 输出。
- `schemas.py`：定义 `PaperCandidate` 和 `available`、`downloading`、`downloaded`、`link_only`、`failed` 等状态。
- `search_service.py`：保持`search_all()`单query兼容接口，并通过`search_many()`把任务queries批量委托给论文搜索MCP；旧arXiv/Crossref解析器仅作为离线夹具保留。
- `paper_search_mcp_service.py`：用一个stdio MCP Session复用第三方`search_papers`完成多query、多来源元数据召回，将第三方Paper隔离映射为现有`PaperCandidate`，不调用下载Tool。
- `download_service.py`：下载 PDF、清洗文件名、处理重名并隔离单篇论文的下载异常；可靠Worker可要求异常在记录失败后继续抛出。
- `mineru_service.py`：调用MinerU云端精准解析API，流式上传PDF、轮询批次、下载并安全读取结构化结果，将章节、表格、公式、列表、页码和bbox转换为检索块，并按PDF哈希缓存结果。
- `task_queue_service.py`：注册下载、完整准备、单独解析、索引、RAG回答、研究搜索、报告和报告后追问处理器，协调租约领取、有限重试、最终失败和Worker中断恢复。
- `worker.py`：独立SQLite任务Worker入口；执行论文处理、问答、研究搜索、报告和`research_followup`持久任务，不承载Web请求。
- `paper_workflow.py`：将用户触发的下载、PDF解析和向量索引串成后台准备流程，按现有状态跳过已完成步骤并在失败阶段停止。
- `agent_trace_service.py`：集中包装LangGraph节点，尽力记录成功、中断、部分成功和失败轨迹；Trace故障不得改变原节点行为。
- `agent_metrics_service.py`：从任务、报告和Node Trace聚合带明确分子/分母/排除数的Agent运行指标；没有真实usage时不得生成Token数字。
- `agent_evaluation_service.py`：维护11条冻结Agent场景，解析pytest JUnit XML，并把受控测试结果与运行Metrics分区生成JSON/文本报告。
- `mcp_server.py`：使用官方MCP Python SDK建立独立stdio服务；当前暴露元数据查询、外部文献检索和已索引证据检索三个受控Tool，全部复用现有Service/Tool Registry，未暴露论文准备能力。
- `scripts/smoke_mcp_client.py`：用当前Python解释器启动独立MCP Server子进程，只执行stdio协议初始化与Tool发现并输出JSON；不调用业务Tool或外部API。
- `context_service.py`：在报告生成前校验证据字段、按论文执行保守文本去重，再按估算Token预算和跨论文公平配额选择证据；输出质量门禁、模型上下文、重复关系和压缩审计，不调用额外模型。
- `research_agent.py`：使用LangGraph编排研究问题规划、三次`interrupt()`人工确认、多查询/单查询条件路由、搜索、论文选择、逐篇准备、证据检索、报告生成和引用检查；规划失败自动降级，不改变下载合规边界。
- `research_checkpoint.py`：使用`SqliteSaver`、稳定`thread_id`和`Command(resume=...)`保存并恢复生产图；检查点与业务任务SQLite分开存储。
- `research_tools.py`：以LangChain `StructuredTool`和白名单注册表封装论文元数据检索，统一校验输入并返回来源、状态、错误和耗时摘要。
- `research_workflow.py`：后台恢复用户已确认的检查点线程，归档论文、同步候选论文ID，并逐节点把报告图状态同步到业务SQLite；旧任务保留A3兼容入口。
- `research_report_service.py`：在用户冻结论文并点击生成后，按准备、证据检索和DeepSeek综合顺序生成跨论文报告；使用不可信JSON上下文边界，逐篇隔离异常，无证据或门禁失败时确定性拒答。
- `research_conversation_service.py`：在报告完成后，先更新Task Memory，再使用当前追问、active Memory、滚动摘要、最近10条有效followup消息、已有报告和新召回证据生成带引用回答；摘要和Memory提取非流式，网页回答真实流式，旧Worker任务保留为无JavaScript回退。
- `followup_context.py`：Unified Follow-up ContextBuilder纯装配层；接收上游已准备的Memory、Summary、Recent、Question、Report、Evidence和Access Limitations，输出稳定`research-followup-context-v4`，不查库、不检索、不调用模型或修改输入。
- `task_memory_service.py`：使用DeepSeek严格JSON从当前User消息提取任务内持久偏好、要求、研究重点和决定；LLM只提出操作，代码校验临时性、论文事实、分类、长度、任务归属和幂等后再事务写库。
- `db.py`：初始化和操作 SQLite，保存论文与研究任务、更新状态、缓存 PDF 页文本与 Seed 向量，并导出 CSV/JSON。
- `model_service.py`：统一管理 DeepSeek `/chat/completions` 与火山方舟 Seed `/embeddings`、`/embeddings/multimodal` 的配置、鉴权、请求和异常；已合并并替代原 `agent_service.py`。
- `pdf_service.py`：生产解析默认路由到MinerU云端；只有评测显式传入策略时才使用历史`pypdf`分块链路，以保留固定边界、章节感知和多粒度实验的可复现性。
- `rag_service.py`：按模型命名空间与文本哈希缓存 Seed 稠密向量，提供 TF-IDF 关键词向量、混合召回、受约束 Prompt 和 DeepSeek 后台问答；没有全文时必须提示可能需要购买或机构订阅并附上链接。
- `evaluation_service.py`：导入人工标注基准、校验 `ExperimentConfig`、按配置差异复用或重建分块与索引、记录缓存命中与耗时、匹配标准证据并聚合 Hit/Recall/MRR 指标。
- `origin_service.py`：整理统计数据，并在本机安装 Origin/OriginPro 时尝试自动生成图表。
- `templates/`：Jinja2 页面模板。
- `static/styles.css`：网页样式。
- `static/source_picker.js`：读取服务端渲染的来源复选框，处理全选、清空、计数和零来源提交拦截；不得在脚本中维护来源 ID。
- `tests/`：解析、下载策略、文件名清洗和异常降级等离线测试。

主要接口：

```text
GET  /
POST /search
GET  /research-tasks
GET  /research-tasks/{task_id}
POST /research-tasks/{task_id}/confirm
POST /research-tasks/{task_id}/cancel
POST /research-tasks/{task_id}/select
POST /research-tasks/{task_id}/message
POST /research-tasks/{task_id}/report
GET  /api/research-tasks/{task_id}
GET  /api/research-tasks/{task_id}/traces
GET  /api/agent/metrics
GET  /papers
GET  /papers/{paper_id}
POST /papers/{paper_id}/retry
POST /papers/{paper_id}/prepare
POST /papers/{paper_id}/parse
POST /papers/{paper_id}/index
POST /papers/{paper_id}/ask
GET  /export.csv
GET  /export.json
GET  /origin
GET  /evaluation
POST /evaluation/prepare
POST /evaluation/run
GET  /evaluation/runs/{run_id}
```

## 数据流程

```text
浏览器提交关键词或研究问题
  -> POST /search
  -> 统一创建研究任务、初始会话消息并进入LangGraph
  -> plan_research() 调用DeepSeek JSON生成2至4条检索词
  -> request_plan_confirmation()调用interrupt()保存图检查点并暂停
  -> research_tasks保存计划、待确认状态和稳定checkpoint_thread_id
  -> 用户可POST /research-tasks/{task_id}/message补充范围
  -> create_research_plan()重新规划并在同一thread_id更新检查点
  -> 用户确认后原子写入running状态与research_search持久任务
  -> 独立Worker使用Command(resume=...)恢复同一线程
  -> 条件边选择multi_query_search或single_query_search降级
  -> 两个节点都把当前任务queries与State中的明确sources交给search_literature_batch()
  -> search_many()一次调用search_queries_via_mcp()，同一stdio Session内逐条执行search_papers
  -> 每条query保留独立search_literature Tool Trace，单来源失败记录partial并继续使用健康候选
  -> deduplicate_results()按DOI、URL和标题去重并限制数量
  -> db.insert_papers() 保存元数据
  -> replace_research_tool_calls()幂等保存工具输入、来源、状态和耗时
  -> update_state()将已入库候选ID同步回图状态
  -> request_paper_selection()第二次调用interrupt()并暂停
  -> 任务进入awaiting_selection并自动刷新候选结果
  -> 用户从当前候选中选择1至5篇，使用Command(resume=...)恢复
  -> 图内和业务层双重校验候选范围，任务进入papers_selected
  -> 图进入第三个report_confirmation interrupt，选择本身不自动下载论文
  -> 用户在会话区单独填写report_question，不复用最初检索主题
  -> 用户点击 POST /research-tasks/{task_id}/report
  -> Command(resume=...)恢复同一thread_id
  -> prepare_report_paper与retrieve_report_evidence节点逐篇执行并保存检查点
  -> link_only、付费或单篇失败记录合法链接和限制，不中断其他论文
  -> begin_report_generation调用fair_budget_v1选择context_evidence
  -> 原始evidence完整保留，模型只接收预算内的跨论文公平上下文
  -> 存在证据时DeepSeek生成带引用报告；全部无证据时代码直接拒答
  -> validate_report_citations节点零Token校验标题/页码引用
  -> 图停在report_regeneration interrupt，允许同线程重新生成
  -> 报告完成后用户在同一页面继续提问
  -> JavaScript调用POST /research-tasks/{task_id}/chat
  -> 短事务写入用户消息和pending助手消息，不修改已有报告
  -> 当前User消息先由Task Memory Extractor提出ADD/UPDATE/REMOVE或NOOP
  -> 代码校验后事务更新当前任务active Memory；失败保留旧Memory并继续
  -> Web流式链路按所选论文重新召回证据，并结合当前报告调用DeepSeek
  -> 读取同任务conversation_summary和最近10条有效原文
  -> 仅把刚退出窗口且未被summary_up_to_message_id覆盖的旧消息非流式合并进摘要
  -> 摘要失败记录错误并继续使用旧摘要；摘要和历史都不能替代Evidence
  -> research-followup-context-v4按Memory、Summary、Recent、Current、Report、Evidence组织
  -> 浏览器消费status/delta/done/error事件并增量更新同一助手消息
  -> 完成时research_messages保存完整回答、证据和模型；刷新后由服务端历史恢复
  -> 无JavaScript时旧/message入口仍可创建research_followup持久任务交给Worker
  -> 可下载论文继续显示用户触发的下载按钮
  -> 用户点击 POST /papers/{paper_id}/download
  -> main.py在同一SQLite事务中抢占论文状态并写入background_jobs
  -> 独立Worker使用租约领取download_paper任务
  -> download_service.download_paper() 下载并更新SQLite
  -> 失败时有限重试；Worker中断时租约到期后重新入队
  -> 用户也可点击 POST /papers/{paper_id}/prepare
  -> main.py原子锁定当前下载/解析/索引阶段并写入prepare_paper任务
  -> Worker复用paper_workflow.prepare_paper_for_qa()从断点执行到ready
  -> 解析阶段由MinerU精准解析API申请上传URL、上传PDF、轮询批次并下载content_list.json
  -> 结构化块连同章节、内容类型、物理页码和bbox写入SQLite
  -> 单独POST /parse或/index分别写入parse_paper/index_paper任务
  -> 活动任务拒绝重复并发；完成后仍允许显式重新解析或索引
  -> 用户提交POST /papers/{paper_id}/ask
  -> main.py在同一事务中创建rag_queries与answer_rag_query任务
  -> 相同活动问题复用原任务；Worker检索证据并调用DeepSeek
  -> 失败有限重试，已完成回答重放时跳过模型调用
  -> 用户通过 GET /papers 查看进度
```

## 运行时数据

以下内容由程序运行时生成，应由 `.gitignore` 排除，不要提交到 Git：

```text
downloads/
downloads/metadata.db
downloads/langgraph_checkpoints.db
downloads/papers/
downloads/exports/
.venv/
.pytest_cache/
__pycache__/
```

本机Eval存储约定：新增的冻结案例、临时数据库、模型原始输出和不准备提交Git的评测脚本统一放在`E:\PaperHunterData\smolagent-tests\`。仓库中的`tests/`、`benchmarks/`、`outputs/`及历史held-out运行目录通过Junction保持Git和旧代码路径兼容，但实际文件不得重复保存在C盘；未来Eval默认也使用该E盘根目录。Git跟踪的项目脚本继续保留在仓库，因为部分脚本会按自身路径推导项目根目录。

## 常用命令

启动项目：

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

浏览器访问：

```text
http://127.0.0.1:8001/
```

运行测试：

```powershell
uv run pytest
```

提交并上传后续修改：

```powershell
git add .
git commit -m "说明本次修改"
git push
```

## Git 历史背景

- 已初始化 Git 仓库并配置 `.gitignore`。
- 初始提交：`008d774 Initial commit`。
- README 重写提交：`b22e7e8 Rewrite README for Paper Hunter`。
- 项目记忆提交：`2104e47 Add project memory for future agents`。
- 出版商展示功能提交：`4d1ffaa Show publisher for searched papers`。
- 远程仓库名为 `origin`，主分支为 `main`。
- 此前 GitHub 连接失败时配置过本机代理 `127.0.0.1:7890`，之后用户确认推送成功。

## 已加入的出版商功能

- arXiv 记录使用 `publisher = "arXiv"`。
- Crossref 记录读取返回数据中的 `publisher` 字段，例如 `Elsevier BV` 或 Springer 旗下出版商名称。
- SQLite 的 `papers` 表包含 `publisher` 字段，`init_db()` 会为旧数据库补充该字段。
- 检索结果、历史列表和论文详情页都会展示出版商。
- 数据源没有提供出版商时，页面可能显示“未知”；这并不一定表示论文没有出版商。

## 后续讨论形成的方向

- 文献发现部分是 arXiv 与 Crossref 的简单多来源召回；论文阅读部分已实现传统 RAG，但不是 GraphRAG。
- 建议先加入 DOI 识别、OpenAlex、Unpaywall、去重、结果融合和重排序，再扩展传统 RAG。
- 传统 RAG 可增加 PDF 解析、分块、向量化、向量数据库以及带引用回答；GraphRAG 可作为更后期的高级功能。
- 若部署为公网网站，还需要服务器、域名与 HTTPS、生产数据库、对象存储、任务队列、用户与安全限流、日志监控及容器化。

## RAG 第一阶段新增内容

- `pdf_service.normalize_pdf_text()`：清理 PDF 提取文本。
- `pdf_service.split_page_text()`：按页生成带重叠区域的文本块。
- `pdf_service.extract_pdf_chunks()`：通过 `pypdf` 提取全文并保留页码。
- `pdf_service.parse_paper_pdf()`：后台解析单篇论文，并隔离解析异常。
- `rag_service.paper_access_url()`：返回论文网页或 DOI 地址。
- `rag_service.build_access_notice()`：没有全文时生成购买、机构订阅和合法开放版本提示。
- `rag_service.build_rag_prompt()`：组合问题、论文信息、页码和证据片段。
- `RAG_SYSTEM_PROMPT`：禁止模型编造全文内容、假装读过付费论文或提供绕过版权限制的方法。
- 新增 `paper_documents` 表保存 `not_started`、`parsing`、`parsed`、`parse_failed` 状态、页数、文本块数量和错误。
- 新增 `paper_chunks` 表保存论文 ID、页码、文本块编号和正文。
- `POST /papers/{paper_id}/parse`最初通过`BackgroundTasks`执行；从B1.3起改为持久`parse_paper`任务。
- 论文详情页可启动解析、显示进度、错误和文本块预览；没有本地 PDF 时显示合法访问说明和原始链接。
- `db.list_paper_statuses()` 与 `GET /api/papers/statuses` 提供轻量轮询数据。
- `static/status_poll.js` 每 2 秒自动更新下载、解析、索引和问答状态；任务结束时详情页自动重载一次以展示完整结果。
- `papers.downloaded_at` 保存实际下载成功时间，旧已下载记录用原 `updated_at` 近似补全。
- `db.clear_paper_history()` 与 `POST /papers/clear` 清空 SQLite 历史、解析状态和文本块，但保留本地 PDF；页面必须保留二次确认和行为说明。
- arXiv 和 Crossref 明确 PDF 直链的初始状态为 `available`，不得在搜索完成后自动下载。
- `POST /papers/{paper_id}/download` 在用户点击按钮后把状态改为 `downloading` 并启动后台下载。
- `paper_chunks.vector_json` 保存本地 TF-IDF 稀疏向量；`paper_documents` 保存索引状态、索引错误、向量器和索引时间。
- `rag_queries` 保存问题、回答、证据、DeepSeek 模型、状态和错误。
- `model_service.ask_deepseek()` 直接调用 DeepSeek `/chat/completions`，不得引入 OpenAI API。
- `model_service.embed_with_seed()` 根据模型和 `SEED_EMBEDDING_API_MODE` 调用火山方舟 `/api/v3/embeddings` 或 `/api/v3/embeddings/multimodal`；推荐模型为 `doubao-embedding-vision-251215`。
- DeepSeek 配置使用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`DEEPSEEK_THINKING` 和 `DEEPSEEK_TIMEOUT`。
- 主召回为 Seed1.6 稠密语义向量，辅召回为 `local-tfidf-v1`；默认融合权重为语义 `0.75`、关键词 `0.25`。
- Seed 配置使用 `SEED_API_KEY`、`SEED_BASE_URL`、`SEED_EMBEDDING_MODEL`、`SEED_EMBEDDING_API_MODE` 和 `SEED_TIMEOUT`。`embedding-vision` 模型自动使用多模态接口；`ep-...` 接入点需要显式指定模式。缺失或调用失败时必须自动降级到 TF-IDF。
- 原 `agent_service.py` 已合并进 `model_service.py`。数据库、PDF、模型和 RAG 职责不同，不应为了减少文件数量继续强行合并。

## v0.6.0 论文章节感知分块

- 新增 `CHUNK_STRATEGY_ACADEMIC = "academic_section"`，保留 `length_boundary` 作为默认策略和历史基线。
- `pdf_service.detect_section_heading()` 识别阿拉伯数字、多级数字、罗马数字、IEEE 字母小节与 `A.1` 附录编号，并拒绝表格数字、正文句子和编号操作步骤等常见误判。
- `_merge_wrapped_heading_lines()` 合并 PDF 提取造成的拆行标题，例如 `II. M` 与 `ATERIALS AND METHODS`。
- `split_academic_page_text()` 跨页继承父子章节路径；每个正文块以 `[Section: 父章节 > 子章节]` 开头，但仍在物理页内按 `chunk_size/overlap` 生成子块，保证证据页码可评测。
- 进入 `References`、`Bibliography`、`Acknowledgment(s)` 后停止索引；遇到明确 `A. Appendix` 后恢复，避免参考文献作者名误判为小节，同时保留附录参数证据。
- `extract_pdf_chunks()`、`parse_paper_pdf()` 新增 `strategy` 参数；普通详情页解析不传参时仍使用 `length_boundary`，避免隐式改变现有行为。
- `ExperimentConfig.chunk_strategy`、`create_experiment_run()`、`run_configured_experiment()` 和 `/evaluation/run` 已贯通策略选择；评测指标额外保存 `detected_section_count`。
- 评测页可选择 `length_boundary` 或 `academic_section`，切换时自动更新实验名称，运行详情显示唯一章节路径数。
- 真实初版章节实验 `#3` 生成 `132` 块、识别 `40` 条路径，发现参考文献锁错误过滤 AOD 附录；该失败记录保留用于诊断回放。
- 修正版章节实验 `#4` 生成 `148` 块、识别 `44` 条路径，`Hit@1=60.00%`、`Hit@3=80.00%`、`Hit@5=80.00%`、`MRR@5=0.6889`、覆盖率 `83.30%`。
- 固定边界 `1200/150` 基线仍更好：`Hit@1=66.67%`、`Hit@5=93.33%`、`MRR@5=0.7667`、覆盖率 `91.49%`。本次结论是结构化分块本身不等于更好的排序，下一步应加入跨语言查询扩展或重排序器。
- 新增章节编号、跨行标题、跨页层级、参考文献锁和附录恢复测试；完整测试为 `32 passed`。

## v0.6.1 基准数据扩展

- `benchmarks/chunking_baseline.csv` 已从论文 1 至 3 的 15 题扩展到论文 1 至 12 的 60 题。
- 新增论文 4 至 12 共 9 篇、45 题，每篇严格包含事实型、方法型、参数型、结果型和原因型各 1 题。
- 外部标注中的 `paper_id` 仅用于整理，写入公共 CSV 时必须转换为 `paper_title`，保持 `import_benchmark_cases()` 接口兼容。
- 12 篇本地 PDF 均已按正式标题匹配；包括 AODE、AVWAODE、SWAODE 及两篇原中文文件名论文，当前文件名已可被 `find_local_pdf()` 正确关联。
- 新增 45 条 `evidence_text` 已使用 `pypdf` 在对应 `evidence_page` 逐条验证，最低 token 覆盖率为 `0.8333`，全部高于 `EVIDENCE_MATCH_THRESHOLD=0.65`。
- 本地 SQLite 已导入 12 篇论文和 60 条案例，并完成固定边界运行 `#5` 与章节感知运行 `#6`；不要把 `v0.6.0` 的 15 题指标当作 60 题结果。
- 新增数据集平衡性测试；完整测试为 `33 passed`。
- 两次实验参数均为 `chunk_size=1200`、`overlap=150`、`top_k=5`、Seed 权重 `0.75`、TF-IDF 权重 `0.25`，所有论文均使用 Seed + TF-IDF 混合索引且无错误。
- 固定边界 `#5`：`785` 块，`Hit@1=43.33%`、`Hit@3=66.67%`、`Hit@5=81.67%`、`MRR@5=0.5747`、覆盖率 `79.25%`。
- 章节感知 `#6`：`794` 块、`195` 条章节路径，`Hit@1=50.00%`、`Hit@3=66.67%`、`Hit@5=75.00%`、`MRR@5=0.5950`、覆盖率 `74.34%`。
- 章节策略逐题排名改善 `17` 题、变差 `12` 题、持平 `31` 题；Top-5 新增 `3` 题、丢失 `7` 题。丢失题的证据仍完整存在于章节语料，因此属于排序变化。
- 当前结论：章节分块提高首位精度，尤其改善方法型与参数型，但尚未稳定提高召回广度；默认策略仍保留 `length_boundary`，下一步测试查询扩展或重排序。

## v0.7.0 实验增量缓存

- 新增 `pdf_text_cache`：以 PDF SHA-256 `source_hash` 和物理页码为主键保存页文本；切换分块策略时不得再次调用 `pypdf` 提取相同文件。
- 新增 `embedding_cache`：以 `cache_namespace + content_hash` 为主键保存 Seed 向量；命名空间包含服务地址、模型和文本/多模态接口模式，不得跨模型误用向量。
- `paper_documents` 新增 `source_hash`、`chunk_strategy`、`chunk_size`、`chunk_overlap` 和 `embedding_namespace`，用于判断当前分块与索引是否可直接复用。
- `pdf_service.pdf_source_hash()` 计算文件指纹；`extract_pdf_pages()` 与 `build_pdf_chunks()` 分离昂贵的文本提取和可重复分块；`parse_paper_pdf()` 返回缓存状态。
- `rag_service.embedding_content_hash()` 生成文本缓存键；`cache_active_chunk_embeddings()` 将升级前已有的稠密向量回填；块向量与问题向量均通过 `_cached_seed_embeddings()` 去重和复用。
- `rag_service.document_index_is_reusable()` 校验当前索引状态与 Seed 命名空间；只有 PDF/分块/模型发生相关变化时才重建对应层。
- `run_configured_experiment()` 的 `metrics_json` 新增 `cache_stats`、`preparation_seconds`、`evaluation_seconds` 和 `total_duration_seconds`；评测页展示缓存命中/新增数量和总耗时。
- 缓存失效规则：只改 Top-K 或融合权重时直接复用分块、索引和问题向量；改分块策略/大小/重叠时复用页文本和问题向量，仅补算新块；改 PDF 或 Seed 模型时失效对应缓存。
- 当前缓存功能自动化测试为 `35 passed`；正式检索指标仍沿用 `v0.6.1` 的运行 `#5/#6`，缓存只改变计算路径，不改变算法得分。
- 真实预热运行 `#7`：211 页首次写入、794 个旧块向量回填并命中、60 个问题向量首次生成，总耗时 `105.876` 秒。
- 全缓存运行 `#8`：794 个分块、12 篇论文索引和 60 个问题向量全部命中，Seed 请求为 0，总耗时 `4.406` 秒，约快 `24.0` 倍；指标与 `#7` 完全一致。
- `#7` 与旧 `#6` 的 Hit@1/MRR 有轻微差异，因为旧版未持久化问题向量，预热时重新请求了 Seed；从 `#7` 开始问题向量被冻结缓存，后续实验应以同一缓存做公平比较。
- C0 缓存步骤已完成；E2 章节前缀消融最初按要求暂不单独提交，现已随 `v0.8.1` 统一归档。
- 后续实验优先级已转回论文分块主线：C1完整句子边界与C2公平固定边界已完成；下一步测试小子块检索、大父块返回，再按顺序测试表格感知和语义断点。RRF与Top-20重排暂缓。Late Chunking依赖token级长上下文向量，当前Seed API不支持；RAPTOR与原子命题索引暂不适合当前单段证据基准。

## v0.8.0 E2 章节前缀消融

- 新增 `academic_section_no_prefix` 实验策略；它与 `academic_section` 调用同一个章节分块函数，只在 Seed 和 TF-IDF 索引时通过 `rag_service.chunk_index_text()` 移除 `[Section: ...]` 首行。
- 12 篇论文共 794 个块的逐块核对结果为 `mismatched_papers=[]`，证明两组块内容、页码和顺序相同，唯一变量是索引文本是否保留章节路径。
- 有前缀组采用全缓存运行 `#8`：`Hit@1/3/5=51.67%/66.67%/75.00%`、`MRR@5=0.6033`、覆盖率 `74.34%`。
- 无前缀组运行 `#9/#10` 指标完全一致：`Hit@1/3/5=43.33%/63.33%/66.67%`、`MRR@5=0.5389`、覆盖率 `65.47%`。
- 前缀使 Hit@5 提高 `8.33` 个百分点、MRR 提高 `0.0644`（约 `11.95%`）、覆盖率提高 `8.87` 个百分点；14 题排名改善、4 题变差、42 题不变，Top-5 净增加 5 题。
- E2 结论：正式保留章节路径前缀，并将“论文结构感知上下文前缀 + 受控消融验证”作为项目亮点；不得声称章节感知整体超过固定边界，因为 E0 Hit@5 仍为 `81.67%`。
- E2 首次运行 `#9` 用时 `950.851` 秒；全缓存复跑 `#10` 用时 `4.749` 秒；完整自动化测试为 `37 passed`。
- 运行 `#11` 已用缓存恢复 `academic_section` 有前缀索引，指标与 `#8` 完全一致；当前 SQLite 不再停留在无前缀对照状态。
- E2 已整理为 `v0.8.0` 正式功能，版本号、README、PROJECT、CHANGELOG、EXPERIMENTS 和测试保持一致，并已进入后续正式发布历史。

## v0.8.1 已归档 E3a 固定英文检索问题消融

- 新增 `benchmarks/retrieval_queries_en.csv`，冻结 60 条英文等价检索问题；原始基准问题和答案不变，离线校验确认一一对应且没有中文残留。
- `evaluation_runs.query_mode` 记录 `original` 或 `english`；`ExperimentConfig`、评测路由、服务和页面已贯通该字段。
- 英文模式只替换送入 Seed 与 TF-IDF 的检索问题，不修改分块、索引、权重、Top-K、证据匹配或 DeepSeek Prompt。
- 中文对照使用 `#11`；英文首次运行 `#12` 与全缓存复跑 `#13` 指标完全一致。
- 英文 `#13`：`Hit@1/3/5=50.00%/71.67%/80.00%`、`MRR@5=0.6108`、覆盖率 `78.20%`；相对中文 `#11` 的变化为 `-1.67/+5.00/+5.00` 个百分点、MRR `+0.0075`、覆盖率 `+3.86` 个百分点。
- 逐题排名改善 13 题、变差 8 题、不变 39 题；Top-5 新增 4 题、丢失 1 题。参数型收益最明显，方法型 MRR 下降。
- `#12` 首次生成 60 个英文问题向量，总耗时 `86.681` 秒；`#13` 全缓存复跑总耗时 `4.365` 秒。
- 当前完整离线测试为 `40 passed`。实验细节已写入 `EXPERIMENTS.md`；尚未决定正式采用，因此不升级版本、不更新正式 `CHANGELOG.md`，不 Git 暂存、提交或上传。

## v0.8.1 已归档 E3b BM25 关键词检索器消融

- 新增 `tfidf` 与 `bm25` 两种关键词检索器配置；普通问答和实验默认仍为 TF-IDF。
- `_bm25_keyword_scores()` 使用标准 `k1=1.5`、`b=0.75`，并在每篇论文候选块范围内归一化到 `0–1` 后与 Seed 分数融合。
- `evaluation_runs.keyword_retriever`、`ExperimentConfig`、评测路由、服务和页面已贯通实际检索器配置。
- 对照固定为 E3a 英文 TF-IDF `#13`；BM25 运行 `#14/#15` 指标完全一致。
- BM25 `#15`：`Hit@1/3/5=50.00%/66.67%/78.33%`、`MRR@5=0.5981`、覆盖率 `75.88%`；相对 TF-IDF `#13`，Hit@3/5 分别下降 `5.00/1.67` 个百分点、MRR 下降 `0.0127`、覆盖率下降 `2.32` 个百分点。
- 逐题排名改善 7 题、变差 14 题、不变 39 题；Top-5 新增 2 题、丢失 3 题。3 道丢失题全部为方法型。
- 当前完整离线测试为 `43 passed`；两个真实运行均全缓存、无新增 Seed 请求，总耗时分别为 `4.223` 和 `4.384` 秒。
- E3b 结论：不采用 BM25 全面替换 TF-IDF；BM25 仅保留为实验选项。该轮没有单独升级版本，现已随 `v0.8.1` 统一归档。

## v0.8.1 已归档 C1 论文章节完整句子分块

- 新增 `paper_section_sentence`：复用现有章节识别、跨页继承、参考文献过滤、附录恢复和章节前缀，只把章节内部切分改为完整句子累积与完整句子重叠。
- `split_sentence_aligned_text()` 保护常见学术缩写；无标点表格/公式单元超过目标长度时安全降级到边界切分。
- 首次预检发现28个超过1800字符的块、最大4956字符；修正后12篇论文共884块，平均899.45字符、最大1200、无空块和超长块。
- 对照使用固定英文 + TF-IDF 的章节字符边界 `#13`；新策略运行 `#16/#17` 指标完全一致。
- 新策略 `#17`：`Hit@1/3/5=55.00%/70.00%/81.67%`、`MRR@5=0.6442`、覆盖率 `80.07%`；相对 `#13`，Hit@1/5 分别提高 `5.00/1.67` 个百分点，MRR提高 `0.0334`，覆盖率提高 `1.87` 个百分点，Hit@3下降 `1.67` 个百分点。
- 逐题排名改善10题、变差6题、不变44题；Top-5新增2题、丢失1题。
- `#16` 首次生成873个新块向量，总耗时 `1120.328` 秒；`#17` 全缓存复跑总耗时 `4.649` 秒。
- 当前完整离线测试为 `48 passed`。C1 保留为候选策略，但尚未与相同英文查询配置下的 `length_boundary` 做公平对照，因此不能声称全面最优。
- C1 当时未单独升级版本或提交；现已随 `v0.8.1` 归档。后续实验先完成固定边界公平基线，再进入父子块。

## v0.8.1 已归档 C2 固定边界英文公平基线

- 固定配置为12篇/60题、固定英文、TF-IDF、Seed/关键词权重 `0.75/0.25`、Top-5、`1200/150`；只比较 `length_boundary` 与 `paper_section_sentence`。
- `#18` 因Seed SSL中断导致部分论文静默降级为TF-IDF-only，已标记 `failed`，不得用于正式结论；`#19` 在修复索引时被本地120秒命令上限中止，也已标记 `failed`。
- `run_configured_experiment()` 新增实验索引一致性校验：普通问答仍可降级，但受控评测若索引与Seed命名空间不一致必须失败。
- 严格固定边界运行 `#20/#21` 指标完全一致：`Hit@1/3/5=46.67%/66.67%/88.33%`、`MRR@5=0.6036`、覆盖率 `85.96%`。
- 自定义句子策略 `#17`：`Hit@1/3/5=55.00%/70.00%/81.67%`、`MRR@5=0.6442`、覆盖率 `80.07%`。
- 句子策略相对固定边界：Hit@1多5题、Hit@3多2题、MRR提高 `0.0406`；Hit@5少4题、覆盖率低 `5.89` 个百分点。逐题排名改善17题、变差10题、不变33题；Top-5新增4题、丢失8题。
- C2结论：句子策略首位排序更强，固定边界召回广度更强，不能直接替换。下一步父子块要用小块定位、父块返回组合两种优势。
- 当前完整离线测试为 `49 passed`；C2有效复跑均为全缓存约5秒。
- 当前12篇/60题足以作为个人项目的小规模人工评测集，但不得称为大规模基准；总体每题对应1.67个百分点，分题型每题对应8.33个百分点。
- C2 当时未单独升级版本或提交，现已随 `v0.8.1` 归档。

## v0.8.1 已归档 C3 小子块检索、大父块返回

- 新增 `paper_parent_child`：父块复用固定边界 `1200/150`，每个父块内部按完整句子生成 `600/75` 子块；Seed与TF-IDF只索引子块，命中后返回父块。
- 新增 `PaperParentChunk`、`paper_parent_chunks` 表和 `paper_chunks.parent_chunk_id`；`db.replace_paper_chunks()` 原子替换父子布局并校验父引用。
- `build_parent_child_chunks()` 生成层次布局；`build_pdf_chunk_layout()` 同时返回索引子块和父块，原有策略行为不变。
- `retrieve_relevant_chunks()` 保留 `retrieval_content`/`child_chunk_index`，按子块评分、按 `parent_chunk_id` 去重，Top-K表示不同父块。
- 评测运行记录父块数、子块数和实际子块参数；页面可选择 `paper_parent_child`，逐题详情区分命中子块与返回父块。
- 12篇论文预检得到785个父块、2196个子块、2191个唯一子块文本，最长600字符，无空块。
- 首次运行 `#22` 总耗时 `2512.665` 秒，首次生成2191个子块向量；`#23/#24` 全缓存复跑指标一致，最终 `#24` 用时 `8.873` 秒，约加速283.2倍。
- `#24`：`Hit@1/3/5=48.33%/76.67%/85.00%`、`MRR@5=0.6322`、覆盖率 `81.65%`，错误为0。
- 相对固定边界 `#21`，Hit@3提高10个百分点、MRR提高0.0286，但Hit@5下降3.33个百分点、覆盖率下降4.31个百分点；18题排名改善、12题变差、30题不变。
- 相对章节句子 `#17`，Hit@3/5分别提高6.67/3.33个百分点，Hit@1下降6.67个百分点、MRR下降0.0120。
- C3结论：父子块取得当前最高Hit@3并呈现更均衡的前三名召回，但未全面超过固定边界或章节句子，不设为默认策略；下一步单独测试表格感知。
- C3 完成时完整离线测试为 `51 passed`；该轮未单独升级版本或提交，现已随 `v0.8.1` 归档。

## v0.8.1 已归档 C4 表题与表头上下文增强

- 新增 `paper_table_context`：保持固定边界正文、页码、顺序和785个块数量不变，只把可信表题、说明和表头作为上下文前缀加入表格后续块。
- 真实211页预检找到97个Table候选行；过滤 `Table 4 gives...` 等正文引用后，运行中识别77个表格上下文、影响202个块。
- `is_table_caption()` 识别数字/罗马数字表题、全大写表题和续表；`extract_table_contexts()` 限制上下文为420字符/4行；`split_table_context_page_text()` 保留完整原文。
- 评测指标新增 `detected_table_count` 和 `table_context_chunk_count`；页面可选择策略并展示识别数量。
- 首次运行 `#25` 复用583个固定块向量、生成202个新向量，总耗时 `314.458` 秒；全缓存 `#26` 用时 `4.547` 秒，指标完全一致。
- `#26`：`Hit@1/3/5=50.00%/71.67%/85.00%`、`MRR@5=0.6300`、覆盖率 `82.22%`。
- 相对固定边界，Hit@1/3和MRR提高，但Hit@5下降3.33个百分点、覆盖率下降3.74个百分点；8题改善、6题变差、46题不变。
- C4没有新增Top-5命中，反而让PWODE和SWAODE两道方法题掉出Top-5；当前60题又缺少直接表格单元格问题，因此不设为默认，只保留实验选项。
- C4 完成时完整离线测试为 `53 passed`；该轮未单独升级版本或提交，现已随 `v0.8.1` 归档，下一步为 C5 语义断点。

## 用户偏好和维护约定

- 解释应面向新手，同时保留可用于面试和简历的技术细节。
- 学习过程中的检查题应以真实面试官会追问的“为什么这样设计、替代方案的代价、失败后会怎样、需求变化如何影响流程”为主，并根据刚学完的概念校准难度；除非用户明确要求语法练习，不要用单行变量取值、明显状态识别等低级记忆题。先完成项目化讲解，再进行简短检查，不要把整节课变成高压模拟面试。
- 优先让项目稳定、合规、可演示，再逐步增加复杂能力。
- 修改项目记忆时继续使用仓库根目录的 `AGENTS.md`。
- 命令、路径、接口、字段名和代码标识符应保持原样，不要为了中文化而翻译它们。
- 每次增加或修改项目功能时，必须在同一次更新中同步维护 Markdown 文档，准确记录新增模块、函数、接口、数据库字段或表、状态、测试和使用方法。
- 每次功能更新都必须确定新的语义化版本号，并在相关 Markdown 文档顶部更新“当前版本/适用版本”和“更新日期”。
- 每次功能更新都必须在 `CHANGELOG.md` 顶部新增一条记录，写明日期、版本、新增功能、修改内容、测试结果和仍未实现的范围。
- 每次实验必须同步更新 `EXPERIMENTS.md`，记录假设、唯一变量、固定参数、代码改动、运行编号、缓存统计、指标、异常、结论和未完成项；一次只完成一个实验变量，得到用户确认后再进入下一项。
- `pyproject.toml` 与 FastAPI 应用中的版本号必须和文档当前版本保持一致。
- 早期没有可靠日期的历史记录应明确写“日期未记录”，不得猜测日期。

## v0.8.1 已归档 C5 Seed 语义断点分块

- 新增历史局部阈值策略 `paper_semantic_breakpoint` 与修正版 `paper_semantic_breakpoint_global`；页面只展示修正版，旧策略保留用于复现 `#27/#28`。
- `collect_semantic_segments()` 在论文章节内生成最长300字符的完整句子单元；`embed_semantic_units()` 使用现有 Seed 内容哈希缓存生成单元向量。
- `_vector_cosine_similarity()`、`semantic_breakpoint_threshold()` 和 `merge_semantic_units()` 根据相邻单元余弦相似度 P25 选择断点，同时保留最小正文、1200字符硬上限和完整单元重叠。
- `build_semantic_pdf_chunks()` 修正版先计算整篇论文统一阈值，再在各章节内部执行断点；不得退回每个页内短片段各算 P25 的做法。
- `paper_documents.layout_stats_json` 保存语义单元数、断点数、阈值范围与阈值；布局缓存命中后仍可恢复统计。
- 评测记录新增 `semantic_unit_count`、`semantic_break_count`、`semantic_threshold_scope`、`mean_semantic_threshold` 和语义单元向量缓存统计；页面可查看这些数据。
- 局部阈值 `#27/#28`：2775单元、522断点、1090块，`Hit@1/3/5=46.67%/66.67%/75.00%`、`MRR=0.5747`、覆盖率`74.27%`；首跑`5206.261`秒，全缓存`7.382`秒。
- 全局阈值 `#29/#30`：2775单元、433断点、1069块，平均阈值`0.602998`，`Hit@1/3/5=50.00%/71.67%/80.00%`、`MRR=0.6156`、覆盖率`78.69%`；复用全部语义单元向量后首跑`561.029`秒，全缓存`4.953`秒。
- 全局阈值相对局部阈值使12题排名改善、5题变差，Top-5净增加3题；但相对固定边界Hit@5低8.33个百分点、覆盖率低7.27个百分点。
- C5结论：全局阈值修正有效，但没有超过固定边界、章节句子或父子块的主要优势指标，不设为默认；不要在当前60题上继续调P10/P20/P30，避免测试集泄漏。
- 本轮统一最优项仍分散：C1最高Hit@1/MRR，C3最高Hit@3，固定边界最高Hit@5/覆盖率；普通解析默认继续使用`length_boundary`。
- C5结束后用运行`#31`恢复固定边界当前索引；785个块向量全部命中缓存，耗时`6.087`秒，指标与公平基线`#21`完全一致。
- C5 完成时完整离线测试为 `56 passed`；该轮未单独升级版本或提交，现已随 `v0.8.1` 归档。完整细节见 `EXPERIMENTS.md`。
- 下一步离开单路分块调参，优先测试多路候选融合/重排序，并增加Faithfulness、答案相关性和引用正确性生成评测。

## v0.8.1 已归档 E3c 中英文双路 RRF 融合

- 新增 `rag_service.reciprocal_rank_fusion()`：按候选块或父块 ID 去重，使用 `1/(60+rank)` 合并多路名次。
- 新增 `retrieve_bilingual_rrf_chunks()`：中文原题与冻结英文题各召回 Top-20，再融合返回 Top-5；不直接相加两路原始分数。
- 新增 `QUERY_MODE_BILINGUAL_RRF = "bilingual_rrf"`；评测配置、服务和页面已贯通，逐题结果保存 `fusion_ranks`。
- 固定配置为 12 篇/60 题、固定边界 `1200/150`、TF-IDF、Seed/关键词权重 `0.75/0.25`、Top-5；对照为固定英文 `#21`。
- `#32/#33` 指标一致：`Hit@1/3/5=41.67%/71.67%/85.00%`、`MRR=0.5808`、覆盖率 `81.09%`。
- 相对 `#21`，Hit@3 提高 5 个百分点，但 Hit@1/5 分别下降 5/3.33 个百分点，MRR下降 `0.0228`，覆盖率下降 4.87 个百分点。
- 11题排名改善、16题变差、33题不变；Top-5新增1题、丢失3题。RRF偏爱两路共同候选，可能压低只在英文路高排名的正确证据。
- 两次运行均为120个问题向量命中、0新增；总耗时分别为`8.368`和`7.328`秒，无错误、无DeepSeek调用。
- E3c不设为默认，不继续在当前60题上调RRF常数或候选池；下一步测试固定英文Top-20重排。
- 当前完整离线测试为`58 passed`；E3c不升级版本、不更新CHANGELOG、不Git暂存、提交或上传。完整细节见`EXPERIMENTS.md`。

## v0.8.1 已归档 E4 DeepSeek Top-20 列表重排

- 新增 `model_service.ask_deepseek_json()`：使用 JSON Output、`temperature=0`、关闭思考模式，并校验 JSON 对象。
- 新增 `llm_response_cache`，按服务地址、模型、Prompt版本和输入哈希缓存合法重排响应；`evaluation_runs.reranker` 记录 `none` 或 `deepseek_listwise`。
- 新增 `RERANK_SYSTEM_PROMPT`、`_rerank_prompt()`、`_validated_rerank_ids()` 和 `rerank_candidates_with_deepseek()`；模型必须返回全部候选ID且不得看到参考答案、证据页或标准证据。
- 固定配置为12篇/60题、固定边界`1200/150`、固定英文、TF-IDF、Seed/关键词`0.75/0.25`、初召回Top-20、最终Top-5；对照为`#21`。
- `#34/#35`指标一致：`Hit@1/3/5=73.33%/93.33%/98.33%`、`MRR=0.8403`、覆盖率`93.94%`、错误0。
- 相对`#21`，Hit@1/3均提高26.66个百分点，Hit@5提高10个百分点，MRR提高`0.2367`（约39.22%），覆盖率提高7.98个百分点。
- 29题排名改善、4题变差、27题不变；Top-5新增7题、丢失1题。唯一丢失的SWAODE方法题由初召回第4名降至重排第8名。
- 首次运行59次新DeepSeek请求、1次冒烟缓存命中，总耗时`277.475`秒；全缓存复跑60次命中、0新增，总耗时`4.809`秒。
- E4是当前提升最大的候选与简历亮点，但暂不修改普通RAG默认链路；先完成G1生成评测框架与G2端到端对照。
- 当前完整离线测试为`63 passed`；E4不升级版本、不更新CHANGELOG、不Git暂存、提交或上传。完整细节见`EXPERIMENTS.md`。

## v0.8.1 已归档 G1 RAG 生成质量评测

- 新增 `generation_evaluation_service.py`，冻结 `paper-answer-v1` 生成 Prompt 与 `paper-quality-judge-v1` 裁判 Prompt；检索证据只读取目标检索运行已经保存的 Top-5，不得在生成阶段重新召回。
- 生成质量包含四项：Faithfulness（声明受证据支持比例）、Answer relevance（回答问题的直接性与完整性）、Reference correctness（与人工参考答案的一致性和覆盖）、Citation correctness（声明拥有完整有效页码引用的比例）。
- `extract_citation_pages()` 解析中英文页码；`validate_judge_response()` 严格校验原子声明、显式引用和证据页集合。Faithfulness 与 Citation correctness 由本地代码根据声明计算，不接受模型直接给总分。
- `_cached_generation()` 与 `_cached_judgment()` 复用 `llm_response_cache`，命名空间包含模型与 Prompt 版本；`evaluate_generation_case()`、`create_generation_run()`、`run_generation_evaluation()` 分别负责单题、建运行和后台批量评测。
- 新增 `generation_evaluation_runs`、`generation_evaluation_results` 表；前者保存检索来源、模型、Prompt、总指标和状态，后者保存逐题答案、Top-5、裁判声明、四项得分、耗时和错误。
- 新增 `GET /generation-evaluation`、`POST /generation-evaluation/run`、`GET /generation-evaluation/runs/{run_id}` 与生成评测页面；后台运行期间复用轮询脚本自动刷新。
- 单题真实冒烟使用检索运行 `#35` 的第一个事实题：`Faithfulness=1.0`、`Answer relevance=0.9`、`Reference correctness=0.0`、`Citation correctness=0.3333`；首次生成/裁判分别为`10.368/6.939`秒，缓存复跑为`0.002/0.003`秒。
- 冒烟反例显示检索虽记为 Hit@1，但固定边界把标准句截断在 `fine particula`，日期和站点数不在返回块；模型忠实拒答而没有得到参考事实。不得把 Hit@K 直接等同于端到端答案正确率。
- 当前完整离线测试为`69 passed`。G1只完成框架与一题真实冒烟，未创建完整60题生成运行；下一步G2固定模型和Prompt，对比`#21`不重排与`#35`重排的60题答案质量、错误、延迟和缓存。
- G1不升级版本、不更新CHANGELOG、不Git暂存、提交或上传。完整细节见`EXPERIMENTS.md`。

## v0.8.1 已归档 G2 端到端生成对照

- 唯一变量是 Top-5 证据：基线读取固定英文初召回 `#21`，对照读取 DeepSeek 重排 `#35`；60 道中文问题、DeepSeek 模型、`paper-answer-v1`、`paper-quality-judge-v1` 和评分规则全部固定。
- 批量运行发现 `第 5、9–10 页` 被旧单页正则误判；`CITATION_GROUP_PATTERNS` 与 `_expand_page_group()` 已支持中英文多页列表和页码范围，并用 `MAX_CITATION_RANGE=200` 限制异常展开。完整离线测试保持`69 passed`。
- 基线生成运行`#1`初始有2次网络中断和1次多页引用误判，只重试失败题后达到60/60、错误0；包含重试总耗时`1234.958`秒。
- 重排生成运行`#2`为60/60、错误0；生成和裁判各1次缓存命中、59次新请求，总耗时`1107.339`秒。两组都为单进程顺序执行，但`#2`有一题G1预热，冷启动耗时不得做严格速度结论。
- 全缓存复跑基线`#3`用时`0.851`秒、重排`#4`用时`0.831`秒；两组均为生成60命中、裁判60命中，质量指标与首次最终结果完全一致。
- 基线`#3`：Faithfulness/Answer relevance/Reference correctness/Citation correctness=`97.81%/92.00%/81.17%/92.19%`。
- 重排`#4`：四项指标=`97.50%/94.33%/86.33%/91.15%`；变化为`-0.31/+2.33/+5.17/-1.04`个百分点。
- Reference correctness逐题改善11题、变差7题、不变42题；E4新增Top-5命中的7题平均提高`0.2286`。参数型Reference提高11.67个百分点，结果型提高9.17个百分点，原因型下降1.67个百分点。
- 10,000次固定种子配对bootstrap：Reference correctness平均变化`+0.0517`，95%区间`[0.0033,0.1100]`；另外三项区间均跨0，不得声称稳定变化。
- G2结论：E4检索提升能够传导到最终参考答案正确性；Faithfulness基本不变，说明“忠实拒答”仍可能没有答出标准事实；Citation correctness未同步提高，下一步需单独做逐句引用实验。
- 当前生成器与裁判均为同一个DeepSeek模型，存在同源偏差；12篇/60题只能称为个人项目小规模人工评测。
- 下一步G3人工抽查变化最大的成功/失败案例，再决定是否调整生成Prompt或把重排接入普通问答；G2不升级版本、不更新CHANGELOG、不Git暂存、提交或上传。完整细节见`EXPERIMENTS.md`。

## 独立未见测试集 heldout_v1

- 现有12篇论文、60题已反复用于开发，统一定位为开发集；不得再把它们单独用于无偏泛化结论。
- 新增`benchmarks/heldout_v1_manifest.csv`保存论文标题、领域、开放来源、PDF地址、本地文件名、下载状态和标注状态；新增`benchmarks/heldout_v1.csv`保存最终40道问答与页码级连续原文证据。
- `HELDOUT_DATASET.md`冻结测试目的、基线/候选配置、标注规则、替换记录和当前进度；新对话应先读该文件，不得重新选题或遗失已完成标注。
- 清单包含8篇此前未参与开发的开放论文，覆盖机器人、地球系统、时间序列、蛋白质、材料发现、医疗多模态、程序推理和量子机器学习；每篇固定标注事实/方法/参数/结果/原因各1题，共40题。
- H06原计划使用PMC11519755，但官方资源在当前网络环境无法稳定复现下载；在任何标注和测试发生前，已替换为arXiv 2408.03361 `GMAI-MMBench`，替换不依据检索结果。
- 8篇PDF均已保存到被Git忽略的`downloads/heldout_v1/`并通过`%PDF-`文件头检查；PDF不得提交。用户已于`2026-08-01`明确授权读取这些PDF用于制作40题。H01-H08共40题已完成并冻结，40条证据均通过对应物理页的连续原文校验。
- 冻结集包含40个唯一问题、8篇唯一论文，每篇恰好5题且五类题型各8题；与12篇开发集的论文标题重叠为0。
- `benchmarks/heldout_v1.csv`冻结SHA-256为`B9724E71F74966B03B15E396597BF70B49C097D07A2A5AF83CF3FD0FCE5B60BB`；正式测试前必须复核，H1结束后不得修改问题、答案、页码或证据。
- H2新增`benchmarks/heldout_v1_queries_en.csv`保存40条冻结英文检索问题，SHA-256为`B60042FE7E9578CCD4C67711ED50F25DF0B617F4CA631FEE482A09E2E46B9819`；它与中文问题一一对应且无缺失、重复或多余项。
- H2隔离库基线检索运行`#1`已完成：8篇/373页/1150块，`Hit@1/3/5=50.00%/67.50%/85.00%`、`MRR=0.6204`、覆盖率`85.01%`、错误0，总耗时`1744.916`秒；所有1150个块向量和40个问题向量均为首次生成。
- H2候选重排运行`#2`已完成：复用相同1150块、40个英文查询和Top-20初召回，仅增加冻结DeepSeek列表重排；`Hit@1/3/5=62.50%/90.00%/92.50%`、`MRR=0.7675`、覆盖率`90.17%`、错误0，总耗时`239.118`秒。
- 相对基线，重排使Hit@1/3/5提高`12.50/22.50/7.50`个百分点、MRR提高`0.1471`、覆盖率提高`5.16`个百分点；结果型Hit@1从`0%`提高到`50%`，但方法型Hit@5从`100%`下降到`75%`。检索泛化得到支持，但两组生成与裁判尚未完成，不得提前宣称最终答案质量提升。
- H2检索逐题比较为15题改善、4题变差、21题不变，Top-5新增5题、丢失2题；固定种子42的10,000次配对bootstrap中，Hit@3差值95%区间为`[+5.00,+37.56]`个百分点，MRR差值区间为`[+0.0296,+0.2567]`，两者不跨0；Hit@1/5与覆盖率区间仍跨0。
- H2基线生成运行`#1`曾在17题后因DeepSeek余额不足中断；`2026-08-02`使用相同配置和`--retry`恢复剩余23题，最终40题无错误。正式指标为Faithfulness`1.0000`、Answer relevance`0.9500`、Reference correctness`0.8863`、Citation correctness`0.9688`；恢复过程生成/裁判各命中17次缓存、各新增23次，耗时`438.373`秒。
- H2重排生成运行`#2`首次完成37题，3题因`incomplete chunked read`失败；同配置`--retry`后40题无错误。指标为Faithfulness`0.9808`、Answer relevance`0.9400`、Reference correctness`0.8900`、Citation correctness`0.9662`；恢复轮生成/裁判缓存命中`38/37`次、新增`2/3`次，耗时`73.400`秒。
- 40题生成配对bootstrap使用seed42和10,000次采样：Faithfulness差值`-0.0192`、95%区间`[-0.0550,0]`；Answer relevance差值`-0.0100`、区间`[-0.0325,+0.0075]`；Reference correctness差值`+0.00375`、区间`[-0.0275,+0.0300]`；Citation correctness差值`-0.0025`、区间`[-0.0279,+0.0229]`。四项均不能证明稳定提升。
- H2检索结论有效：外部未见集支持DeepSeek列表重排改善前三名检索排序。原始生成结论后被G3a发现的800字符持久化截断污染，只能描述截断证据管线，修复后必须按原冻结配置复核；不得借机调整重排Prompt、候选池或阈值。
- 冻结对照：基线使用`#21`等价配置，候选使用`#35`等价DeepSeek Top-20到Top-5重排，生成与裁判Prompt继续使用`paper-answer-v1`和`paper-quality-judge-v1`；不得根据held-out结果回调这些配置。
- H1已完成；H2首次冻结对照受G3a发现的证据截断影响，旧生成结果只保留审计。H2-R已使用相同配置完成完整证据复核并关闭`heldout_v1`；若后续修改算法方案，必须另建新的未见测试集。
- 用户已要求论文、问题和证据必须持久化，不能只留在聊天记录；每完成一篇应立即更新CSV、manifest、`HELDOUT_DATASET.md`和本节。

## v0.8.1 已归档 G3a 检索到生成案例诊断

- G3a只读取开发集检索运行`#21/#35`和生成运行`#3/#4`，不修改算法、不调用API；本地诊断脚本与完整报告位于被Git忽略的`downloads/g3_case_diagnostic.py`、`downloads/g3_case_diagnostic.json`和`downloads/g3_case_diagnostic.md`。
- 60题分组结果：标准证据新进入Top-5的7题Reference correctness平均提高`0.2286`；22题仅排名上升时平均提高`0.0636`；27题排名不变时平均提高`0.0074`；3题排名下降时平均降低`0.0333`。
- 21题检索变好但答案未改善，其中15题基线答案已达到Reference correctness 1.0；Case 6和Case 93等反例暴露出评测证据截断。
- 根因：`evaluation_service.run_evaluation()`使用完整块计算命中，但保存`retrieved_json`时只保留`chunk["content"][:800]`；`generation_evaluation_service.run_generation_evaluation()`直接读取该截断内容生成答案，导致检索指标和生成器可见证据不一致。
- 开发集基线有15题命中排名无法由落库证据复现、13题命中完全消失；重排为21题和14题。生成器可见Hit@5实际为`66.67%/75.00%`，而检索报告为`88.33%/98.33%`。
- H2同样受影响：基线/重排分别8/10题排名不一致、各7题命中完全消失；生成器可见Hit@5为`67.50%/75.00%`，而检索报告为`85.00%/92.50%`。
- 普通`rag_service.answer_rag_query()`直接使用完整检索块，不经过评测结果落库，因此该缺陷限定在生成评测管线。下一步需保留旧运行并修复评测证据一致性，再以冻结配置建立新运行；修复前不得调整Prompt或引用策略。

## v0.8.1 生成评测证据一致性修复

- `evaluation_service.run_evaluation()` 不再对逐题落库的 `content` 和 `retrieval_content` 执行 `[:800]` 截断；新建 `evaluation_results.retrieved_json` 保存完整 Top-K 文本块。
- 生成评测仍从对应检索运行读取证据，因此修复后检索命中、证据覆盖和 DeepSeek 生成使用同一份完整块；没有修改分块、召回、重排、生成 Prompt 或裁判 Prompt。
- 本次不需要数据库迁移；旧运行保留审计且不会自动补全，因为被截断的文本尾部没有保存在旧 `retrieved_json` 中。
- 普通网页 RAG 原本直接使用 `retrieve_relevant_chunks()` 返回的完整块，不受该缺陷影响。
- 新增 `test_run_evaluation_persists_complete_evidence_for_generation`，覆盖 800 字符之后的关键证据持久化和 Prompt 可见性；聚焦测试 `6 passed`，完整测试 `70 passed`。
- 代码修复本身未调用外部 API；随后 G2-R 与 H2-R 已按冻结配置完成，不得再根据 `heldout_v1` 修改算法或 Prompt。

## 本地 G2-R 完整证据修正版复核

- `2026-08-02` 按旧 G2 冻结配置建立新检索运行：基线 `#36` 与重排 `#37` 的检索指标分别与旧 `#21/#35` 完全一致，说明 `v0.8.1` 只修复证据持久化，没有改变检索算法。
- `#36/#37` 分别保存263/279个超过800字符的 Top-5 块；按页码和证据覆盖阈值重新计算后，两组60题的 `hit_rank` 均为 `0 mismatch`。
- 修正版生成运行 `#5/#6` 均完成60题且错误0；`#6` 曾有1题 DeepSeek SSL 中断，只重试失败题后恢复。全缓存复跑 `#7/#8` 分别耗时 `0.764/0.775` 秒且指标完全一致。
- 基线 `#7`：Faithfulness `1.0000`、Answer relevance `0.9700`、Reference correctness `0.9367`、Citation correctness `0.9554`。
- 重排 `#8`：Faithfulness `0.9972`、Answer relevance `0.9983`、Reference correctness `0.9667`、Citation correctness `0.9478`。
- 10,000次配对bootstrap中，Answer relevance差值为`+0.0283`、95%区间`[+0.0050,+0.0683]`，是唯一稳定正向指标；Reference correctness差值`+0.0300`但区间`[-0.0033,+0.0750]`跨0。
- 旧截断管线曾把基线/重排 Reference correctness 低估为`0.8117/0.8633`；修复后变为`0.9367/0.9667`。旧 G2 的“Reference correctness稳定提升”宣传口径废止，改为“重排稳定提升答案相关性，参考正确性为正向点估计”。
- 本轮分析脚本与JSON位于被Git忽略的 `downloads/g2_v081_analysis.py` 和 `downloads/g2_v081_analysis.json`；完整细节见 `EXPERIMENTS.md`。

## H2-R 独立未见集完整证据最终复核

- `2026-08-02` 重新校验 `heldout_v1` 数据集与英文查询指纹，8篇/40题均未改变；全部运行继续使用隔离目录 `downloads/heldout_h2_runtime/`。
- 新检索运行 `#3/#4` 与旧 `#1/#2` 指标完全一致；基线/重排分别为 `Hit@3=67.50%/90.00%`、`MRR=0.6204/0.7675`。两组落库证据分别有179/186个块超过800字符，重算 `hit_rank` 均为 `0 mismatch`。
- 检索配对bootstrap继续支持外部泛化：Hit@3差值95%区间`[+5.00,+37.56]`个百分点，MRR差值区间`[+0.0296,+0.2567]`。
- 修正版生成首次运行 `#3/#4` 均为40题、错误0；全缓存复跑 `#5/#6` 分别耗时 `0.510/0.511` 秒且指标完全一致。
- 基线 `#5`：Faithfulness `0.9917`、Answer relevance `0.9900`、Reference correctness `0.9675`、Citation correctness `0.9917`。
- 重排 `#6`：Faithfulness `1.0000`、Answer relevance `1.0000`、Reference correctness `0.9762`、Citation correctness `0.9938`。
- Answer relevance差值为`+0.0100`，10,000次配对bootstrap区间`[+0.0025,+0.0200]`，是唯一严格正向生成指标；Reference correctness差值`+0.00875`但区间`[-0.0125,+0.0325]`跨0。
- 旧截断管线的基线/重排 Reference correctness 为`0.8863/0.8900`，完整证据后提高到`0.9675/0.9762`。今后不得引用旧 H2 生成分数作为项目效果。
- `heldout_v1` 已正式关闭，不得继续调参；新算法必须使用 `heldout_v2` 或交叉验证。本地分析保存在被Git忽略的 `downloads/heldout_h2_runtime/h2_v081_paired_analysis.json`。

## v0.8.1 普通问答可选 DeepSeek 重排

- 论文详情页问答表单新增默认关闭的 `use_reranker` 复选框；`POST /papers/{paper_id}/ask` 将它转换为 `none` 或 `deepseek_listwise` 并写入查询记录。
- `rag_queries` 新增 `reranker TEXT NOT NULL DEFAULT 'none'` 与 `rerank_error TEXT`；`init_db()` 为旧SQLite自动补列，历史记录保持原始排序。
- `answer_rag_query()` 在选择重排时调用 `retrieve_relevant_chunks(..., top_k=20)`，再复用 `rerank_candidates_with_deepseek(..., top_k=5)`；普通模式继续沿用原检索行为。
- 重排异常时记录最多500字符的 `rerank_error`，退回原始候选前5条并继续生成；只有索引、召回为空或最终生成失败才把整条查询标记为 `failed`。
- 问答历史展示实际请求的检索模式、降级原因及证据的 `retrieval_rank → rerank_rank`，便于演示和审计。
- 新增旧库迁移、Top-20到Top-5、重排失败降级和页面控件测试；聚焦测试 `4 passed`，完整离线测试 `73 passed`，未调用真实外部API。

## v0.8.2 M1 论文多粒度父子融合候选策略

- 新增 `CHUNK_STRATEGY_MULTI_GRANULARITY = "paper_multi_granularity"`；每个固定父块 `1200/150` 同时生成约 `600/75` 的完整句子子块，父块与子块都进入索引。
- `PaperChunk.role` 与 `paper_chunks.chunk_role` 区分 `retrieval`、`parent` 和 `child`；`init_db()` 为旧SQLite自动补列，已有策略默认保持 `retrieval`。
- `build_multi_granularity_chunks()` 复用已有父子布局，建立785个父块与2196个子块，共2981个索引单元；没有加入表格前缀、语义断点或新分块参数。
- `retrieve_multi_granularity_chunks()` 使用相同问题、Seed/TF-IDF权重分别召回父块Top-20与子块Top-20，再通过现有 `reciprocal_rank_fusion()` 按共同父块ID融合并返回父块正文。
- 唯一变量为“固定父块单路排序”改为“父块/子块双路父ID融合”；英文问题、`1200/150`、Seed/TF-IDF `0.75/0.25`、Top-5、RRF常数60和候选池20全部冻结，不启用DeepSeek重排。
- 首次运行 `#38` 与全缓存复跑 `#39` 指标完全一致：`Hit@1/3/5=48.33%/76.67%/88.33%`、`MRR=0.6219`、覆盖率`85.28%`；相对固定基线`#21`，Hit@3提高10个百分点、Hit@5不变、MRR提高0.0183、覆盖率下降0.68个百分点。
- 逐题为12题改善、6题变差、42题不变，Top-5新增1题并丢失1题；10,000次配对bootstrap中Hit@3差值区间`[-1.67,+21.67]`个百分点，仍跨0，因此只能称为达到开发集门槛的候选策略。
- 两次运行均为0次Seed与DeepSeek新请求；`#38`复用2977个唯一块向量与120次双路问题向量，耗时25.880秒，`#39`全布局/索引缓存耗时19.635秒。
- 完整离线测试为`76 passed`。实验后运行`#40`恢复固定边界当前索引，指标与`#21`完全一致；普通解析默认仍为`length_boundary`。
- `heldout_v1`已经关闭，不得用于M1调参或宣传泛化；最终采用前必须建立`heldout_v2`或使用预注册交叉验证。

## v0.8.2 H3 M1独立测试集预注册与冻结

- 新增`benchmarks/heldout_v2_manifest.csv`和`HELDOUT_V2_DATASET.md`，选定8篇与开发集及`heldout_v1`标题零重叠的开放论文。
- `heldout_v2`只比较固定边界`length_boundary 1200/150`与M1 `paper_multi_granularity`；英文问题、Seed/TF-IDF `0.75/0.25`、TF-IDF、父/子Top-20、RRF常数60、Top-5和关闭DeepSeek重排均预先冻结。
- 用户授权后已下载并验证8篇PDF，共308个物理页、81,962,255字节；PDF、页文本和构建脚本只保存在Git忽略的`downloads/`中。
- 新增`benchmarks/heldout_v2.csv`，每篇事实型、方法型、参数型、结果型和原因型各1题，共40题；构建脚本按物理页和原文起止标记截取连续英文证据，40段证据均通过指定页校验。
- 新增`benchmarks/heldout_v2_queries_en.csv`，40条英文问题与中文问题一一对应且无中文字符；每篇5题、每种题型8题、问题和查询均无重复。
- M1正式采用门槛：Hit@3差值为正且配对bootstrap 95%区间下界大于0；Hit@5最多下降1题；覆盖率下降不超过1个百分点；MRR不下降。
- 冻结SHA-256：案例`0A1A29758B332F53D9F898CA08A94E83CC29DA071F2C7FA066FF746534A27FF5`，英文查询`4F3E83266476E008F1F9DE81F5E2D28CAA63985EA218D5702D667CE5CF2A2AA5`，清单`4F76E5850A0E72C8AFBD5892586B8F1242B1617A607AB4988C734C1DC3FE2C85`；8篇状态均为`downloaded/validated`。
- 本步骤没有调用Seed或DeepSeek，也没有运行指标；新增2个完整性测试，完整离线测试为`78 passed`。下一步只能按冻结配置在隔离数据库中先跑基线、再跑M1，禁止根据结果修改案例或查询。

## v0.8.3 H4 M1独立泛化验证

- 在隔离数据库`downloads/heldout_v2_runtime/metadata.db`完成冻结基线`#1`和M1`#2`；8篇论文、40题均成功，错误数为0，冻结案例、查询和清单指纹未改变。
- 基线`#1`使用`length_boundary 1200/150`，1084个块，`Hit@1/3/5=50.00%/82.50%/90.00%`、`MRR=0.6675`、覆盖率`87.04%`，总耗时`1500.422`秒。
- M1`#2`使用1084个父块和2991个句子子块，共4075个索引单元；`Hit@1/3/5=55.00%/77.50%/90.00%`、`MRR=0.6925`、覆盖率`88.66%`，总耗时`1888.767`秒。
- 10,000次配对bootstrap差值：Hit@1`+5.00`个百分点，区间`[-5.00,+15.00]`；Hit@3`-5.00`个百分点，区间`[-12.50,0.00]`；Hit@5`0`，区间`[-10.00,+10.00]`；MRR`+0.0250`，区间`[-0.0333,+0.0896]`；覆盖率`+1.62`个百分点，区间`[-6.91,+10.37]`。
- 逐题为6题改善、5题变差、29题不变，Top-5新增2题并丢失2题。Hit@5、覆盖率和MRR保护门槛通过，但主指标Hit@3为负，因此`adopt_m1=false`。
- M1恢复期间两次遇到Seed SSL EOF；受控评测的严格索引一致性门禁拒绝TF-IDF静默降级。保持同一数据、参数和运行重试成功，异常未进入正式指标。
- M1只保留为实验选项，普通解析默认继续使用`length_boundary`；不得在`heldout_v2`上调块大小、候选池、RRF常数或查询后再次宣称独立验证。该测试集已正式关闭。
- 本轮最重要的工程结论是：开发集点估计不能直接代表泛化；冻结数据、预注册门槛、配对区间和降级门禁共同防止了挑结果与错误降级污染。
- GitHub README用策略总览区分生产解析与冻结实验：生产自`v0.15.0`默认采用MinerU结构块 + Seed/TF-IDF；固定边界是历史实验基线，DeepSeek列表重排仍是已通过独立集验证的可选能力；M1不得省略其独立集Hit@3下降的限制。

## v0.9.0 论文问答准备闭环

- 新增 `paper_workflow.py`，只负责编排现有 `download_paper()`、`parse_paper_pdf()` 和 `index_paper_chunks()`，不得把三个模块的具体业务逻辑复制进工作流。
- `paper_preparation_status()` 根据现有论文、解析和索引记录推导统一状态：`available`、`downloading`、`download_failed`、`awaiting_parse`、`parsing`、`parse_failed`、`awaiting_index`、`indexing`、`index_failed`、`ready` 和 `link_only`。
- `mark_preparation_started()` 在后台任务入队前标记当前阶段，并拒绝重复运行中的请求；`prepare_paper_for_qa()` 跳过已经完成的下载或解析，只执行剩余步骤。
- 新增 `POST /papers/{paper_id}/prepare`。只有用户点击“准备问答”才开始处理；普通搜索不会自动下载，原 `POST /download`、`POST /parse` 和 `POST /index` 继续保留。
- 任一步失败后立即停止，错误继续由原阶段字段保存；再次点击“重试准备”会从当前数据库状态继续，不新建工作流表。
- 搜索结果、历史页和详情页展示统一准备状态；`GET /api/papers/statuses` 返回 `preparation_status`，前端每2秒自动更新。
- Crossref没有明确PDF直链时仍为`link_only`；Seed不可用时继续复用原索引服务的TF-IDF降级，不把降级逻辑放进编排层。
- 新增首次完整执行、下载失败停止和从索引阶段恢复3项测试；完整自动化测试为`81 passed`，没有调用真实外部API。

## v0.10.0 LangGraph研究检索闭环

- 新增`langgraph>=1.2.10`、`langgraph-checkpoint-sqlite>=3.1.1`与`research_agent.py`，使用`ResearchSearchState`保存研究问题、计划、人工确认标记、候选/已选论文、模型信息和降级错误。
- 生产图包含规划、计划确认中断、多查询/单查询、取消、去重和论文选择中断节点；两个审批点均使用动态`interrupt()`。
- DeepSeek只生成2至4条结构化学术检索词，不获得下载、数据库写入或任意工具执行权限；实际搜索继续由代码调用`search_all()`。
- `normalize_research_plan()`校验JSON字段、查询数量、长度和重复项；缺少API Key、请求失败或计划无效时记录原因并保存原始问题单查询，确认前不调用搜索。
- 新增`research_checkpoint.py`，以独立`downloads/langgraph_checkpoints.db`和稳定`thread_id`保存原生检查点；恢复必须使用`Command(resume=...)`与同一线程ID。
- 新增`research_tasks.checkpoint_thread_id`唯一索引；业务表保存审计状态，Checkpointer保存图执行位置，两者职责不得混淆。
- 新增`research_tools.py`、`RESEARCH_TOOL_REGISTRY`和标准`search_literature` StructuredTool；工具只检索元数据，不能下载、写库或调用模型。
- 新增`research_tool_calls`表与幂等替换/查询函数，任务详情页和状态API展示每路工具输入、来源数、状态、错误与耗时。
- A2新增Tool Schema、结构化结果、轨迹幂等落库、工作流和页面测试；完整离线测试为`95 passed`，没有调用真实外部API。
- 新增`research_workflow.execute_research_task()`；确认路由通过`BackgroundTasks`执行，成功复用`insert_papers()`，异常只更新当前任务。
- 新增`GET /research-tasks`、`GET /research-tasks/{task_id}`、确认/取消/选择POST路由和`GET /api/research-tasks/{task_id}`；运行页面每2秒轮询。
- 多查询结果按DOI、规范化URL和标题确定性去重，最终数量不超过用户输入的`max_results`；模型仍无下载和数据库写权限。
- 检索成功后任务进入`awaiting_selection`；用户通过复选框从该任务候选中选择1至5篇，`select_research_task_papers()`去重、校验归属并保存`selected_paper_ids_json/selected_count/selected_at`，状态变为`papers_selected`。
- 新增`POST /research-tasks/{task_id}/select`；选择阶段只冻结后续输入，严禁自动下载、解析、Embedding或报告生成。
- A3新增`prepare_paper_for_research`和`retrieve_paper_evidence`标准工具；前者复用论文准备闭环，后者返回标题、访问链接和带物理页码的Top-K证据。
- 新增`research_reports`表、`start_research_report()`和`update_research_report()`；报告状态为`preparing/retrieving/generating/completed/failed`，事务锁拒绝重复启动。
- 新增`research_report_service.generate_research_report()`和`POST /research-tasks/{task_id}/report`；只有用户点击后才准备所选论文并生成报告。
- 报告Prompt必须只使用提供的证据，并要求`[论文标题, p. 物理页]`引用；`link_only`或付费论文只展示合法访问链接，不得推断正文或绕过版权。
- 全部论文无证据时使用`build_no_evidence_report()`确定性拒答，禁止调用DeepSeek；单篇准备或召回抛错必须转换为当前论文限制，继续处理其余论文。
- 研究任务页面和状态API展示报告状态、证据数、限制、模型和错误；前端每2秒轮询运行阶段。
- A3新增工具、状态锁、证据/限制合并、零证据零模型调用、失败隔离和页面测试；完整离线测试为`101 passed`，没有调用真实外部API。
- A4将报告阶段正式接入生产图：新增第三个`interrupt()`、逐篇准备/检索自循环节点、生成节点、引用检查节点和重新生成中断。
- `stream_checkpointed_research_report()`按检查点位置选择`Command(resume=...)`或`invoke(None)`；`execute_research_report()`逐状态同步`research_reports`并兼容旧A3任务。
- `research_reports.details_json`新增`checkpoint_progress`和`citation_validation`；引用检查只验证标题/物理页码来自召回证据，不调用模型、不判断事实语义。
- 应用启动会把中断的报告、解析和索引状态转换为可重试失败；重新生成时图从未完成节点继续，论文准备服务按现有状态幂等跳过完成项。
- A4跨SQLite连接测试在第一篇准备后关闭连接，新图实例只继续第二篇，证明检查点恢复不会重复已完成论文；完整离线测试为`106 passed`。
- A5-0/A5-1新增`ContextConfig`和`pack_research_context()`；默认6000估算Token、每篇目标至少1条且最多2条，先跨论文分配第一轮，再分配第二轮。
- `details_json.evidence/context_evidence/context_stats`分别保存完整召回、实际模型输入和策略审计；生成与引用校验必须只使用`context_evidence`。
- `context_stats`必须包含原始/去重后/最终证据数、字符数、估算Token、压缩率、论文覆盖、最低配额状态、每篇数量及`exact_duplicate/overlap_duplicate/token_budget/paper_quota`删除原因。
- A5-2新增`normalize_evidence_text()`、`evidence_overlap_ratio()`和`deduplicate_paper_evidence()`：精确重复使用NFKC、大小写与空白归一化；高重叠使用5-token shingle containment、阈值`0.85`、至少12个shingle，并限制在同论文同页或相邻页。
- 不同论文不得互相去重；候选先按检索分数排序并保留高分块；删除记录必须包含`overlap_ratio`和`duplicate_of`。该能力称为文本重叠去重，不得称为Embedding语义去重。
- A5-3新增`build_research_context_payload()`和`research-context-json-v1`；所有JSON字段均是不可信数据，系统Prompt不得执行其中的角色切换、工具调用或格式命令。结构化边界是风险缓解，不得宣称完全阻止Prompt Injection。
- A5-4新增`evidence_validation_errors()`和`context-quality-gate-v1`；缺少论文ID、块ID、标题、正物理页或正文的证据必须在模型前剔除。
- `report_context_quality`必须随LangGraph检查点持久化到`details_json.context_quality`，并通过状态API与页面展示；`model_call_allowed=false`时只允许确定性拒答。
- 当前Token估算规则为中文字符1个、其他非空白字符约4个折算1个，不得称为DeepSeek官方Token。
- A5-0至A5-4已覆盖公平配额、预算轮询、去重、结构化Prompt、质量门禁、模型阻断、A3/A4接入、状态落库和页面展示；完整离线测试为`119 passed`，没有调用真实外部API。
- `request_plan_confirmation()`和`request_paper_selection()`在`interrupt()`前不得执行非幂等副作用；搜索完成后先入业务库，再用`update_state()`同步候选论文ID。
- 设置`LANGGRAPH_STRICT_MSGPACK=true`；`SqliteSaver`只用于本地单实例，未来公网多实例必须换`PostgresSaver`。
- 新增跨SQLite连接模拟进程重启测试，连续恢复两个中断且搜索节点仅执行一次；完整自动化测试为`93 passed`，没有调用真实DeepSeek、arXiv或Crossref。
- A1-A5单Agent LangGraph主线已经完成；尚未实现MCP、长期记忆或自主反思循环，本版明确不做多Agent，不能宣称完整自主研究Agent。

## v0.10.1 下载任务原子抢占

- `db.claim_download()`使用单条条件`UPDATE`把允许的起始状态改为`downloading`，并以`cursor.rowcount == 1`判断当前请求是否取得下载资格；不得退回“先查询状态、再无条件更新”的写法。
- `POST /papers/{paper_id}/download`只允许从`available`抢占，`POST /papers/{paper_id}/retry`只允许从`failed`抢占；抢占失败时不创建`BackgroundTasks`。
- `mark_preparation_started()`复用同一抢占函数，覆盖“准备问答”和“仅下载”同时提交的竞争；`prepare_paper_for_qa(download_claimed=True)`表示路由已取得资格，后台工作函数不得再次抢占。
- 该实现依赖数据库原子状态转换，不是分布式锁；SQLite与MySQL都能保留这一业务规则。公网部署仍需独立任务队列处理任务持久化、进程重启和Worker重试，两者不可互相替代。
- 新增双线程同时抢占与失败状态重试测试；完整离线测试为`121 passed`，不调用真实下载服务或外部API。

## v0.11.0 A6.1统一Agent Trace

- 新增`agent_traces`表，字段包含可空`task_id`、`thread_id`、`node_name`、`status`、起止时间、`duration_ms`、`tool_calls_json`、可空模型调用/Token字段、`retry_count`和`error`。
- 新增`create_agent_trace()`、`finish_agent_trace()`、`attach_agent_traces_to_task()`和`list_agent_traces()`；首次规划轨迹先按线程保存，业务任务创建后回填外键。
- 新增`agent_trace_service.trace_node()`和集中`_add_traced_nodes()`注册方式；成功、Tool部分失败、`GraphInterrupt`和普通异常分别记录为`success/partial/interrupted/failed`。
- Trace由LangGraph`configurable`中的显式开关、`thread_id`和`paper_hunter_task_id`控制；直接构图的旧测试不自动写入业务库。
- 节点Trace是尽力记录：落库失败时继续执行原节点；节点本身失败时记录后重新抛出原异常，不改变工作流错误边界。
- 新增`GET /api/research-tasks/{task_id}/traces`返回按执行顺序排列的结构化轨迹；本版不建设Metrics Dashboard。
- `model_calls/input_tokens/output_tokens`暂时保持空值，不能把上下文估算Token冒充真实API usage。
- 新增4项A6.1专项测试，覆盖任务回填、异常保持、Trace故障隔离和真实interrupt/resume重试；完整离线测试为`125 passed`，没有调用真实外部API。

## v0.11.1 A6.2固定Agent场景测试

- 新增`tests/agent/test_scenarios.py`，将Agent可靠性要求固定为8条独立、可重复运行的离线场景；不调用DeepSeek、Seed、arXiv或Crossref真实接口。
- Planner调用失败时必须以原研究问题执行且只执行一次单查询fallback；用户在首个`interrupt()`拒绝计划时，搜索Tool调用次数必须为0。
- `invoke_research_tool()`对未注册名称必须在Registry边界直接抛出错误，不能执行任何已注册工具或副作用；单个搜索源异常时工具结果应为`partial`并保留健康来源结果。
- 单篇论文准备失败必须转成该论文的访问限制，循环继续处理其余论文；没有通过质量门禁的Evidence时必须生成确定性拒答，报告模型调用次数为0。
- 使用同一`thread_id`和SQLite Checkpoint跨连接恢复时Planner只执行一次；重复下载和报告触发均通过数据库原子抢占保证只有一个请求取得资格。
- A6.2场景测试`8 passed`，完整离线测试`133 passed`。本步骤验证受控场景行为，不等同于真实线上成功率，也不包含进程内`BackgroundTasks`的重启恢复。

## v0.11.2 A6.3 Checkpoint恢复专项验证

- 新增`tests/agent/test_checkpoint_recovery.py`，分别验证计划确认、论文选择和报告确认三个`interrupt()`暂停点的跨SQLite连接恢复。
- 计划确认恢复后Planner调用次数保持1；用户拒绝时搜索调用保持0，Trace显示确认节点依次为`interrupted/success`。
- 论文选择恢复后不重跑Planner、`multi_query_search`或`deduplicate_results`；恢复值只能从已冻结的候选论文ID中选择。
- 报告确认恢复测试在第一篇论文完成后主动关闭流和连接，创建第三个图实例后从第二篇继续；两篇论文各准备和检索一次，报告模型只调用一次。
- 专项测试同时校验关键Node Trace数量和状态，避免仅凭最终任务状态掩盖重复副作用。
- A6.3专项测试`3 passed`，完整离线测试`136 passed`，无真实外部API调用。该结论只覆盖`SqliteSaver + thread_id + Command(resume=...)`，不覆盖`BackgroundTasks`跨进程恢复。

## v0.11.3 A6.4 Agent Metrics

- 新增`list_agent_metric_tasks()`和`list_agent_metric_traces()`，只读加载任务、报告和Trace快照；不新建指标数据表，不修改运行记录。
- 新增`agent_metrics_service.build_agent_metrics()`，输出`agent-metrics-v1`，聚合Task Completion、Tool Success、Approval Compliance、Checkpoint Recovery、Fallback Success、平均任务/节点耗时以及失败和部分失败分布。
- 每项比例保存`numerator/denominator/excluded/value_percent/definition`；等待审批、等待论文选择、未开始报告和用户取消不作为系统失败进入Task Completion分母。
- Approval Compliance当前口径为“明确拒绝检索计划后零搜索”；Checkpoint Recovery只统计已有后续恢复尝试的中断，仍在等待用户的中断计入`excluded`。
- `model_calls/input_tokens/output_tokens`只聚合Trace中的真实非空值；无观测时总量和平均值返回`null`，同时返回usage覆盖率。
- 新增内部接口`GET /api/agent/metrics`；本版不增加普通用户Dashboard。pytest场景结果尚未持久化，`scenario_pass_rate`明确返回`not_persisted`，由A6.5报告单独接入。
- 新增空数据、指标口径、真实usage、临时SQLite和API回归测试；A6.4专项测试`4 passed`，完整离线测试`140 passed`，无真实外部API调用。

## v0.11.4 A6.5 Agent Evaluation Report

- 新增`AgentScenario`与`AGENT_SCENARIOS`，冻结A6.2的8条固定可靠性场景和A6.3的3条Checkpoint恢复场景；测试函数名变化时清单和报告测试必须同步更新。
- 新增`parse_junit_scenario_results()`，从pytest JUnit XML读取每条场景的`passed/failed/skipped/not_run`、耗时和错误；漏跑不能被当作通过。
- 新增`build_agent_evaluation_report()`与`agent-evaluation-report-v1`，受控pytest结果和A6.4真实运行Metrics分别存放，报告包含版本、生成时间、总状态、样本、分子/分母和限制。
- 新增`render_agent_evaluation_text()`与`save_agent_evaluation_report()`，原子写入带时间戳和`latest`别名的JSON/文本文件到`outputs/agent_evaluation/`。
- 新增开发命令`scripts/run_agent_evaluation.py`；默认运行11条冻结场景并读取本地运行Metrics，`--skip-runtime-metrics`只报告离线场景。Web接口不得触发pytest。
- `outputs/`由`.gitignore`排除，报告可能含本地运行统计，默认不进入公开Git；源码、冻结场景和测试必须提交。
- 真实离线执行结果为`11/11 passed`，运行时样本因使用`--skip-runtime-metrics`显示N/A；新增4项报告测试，完整离线测试`144 passed`，无外部API调用。

## v0.12.0 A7.1 最小MCP只读接入

- 新增`mcp>=1.27,<2`并锁定MCP Python SDK 1.x，避免2.x主版本升级造成未验证的接口变化。
- 新增`mcp_server.create_mcp_server()`，以stdio方式提供独立MCP Server；FastAPI与MCP继续并列复用Service Layer，不互相调用。
- 新增`build_public_paper_metadata()`和`get_paper_metadata(paper_id)` Tool，返回论文公开元数据、访问链接和状态，不返回`local_path`，不下载、不写库、不调用外部API。
- Tool声明`readOnlyHint/idempotentHint`等安全注解；非法ID和不存在记录返回明确错误。
- `tests/agent/test_mcp_server.py`使用官方内存传输建立真实Client Session，完成初始化、Tool发现、结构化调用和标准Tool错误验证；专项`6 passed`，完整离线测试`150 passed`。
- A7尚未全部完成；下一步A7.2复用现有`search_literature`，并保持搜索来源降级和输入上限不变。

## v0.12.1 A7.2 MCP文献检索Tool

- 新增`search_public_literature()`适配函数，通过`invoke_research_tool(LITERATURE_SEARCH_TOOL_NAME, ...)`调用现有白名单Tool，不直接复制或绕过搜索规则。
- MCP注册`search_literature(query, max_results=10)`，复用空查询、240字符和1至50条数量校验，以及arXiv/Crossref来源计数和`partial/failed`状态。
- Tool声明只读、非破坏、幂等和`openWorldHint=True`；实际运行会访问公开搜索源，但不写论文数据库、不下载PDF、不调用DeepSeek或Seed。
- 新增委托参数、既有校验零外部调用及协议级Client检索测试；单来源失败的`partial`结果和健康候选可完整返回。
- MCP专项测试`9 passed`，完整离线测试`153 passed`；全部外部检索使用mock，未调用真实API。
- 下一步A7.3评估并接入`retrieve_paper_evidence`，必须保持“只读取已索引证据、不自动准备或下载论文”的边界。

## v0.12.2 A7.3 MCP论文证据检索Tool

- 新增`retrieve_public_paper_evidence()`，固定通过`invoke_research_tool(RETRIEVE_EVIDENCE_TOOL_NAME, ...)`调用现有证据Tool。
- MCP注册`retrieve_paper_evidence(paper_id, question, top_k=3)`，复用正整数ID、非空问题和Top-K 1至6校验，返回标题、访问链接、页码证据、评分和错误。
- MCP未暴露`prepare_paper_for_research`；论文未索引或无匹配证据时只返回`failed`，不得自动下载、解析或建立索引。
- 该Tool对论文业务数据只读；若Seed已配置且查询向量未缓存，底层检索可调用Embedding API并写入内部`embedding_cache`，因此使用`openWorldHint=True`。
- 新增委托边界、非法问题零检索和协议级Client证据测试；MCP专项`12 passed`，完整离线测试`156 passed`，未读取真实PDF或调用外部API。
- 下一步A7.4增加独立stdio MCP Client冒烟命令，验证子进程传输和三个Tool发现；完成后结束A7并进入B1。

## v0.12.3 A7.4 stdio MCP Client冒烟验证

- 新增`scripts/smoke_mcp_client.py`，以`sys.executable -m my_agent_project.mcp_server`启动真实子进程，工作目录固定为仓库根目录。
- Client使用10秒读取超时和UTF-8编码，执行`initialize()`与`list_tools()`；不调用元数据、外部搜索或证据Tool。
- 严格校验Server名称和三个Tool的名称及顺序；不匹配、超时或子进程错误均返回非零退出码。
- 精确过滤MCP SDK当前无关的`lifespan`前向引用警告，并将Server普通请求日志调整为WARNING；不隐藏业务异常。
- 真实运行通过：Server `paper-hunter`、SDK Server版本`1.29.0`、MCP协议`2025-11-25`、stdio传输、三个Tool全部发现。
- 新增真实子进程测试；MCP专项`13 passed`，完整离线测试`157 passed`，无外部API或业务数据调用。
- A7阶段完成。下一步B1解决进程内`BackgroundTasks`无法跨服务重启可靠恢复的问题。

## v0.13.0 B1.1 SQLite持久下载队列

- 新增`background_jobs`表，字段包含任务类型、载荷、活动去重键、论文/研究任务关联、状态、优先级、尝试上限、可执行时间、租约、Worker、错误和时间戳。
- `enqueue_download_job()`使用`BEGIN IMMEDIATE`把`available/failed -> downloading`与任务创建合并为同一事务；活动状态为`queued/running/retry_wait`，同一论文只允许一个活动下载任务。
- 新增`claim_next_background_job()`、`complete_background_job()`、`fail_background_job()`和`recover_expired_background_jobs()`；领取使用SQLite写事务和租约，不依赖内存锁。
- 新增`task_queue_service.run_one_job()`，通过注册表调用现有`download_paper()`；失败按`5s * 2^(attempt-1)`退避，默认最多3次，达到上限后任务和论文都进入`failed`。
- `download_paper(raise_on_error=True)`只供Worker使用：服务仍先更新论文失败记录，再把异常交给队列；默认参数保持旧流程的单篇异常隔离。
- 新增独立`python -m my_agent_project.worker`和`--once`模式。Web进程只入队，Worker进程执行；启动Web后若不启动Worker，任务会安全停留在队列而不是丢失。
- `init_db()`不得把存在活动持久任务的`downloading`论文恢复成`available`；没有活动任务的旧遗留记录仍按原规则恢复。
- `GET /api/papers/statuses`新增`download_job_status`与`download_job_attempts`；不破坏旧轮询字段。
- 新增`tests/test_task_queue.py`6项离线测试：持久化、并发原子去重、下载服务委托、失败重试成功、过期租约跨Worker恢复、达到上限最终失败；完整测试`163 passed`。
- B1.1完成时只迁移`POST /papers/{paper_id}/download`和`/retry`；同日B1.2继续迁移`prepare`。

## v0.13.0 B1.2持久论文准备任务

- 新增`enqueue_paper_preparation_job()`，在`BEGIN IMMEDIATE`事务中读取论文和文档状态、检查活动下载/准备任务、锁定`downloading/parsing/indexing`阶段并创建`prepare_paper`任务。
- 无本地PDF时只允许`available/failed`进入准备；路由仍使用`can_attempt_download()`拒绝没有合法直链的论文。已有本地PDF时从未完成的解析或索引阶段开始；已经`ready`时不创建任务。
- `POST /papers/{paper_id}/prepare`移除`BackgroundTasks`参数，不再直接调用`mark_preparation_started()`和`prepare_paper_for_qa()`。
- Worker注册`prepare_paper`，处理器只调用现有`prepare_paper_for_qa(paper_id, download_claimed=...)`；下载、解析和索引实现仍分别属于原Service。
- 准备失败时从论文或文档记录提取真实阶段错误；进入`retry_wait`时把失败阶段恢复为处理中，下一次从数据库断点重入；最终失败保留对应`download_failed/parse_failed/index_failed`。
- `init_db()`恢复遗留状态时必须排除活动`prepare_paper`任务，防止Web或Worker启动把有效的`downloading/parsing/indexing`错误改成可重试失败。
- 同一论文的活动`download_paper`和`prepare_paper`互斥；任一入口已入队时，另一个入口不能创建第二个任务。
- 状态API新增`preparation_job_status`和`preparation_job_attempts`。
- B1.2新增6项测试，队列专项累计`12 passed`，完整离线测试`169 passed`；不调用真实PDF、Seed或DeepSeek。
- B1.2完成时的边界：直接下载与完整论文准备已进入持久Worker；单独`parse/index/ask`、智能研究搜索与报告仍使用`BackgroundTasks`。随后由B1.3迁移单独解析和索引。

## v0.13.0 B1.3单独解析与索引持久任务

- 新增`_active_paper_job()`和`enqueue_parse_job()/enqueue_index_job()`；在SQLite写事务内完成前置状态校验、论文级活动任务互斥、阶段状态锁定和任务创建。
- `parse_paper`要求存在本地PDF，`index_paper`要求解析已完成；活动任务拒绝重复入队，完成后仍保留页面“重新解析/重新建立索引”的原有能力并复用缓存。
- `/papers/{paper_id}/parse`与`/index`不再接收`BackgroundTasks`，路由只做请求校验和持久入队。
- Worker新增`parse_paper`与`index_paper`处理器，分别调用现有`parse_paper_pdf()`和`index_paper_chunks()`；服务内部异常隔离保持不变，处理器通过数据库终态判断任务是否成功。
- 单独解析或索引失败进入`retry_wait`时恢复对应处理中状态；达到尝试上限或租约最终过期时同步`parse_failed/index_failed`及真实错误。
- `init_db()`不得重置存在活动`parse_paper/index_paper`任务的状态；状态API新增`parse_job_status/attempts`和`index_job_status/attempts`。
- 下载、完整准备、单独解析和单独索引共享论文级活动任务互斥；同一论文任一活动任务存在时，其他处理入口不得创建第二个任务。
- B1.3新增6项测试，队列专项累计`18 passed`，完整离线测试`175 passed`；不调用真实PDF、Seed或DeepSeek。
- B1.3完成时的边界：论文下载与知识库准备入口已进入持久Worker；`ask`、智能研究搜索和报告仍使用`BackgroundTasks`。随后由B1.4迁移单篇RAG问答。

## v0.13.0 B1.4单篇RAG问答持久任务

- 新增`background_jobs.rag_query_id`及兼容旧数据库的补列迁移；`enqueue_rag_answer_job()`在`BEGIN IMMEDIATE`事务中校验索引、创建问答记录和任务。
- 去重键由论文ID、规范问题和重排模式生成；完全相同的活动请求返回原`query_id/job_id`，不同问题可分别排队。
- `/papers/{paper_id}/ask`不再接收`BackgroundTasks`；Worker的`answer_rag_query`处理器复用现有RAG服务，`raise_on_error=True`只用于把已记录失败交给队列重试。
- `answer_rag_query()`遇到已经`answered`的记录直接返回，防止答案已落库但任务确认失败时重复调用模型和消耗Token。
- 回答失败进入`retry_wait`时恢复`answering`并记录等待原因；达到上限或租约最终过期时进入`answer_failed`。启动时只有不存在活动持久任务的旧`answering`记录才会被标记中断。
- 活动回答任务阻止解析/索引等写操作并发修改同一论文知识库；多个不同问题仍允许分别排队。
- 默认任务租约从90秒提高到600秒，覆盖当前模型超时窗口；自动心跳续租尚未实现，不得宣称租约可无限延长。
- 状态API新增`rag_job_status/rag_job_attempts`；新增9项测试，队列专项累计`27 passed`，完整离线测试`184 passed`，其中覆盖答案落库后租约接管且不重复调用模型，不调用真实DeepSeek或Seed。
- B1.4完成时的边界：下载、准备、解析、索引和单篇问答已进入持久Worker；智能研究搜索和报告仍使用`BackgroundTasks`。随后由B1.5补自动租约续期。

## v0.13.0 B1.5 Worker自动心跳续租

- 新增`db.renew_background_job_lease()`；续租条件同时校验任务为`running`、`worker_id`匹配、租约非空且尚未过期，防止旧Worker恢复后重新抢回任务。
- `task_queue_service._maintain_job_lease()`使用守护线程和`Event`维护执行期租约；默认间隔为`min(max(lease/3, 0.25), 30)`秒，任务离开上下文后立即停止。
- 心跳上下文要求`lease_seconds >= 2`且续期间隔小于租约；`worker.py`命令行执行同一校验。数据库底层租约函数仍允许1秒，用于精确所有权测试，不代表Worker有效配置。
- `run_one_job()`在心跳上下文内完成Service处理和任务成功/失败写回；默认租约从B1.4临时的600秒恢复到90秒。
- 心跳异常不得替换原业务异常；若所有权已经丢失，`complete_background_job()`仍以Worker条件拒绝旧任务确认。当前语义是至少一次执行，不得宣称外部副作用严格只执行一次。
- 新增3项测试：当前Worker未过期租约可续期而错误/过期Worker不可续期；长任务超过原租约仍持续续期且恢复器返回空列表；失去租约的Worker不能把处理器异常写成普通重试。队列专项`30 passed`，完整离线测试`187 passed`。
- 当前边界：Worker执行层已完成自动续租；智能研究搜索和报告仍使用`BackgroundTasks`。下一步迁移智能研究搜索任务。

## v0.13.0 B1.6 智能研究搜索持久任务

- 新增`db.enqueue_research_search_job()`：以`research_search:{task_id}`为活动去重键，在同一`BEGIN IMMEDIATE`事务中完成研究任务确认状态转换与队列任务创建。
- `POST /research-tasks/{task_id}/confirm`不再接收`BackgroundTasks`；未确认任务不会入队，重复确认和并发双击复用同一活动任务。
- `task_queue_service`注册`research_search`处理器并复用`research_workflow.execute_research_task()`；后者新增`raise_on_error`可选参数，仅让Worker在业务错误已经落库后继续执行队列重试。
- 失败进入`retry_wait`时研究任务恢复为`running`并展示等待重试原因；达到上限或租约最终过期时保持`failed`。
- `init_db()`只恢复没有活动`research_search`任务的旧`running`孤儿；有效队列任务跨Web/Worker初始化保留。
- 已经保存候选并进入`awaiting_selection`时，恢复后的处理器直接成功，不再次调用搜索Tool；这保证“候选落库后、队列确认前中断”的重放幂等性。
- `get_research_task()/list_research_tasks()`和任务状态API增加`search_job_status/search_job_attempts/search_job_max_attempts`，运行页展示队列尝试信息。
- 新增6项离线测试：原子持久入队、串行与并发重复确认、工作流委托、失败重试成功、最终租约失败、完成后恢复零重复搜索。队列专项`36 passed`，完整离线测试`193 passed`，没有调用真实外部API。
- 当前边界：智能研究搜索已进入持久Worker；研究报告仍使用`BackgroundTasks`。下一步B1.7迁移研究报告生成。

## v0.13.1 B1.7 研究报告持久任务

- 新增`db.enqueue_research_report_job()`：在同一`BEGIN IMMEDIATE`事务中校验已冻结论文、创建或重置`research_reports`记录，并写入`research_report`持久任务。
- `POST /research-tasks/{task_id}/report`不再接收`BackgroundTasks`；初次生成、失败重试后的重新生成和完成后的显式再生成都通过持久队列执行。
- 活动去重键为`research_report:{task_id}`；串行重复点击与并发请求复用同一活动任务，旧任务成功后可创建新的再生成任务。
- Worker处理器复用`research_workflow.execute_research_report()`和原LangGraph检查点，不复制论文准备、证据检索、上下文门禁、DeepSeek生成或引用检查逻辑。
- 报告失败进入`retry_wait`时恢复为`preparing`并保留等待原因；达到尝试上限或租约最终过期时进入`failed`。
- 若报告已经`completed`但Worker尚未确认队列成功，租约恢复后的处理器直接完成，不重复调用报告模型。
- `init_db()`只把没有活动`research_report`任务的旧运行中报告标记为中断；`get_research_report()`、状态API和页面增加`job_status/job_attempts/job_max_attempts`。
- 新增6项离线测试：原子持久入队、串行与并发去重、工作流委托与显式再生成、失败重试、最终租约失败、完成后恢复零重复模型调用。队列专项`42 passed`，完整离线测试`199 passed`。
- 当前边界：B1面向用户的后台任务迁移完成；离线评测路由仍可使用FastAPI `BackgroundTasks`，SQLite Worker提供单机至少一次执行语义，不宣称分布式集群或严格恰好一次。

## v0.14.0 统一研究工作台与最小会话闭环

- 首页移除普通搜索/智能研究模式切换；`POST /search`统一创建研究任务、LangGraph计划和初始`research_messages`。
- 新增`research_tasks.report_question`，把“用于发现论文的主题”和“报告必须回答的问题”分开，论文选择后必须先明确报告问题才能生成。
- 新增`POST /research-tasks/{task_id}/message`：计划确认前用于补充范围并更新同一`thread_id`的计划；报告完成后用于提交证据追问。
- 新增`research_messages`表，保存`role/phase/content/status/evidence/model/error`；当前用于任务内消息展示和异步回答状态，不承担跨任务长期记忆。
- 新增`research_conversation_service.answer_research_followup()`与`research_followup`持久任务。Worker按冻结论文重新召回页码证据，并把“当前问题 + 当前报告 + 本次证据”交给DeepSeek；无证据时确定性提示，不调用模型。
- 追问消息和后台任务同事务创建，活动任务按`research_followup:{task_id}`去重；重试、最终失败和租约恢复同步消息状态。
- 当前明确不包含：完整聊天历史注入、自动摘要、长期用户画像、跨任务记忆或复杂Token预算。这些属于下一阶段上下文/记忆管理。
- 研究任务页固定加载`research_task_poll.js`，由脚本读取DOM状态决定是否轮询，避免Jinja内容块中定义的状态变量在脚本块不可见；请求使用`cache: "no-store"`，状态API同时返回`Cache-Control: no-store`。
- 新增6项流程、轮询与检查点测试；旧双模式页面测试改为单入口契约，完整离线测试`205 passed`。

## v0.15.0 MinerU云端精准解析默认链路

- 生产环境不再使用`pypdf + 1200/150`作为默认解析：`parse_paper_pdf()`未传`strategy`时固定调用MinerU云端，现有解析和准备按钮无需新增模式选择。
- 新增`mineru_service.parse_pdf_with_mineru()`：申请预签名上传地址、PUT上传PDF、轮询批次，下载结果并读取稳定版`content_list.json`。
- 新增`build_structured_chunks()`，将标题层级和文本、表格、公式、代码、列表、图表说明等转为`PaperChunk`；过滤页眉页脚、页码、侧边文字及参考文献区，保留物理页码和bbox。
- `paper_documents`新增`parser_backend/parser_model/parser_job_id/parse_stage/parsed_at`；`paper_chunks`新增`section_title/content_type/bbox_json`；`update_remote_parse_state()`保存远程阶段和可恢复任务ID。
- MinerU结果保存到Git忽略的`downloads/mineru/<model>/<pdf_sha256>/`；相同PDF和模型优先复用本地结构结果，远程处理中断则使用原batch ID继续轮询。
- RAG索引为MinerU结构块注入简短章节与内容类型前缀，返回证据、重排候选和生成Prompt均携带结构元数据；召回权重和重排算法不变。
- `evaluation_service.prepare_benchmark()`显式指定`length_boundary`，全部旧分块实验继续由`pypdf`复现。严禁把实验基线误写成当前生产默认，也严禁MinerU失败后自动切回旧解析器。
- 配置使用`MINERU_API_TOKEN`及相关超时、模型和轮询项。Token不得进入页面、日志、文档或Git；真实解析会把PDF发送给MinerU第三方服务。
- 官方精准解析API当前限制为单文件200MB/200页，并受账户额度约束；代码已实现200MB前置限制，其他限制错误进入Worker重试或最终失败。
- 自动化测试使用`httpx.MockTransport`和临时ZIP验证完整协议，不调用真实MinerU；完整离线测试为`209 passed`。

## 2026-08-24 多来源搜索MCP接入进行中

- 当前生产调用链为`multi_query_search/single_query_search -> search_literature_batch() -> search_many() -> search_queries_via_mcp() -> 同一Session内多次MCP search_papers -> PaperCandidate`；`search_all()`只保留单query兼容入口。
- 新增`paper_search_mcp_service.py`，只允许调用第三方`search_papers`，不得调用其下载、读取或Sci-Hub相关Tool。
- `SUPPORTED_SOURCE_IDS`冻结第三方统一搜索当前21个来源；新任务选择全部时也必须保存并传递明确ID，不得使用`all`。旧任务缺失来源时回退`LEGACY_SOURCE_IDS = ("arxiv", "crossref")`。
- `DEFAULT_MAX_RESULTS_PER_SOURCE = 3`是与任务最终`max_results`分离的第一版召回预算，后续只能通过真实检索评测调整，不能按来源数量除法推导。
- `search_queries_via_mcp_async()`在一个stdio Session内执行同一批queries；不得改回每条query启停一次第三方Server，也暂不建设全局连接池。
- `paper_candidate_from_mcp()`只归一化元数据和候选`pdf_url`，不得发起网络验证；真实下载资格和响应校验继续由现有下载流程负责。
- 首页来源选项由后端`SEARCH_SOURCE_OPTIONS`渲染，默认明确选择全部21个来源；前端脚本只操作已渲染复选框，不维护第二套来源ID。最终数量继续由Paper Hunter的`max_results`控制。
- 旧`search_arxiv()`、`search_crossref()`及直接HTTP实现已删除，不作为fallback；`parse_arxiv_feed()`、`parse_crossref_items()`只保留为纯解析函数和离线测试夹具。
- 第1步完整回归`214 passed`；第2步定向回归`136 passed`、完整离线回归`215 passed`，均未调用真实外部搜索API；这些数字只表示当时步骤，当前结果以第3步为准。
- 第3步已新增`research_tasks.sources_json`作为任务来源canonical value；LangGraph State只保存执行副本，Worker恢复前必须用数据库值调用`update_checkpoint_sources()`校准，然后再`Command(resume=...)`。
- 升级前旧任务或缺失来源的Checkpoint统一回退`["arxiv", "crossref"]`；新任务必须保存用户创建时提交并经过`normalize_source_ids()`校验的明确ID列表。
- `multi_query_search`和`single_query_search`现已统一调用`search_literature_batch()`；一次任务只调用一次`search_many()`和`search_queries_via_mcp()`，同一Session内依次执行全部Planner queries，每条query仍保存独立Tool Trace。
- 第4步完成首页来源多选、默认全选、全选/清空、实时计数和零来源双层拦截。`POST /search`只接受后端白名单中的明确source ID列表，并继续把同一列表写入Checkpoint和`research_tasks.sources_json`。
- 第4步浏览器验收确认：默认`21/21`、清空后`0/21`且提交按钮禁用、再次全选后恢复提交；完整离线回归`224 passed`。Planner Prompt、`PaperCandidate`、搜索实现、去重、下载、报告及MCP下载边界均未修改。
- 第5步首轮真实联网任务`#10/#11/#12`发现`paper-search-mcp==0.1.4`的业务数据位于`structuredContent.result`，旧Adapter因读取顶层`papers`而把全部真实结果转换为失败；这些任务保留为失败现场。
- 第5.1步仅修复MCP返回结构兼容：支持真实`result` envelope、旧直接payload和文本回退，校验并读取`papers/sources_used/source_results/errors/raw_total/total`，同时让`ExceptionGroup`错误摘要保留叶子根因。不得把该修复描述为Planner、Mapper、去重或Reranker改造。
- Tool Trace的Checkpoint副本新增`sources_used/source_results/raw_total/total`验收字段；业务库表未迁移，现有页面持久Trace字段保持兼容。完整离线回归`233 passed`。
- 原样复验任务`#13/#14/#15`均在一个任务级MCP Session内连续完成4条Planner query，并分别以`5/18/19`篇候选进入Paper Selection；合并前/去重后为`20->5、20->18、20->19`。后台只存在`research_search`任务，未调用任何MCP下载Tool。
- `#13`全21来源存在Zenodo日期类型错误且最终5篇明显无关；`#14/#15`中Semantic Scholar均因429返回0但第三方`errors={}`；`#15`仍召回人类记忆论文。第5.1只证明通路恢复，不能宣称多来源排序质量已达标。
- `2026-08-25`新增P2冻结回归集`benchmarks/rerank_p2_cases.json`和独立脚本`scripts/evaluate_rerank_p2.py`。15例使用生产`_rerank_prompt()`、`ask_deepseek_json()`及当前`deepseek-v4-pro`配置真实调用，未进入RAG或LangGraph。
- P2结果：JSON/ID契约`15/15`、Top1`15/15`、关键词陷阱`2/2`、Candidate Injection`2/2`；严格关键关系判定`14 PASS / 1 FAIL = 93.33%`。唯一失败为模型比较题的次级顺序，直接答案仍为Top1；当前结论是不修改P2，保留固定案例继续回归。
- `2026-08-25`新增P3冻结集`benchmarks/rag_p3_cases.json`与脚本`scripts/evaluate_rag_p3.py`，仅调用生产`build_rag_prompt()`和`ask_deepseek()`；真实配置是`deepseek-v4-pro`、thinking开启、temperature未显式设置。
- P3结果为`14 PASS / 1 WARN / 0 FAIL`，Strict Pass Rate `93.33%`、Unsupported Claim Cases `0`、Citation Error Cases `1`、证据不足处理`4/4`、Evidence Injection `2/2`。唯一WARN是证据不足回答末尾的“证据来源”为空；本轮不修改Prompt，后续只需针对不足分支强化标题/页码输出并复跑同一冻结集。
- 用户随后只为P3增加“证据不足时，实际用于判断缺失的片段也必须列入证据来源”规则，并用同一15例复跑。Case 12成功补出`Neural Architecture Description，第5页`，原14个PASS回归`0/14`；最终`15 PASS`，Citation Error和Unsupported Claim均为0。该规则已通过冻结回归，可以保留。
- `2026-08-25`新增P4冻结集`benchmarks/research_report_p4_cases.json`和脚本`scripts/evaluate_research_report_p4.py`，按生产`research-context-json-v1`与真实`deepseek-v4-pro`配置运行15例。结果`10 PASS / 5 WARN / 0 FAIL`；跨论文污染、错误泛化、非法数值比较、冲突解释、unsupported claim均为0，冲突/部分证据/Injection分别`1/1、1/1、2/2`。
- P4的5个WARN仅为多来源引用格式：模型写成`[A, p.x; B, p.y]`而非独立`[A, p.x][B, p.y]`，标题页码事实正确但不满足引用解析契约。后续只需强化这一条并复跑固定15例，不应改动已经通过的跨论文证据边界和安全规则。
- 用户已将P4改为适合网页展示的中文纯文本，并采用`[论文标题，第3页]`及`[论文A，第3页；论文B，第5页]`引用。使用同一冻结15例和真实`deepseek-v4-pro`配置复跑后为`15 PASS / 0 WARN / 0 FAIL`，Strict Pass Rate `100%`；旧5个Citation WARN全部消失，原10个PASS全部保持。
- P4新回归的Unsupported Claim、Cross-paper Attribution Error、Unsupported Generalization、Invalid Numerical Comparison和Citation Error均为0；冲突、部分证据和Injection分别通过`1/1、1/1、2/2`。报告没有Markdown标题、表格、代码块、空章节或机械固定四段，当前Prompt可以保留。
- `scripts/evaluate_research_report_p4.py`现按新中文引用和纯文本契约检查；冻结案例事实预期未改变，新原始输出为Git忽略的`outputs/research_report_p4_results_plaintext.json`。不得再用旧`[A, p.3]`解析器误判新结果。

## v0.16.0 报告后实时Follow-up Chat

- 本版只改造研究报告完成后的第三阶段追问，不修改Planner、搜索、论文选择、下载、MinerU解析、Evidence Retrieval核心策略、`pack_research_context()`、LangGraph检查点或报告内容。
- 新增`db.create_research_followup_messages()`：在一个`BEGIN IMMEDIATE`短事务中校验任务、报告与活动追问，先保存User消息和`pending` Assistant消息；实时链路不创建后台任务，也不更新`research_reports`。
- 新增`model_service.stream_deepseek()`：向生产DeepSeek `/chat/completions`发送`stream=true`，解析SSE数据并只转发`delta.content`；禁止先生成完整答案再人工切片。
- `research_conversation_service.prepare_research_followup_context()`复用原“当前问题 + 已有报告 + 所选论文新召回证据 + Access Limitations”上下文；`stream_research_followup()`发送`retrieving/generating/streaming/completed`状态及`delta/done/error`事件。
- 新增`POST /research-tasks/{task_id}/chat`和`research_followup_chat.js`。前端使用`fetch POST + ReadableStream`消费SSE风格响应，立即追加User消息，只更新一个Assistant气泡，不调用`window.location.reload()`。
- 成功时将完整Assistant回答、证据和实际模型保存为`completed`；中途失败或客户端断开时保存`failed`及已有部分内容，不能把半截回答标记为成功。手动刷新后服务端继续从`research_messages`恢复历史。
- 旧`POST /message -> enqueue_research_followup_job() -> Worker`链路保留为无JavaScript回退，因此B1可靠队列能力没有被删除；网页正常启用JavaScript时使用实时链路。
- 自动化覆盖真实API流解析、前端无刷新契约、成功持久化、失败状态和报告不变；完整离线测试`237 passed`。真实浏览器连续两次追问确认同URL、即时User消息、loading、正文逐段增长、完成后继续发送、刷新恢复和报告不变全部通过。
- 当前仍不包含完整历史注入、对话摘要、长期记忆、跨任务用户画像、向量消息检索、WebSocket、复杂断线续传或报告自动更新。

## v0.17.0 Recent Messages多轮追问

- 新增`db.list_recent_research_conversation_messages()`：只查询指定`task_id`下`phase='followup'`、`role in (user, assistant)`且`status='completed'`的最近10条消息，先倒序截取窗口，再按`created_at/id`升序返回。
- 实时入口把同事务创建的`user_message_id`和`assistant message_id`传给Service；历史查询明确排除这两个ID，因此当前问题只出现在`followup_question`一次，pending Assistant不会进入历史。
- 无JavaScript Worker回退的队列payload同步保存`user_message_id`，确保两条路径使用同一排除规则；旧payload缺少该字段时仍以0兼容，不影响任务执行。
- `prepare_research_followup_context()`新增`conversation_history`，`build_research_followup_prompt()`把它序列化进不可信JSON，不写入System Prompt。
- 历史消息只用于解析“它、刚才那篇、第二种方法”等上下文指代；历史Assistant陈述不被视为论文证据，事实回答仍必须由本轮Evidence或报告支持。
- Hybrid Retrieval、Evidence Top-K、`pack_research_context()`、报告生成、引用校验、DeepSeek真Streaming和前端ReadableStream均未修改；普通追问仍不更新Report。
- 新增5项冻结行为测试，覆盖前一轮注入且当前问题不重复、pending排除、failed Assistant排除、任务隔离及最近10条时间顺序。

## v0.17.0 Conversation Summary滚动摘要

- `research_tasks`新增`conversation_summary`、`summary_up_to_message_id`、`conversation_summary_error`和`conversation_summary_updated_at`，由`init_db()`为旧SQLite增量补列；原始消息继续完整保存在`research_messages`。
- `db.list_research_conversation_context_messages()`同时返回最近10条有效原文和“游标之后、最近窗口之前”的待摘要消息；pending/failed Assistant、本轮消息及其他任务消息均被排除。
- `prepare_research_conversation_context()`把旧摘要与本次退出窗口的消息交给`ask_deepseek()`非流式合并，并通过预期旧游标CAS推进覆盖边界；已经覆盖的ID不会再次进入模型。
- Summary Prompt只保留主题、明确结论、用户关注、比较关系、未决问题和指代实体，不得补充新事实，也不承担语言或范围等硬约束。
- 摘要模型失败或返回空白时记录错误、保留旧摘要并继续当前Follow-up；摘要和近期历史仍是不可信数据而非Evidence。
- 新增9项摘要测试，原Streaming和Report不变契约继续通过。

## v0.17.0 Task Memory任务内持久要求

- 新增`research_task_memories`，字段包含`research_task_id/category/content/status/created_from_message_id/updated_from_message_id/created_at/updated_at`；`research_tasks`补充`task_memory_error/task_memory_updated_at`。
- Category固定为`preference/persistent_instruction/task_focus/task_decision`，内容保持自然语言；当前只读active项，取消后标记removed，完整历史仍由Raw Messages承担。
- `TASK_MEMORY_EXTRACTOR_SYSTEM_PROMPT`输入只包含当前任务active Memory、最近4条有效消息和当前User Message；使用`ask_deepseek_json()`的temperature 0、thinking disabled与JSON Object模式。
- 操作契约为ADD/UPDATE/REMOVE或空数组NOOP。Validator拒绝非法字段、过长内容、论文事实和数字、非active ID、跨任务ID及一次性要求；DB在`BEGIN IMMEDIATE`事务内再次校验来源消息和任务归属。
- 当前User消息先更新Memory，再构造`research-followup-context-v4`；优先级为System > Current Explicit User Intent > Task Memory > Summary/Recent。Memory、Summary和Recent都不是Evidence。
- Extractor/API/JSON/Validator/DB失败时记录错误并返回旧active Memory，当前DeepSeek Streaming继续；Evidence Retrieval、Citation与Research Report均不改变。
- 新增17个测试实例，完整离线回归`268 passed`。真实浏览器验收验证持久语言偏好、UPDATE立即生效、一次性覆盖不持久、下一轮恢复和Report不变。
- `_looks_like_paper_fact()`不再用“分析/研究/作为基线”等宽泛词豁免已命中的论文事实句；明确论文事实会被Validator拒绝，合法“比较各论文数据集”`task_focus`和“以Paper B为基线”`task_decision`仍可写入。
- 2026-08-26定向验证：新增4个边界测试全部通过；真实DeepSeek共12次调用，论文事实、合法focus、合法decision各`3/3`，Recent越权仅`1/3`正确。生产链路因Current Message无持久性标记而在调用Extractor前NOOP，但Prompt单独稳定性仍是待修项。
- 最终混合来源测试：Recent为“以后都用英文回答”、Current为“接下来重点比较各论文使用的数据集”时，三次均同时ADD英文`persistent_instruction`和当前`task_focus`，严格结果`0/3`；Validator没有来源校验并全部接受。该场景会绕过普通问题前置NOOP，因此Task Memory v1尚不能封版，下一步应解决操作必须由Current Message触发的可验证来源约束。
- 当前边界：没有跨任务User Memory、长期用户画像、向量Memory、TTL、Semantic Dedup、Memory UI或Report Update。

## v0.17.0 Unified Follow-up ContextBuilder

- 新增`followup_context.build_followup_context()`纯装配组件，统一输出`research-followup-context-v4`；字段顺序固定为分类信息、Task Memory、Summary、Recent、Current Question、Existing Report、Evidence和Access Limitations。
- 原`build_research_followup_prompt()`保留兼容签名，只委托ContextBuilder并序列化JSON；实时与Worker两条Follow-up路径无需改调用方式。
- Builder不接收`task_id`，不查询数据库、不执行Retrieval、不更新Memory/Summary/Report、不调用DeepSeek或Citation Validation；上游已有结果只消费一次。
- 当前User问题会从Recent中的同内容User消息去重，pending/failed消息被防御性过滤；各来源保持独立字段，Evidence仍是论文事实主要依据。
- 新增15项专项测试，覆盖完整/空值装配、问题去重、状态过滤、任务隔离、字段边界、元数据、单次消费、兼容入口、Streaming和输入不变；完整离线回归`287 passed`。

## v0.17.1 Context / Memory Eval v1

- 在`E:\PaperHunterData\smolagent-tests\benchmarks\context_memory_eval_v1_cases.json`冻结29个场景；E盘本地脚本`scripts\evaluate_context_memory_v1.py`复用当前生产Prompt、ContextBuilder、Validator和DeepSeek模型，不进入生产请求链路，也不上传Git。
- 真实DeepSeek调用56次，严格场景`26/29`、单次运行`37/43`。目标能力：Recent指代`5/5`、Summary`4/4`、Memory持续`2/2`、临时覆盖`2/2`、Evidence Grounding`8/8`、Task隔离`1/1`、恢复`1/1`、Injection`3/3`。
- 8组消融中With Context=`8/8`、Without Context=`2/8`，目标能力净提高75个百分点；不得把该小规模结果描述成所有真实对话上的统计保证。
- Task Memory混合输入来源隔离仍为`0/3`：Recent英文要求被错误复制为新Memory，Validator无法证明操作由Current Message触发。该问题同时属于Prompt遵守不足和代码来源校验缺口，v1暂不封版。
- 英文回答引用格式有3/33次使用`[Paper A, p.4]`或半角逗号，Citation Contract=`90.91%`；Memory本身仍正确执行，报告必须分开描述能力与格式。
- 生成结果位于`E:\PaperHunterData\smolagent-tests\outputs\context_memory_eval_v1_2026-08-27.{json,md}`。全部benchmark案例、历史未提交测评资产和pytest缓存均已迁至E盘，仓库原路径仅通过Junction保持兼容；未来Eval也必须使用该E盘根目录。

## 已知环境信息

- 工作区位于 Windows 中文路径下。
- 推荐使用 `uv run ...` 启动和测试，减少虚拟环境与路径编码问题。
- Git 曾因仓库所有者不同配置过 `safe.directory`。
- 文档修改不涉及代码时通常不必重新运行测试；代码有变化时应执行 `uv run pytest`。
- 当前版本为`v0.17.1`：A1-A7与B1保持完成；报告后追问支持真实流式响应、最近10条原文、滚动摘要、任务级Task Memory和统一ContextBuilder。Context / Memory真实评测已完成，Task Memory因Recent来源越权暂未封版；跨任务长期用户画像、向量记忆和动态Token窗口尚未开始。
- `pdf_service.parse_paper_pdf()`不传策略时使用MinerU；`extract_pdf_chunks()`及`chunk_size/overlap`只属于显式受控实验路径。
- `rag_service.retrieve_relevant_chunks()` 接受并归一化 `semantic_weight`、`keyword_weight`。
- `evaluation_service.validate_experiment_config()` 校验范围并推导关键词权重；`create_experiment_run()` 保存配置；`run_configured_experiment()` 重新解析、索引并执行评测。
- `v0.8.0` 已提供 `length_boundary`、`academic_section` 与 `academic_section_no_prefix`；本地 E3a/E3b/E3c/E4/C1/C2/C3/C4/C5 已提供固定英文查询、BM25实验、中英文RRF、DeepSeek Top-20重排、`paper_section_sentence`、`paper_parent_child`、`paper_table_context`、`paper_semantic_breakpoint_global` 和公平固定边界基线。论文分块、候选融合与重排实验已完成，下一步是生成质量评测。
- 12 篇、60 题当前正式结果：固定边界 `#5` 的 `Hit@1/3/5=43.33%/66.67%/81.67%`、`MRR@5=0.5747`；章节感知 `#6` 的 `Hit@1/3/5=50.00%/66.67%/75.00%`、`MRR@5=0.5950`。旧 3 篇结果不得参与当前结论。
- `2026-07-13` 真实 API 连通性测试通过：`doubao-embedding-vision-251215` 返回 2048 维向量，`deepseek-v4-pro` 正常生成答案；不得在文档或 Git 中保存真实 Key。
- `v0.5.0` 至 `v0.6.0` 的 3 篇、15 题及 `900/120` 参数结果仅为历史实验，当前结论统一以 12 篇、60 题运行 `#5/#6` 为准。
