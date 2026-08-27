# Paper Hunter：文献检索与开放获取下载助手

> 当前版本：`v0.17.1`
>
> 更新日期：`2026-08-27`
>
> 版本历史：见 [CHANGELOG.md](CHANGELOG.md)
>
> 实验过程：见 [EXPERIMENTS.md](EXPERIMENTS.md)
>
> 项目全景：见 [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)

Paper Hunter 是一个面向新手、适合写入简历的 FastAPI 项目。用户输入关键词或论文标题后，可以在首页从统一白名单提供的21个论文来源中多选，系统再通过统一论文搜索MCP检索相关论文并保存网页地址和元数据；如果发现明确可免费获取的 PDF，则在用户确认后由后台任务下载到本地。

`v0.17.1`完成Context / Memory Eval v1：冻结29个Recent、Summary、Task Memory、Evidence隔离、任务隔离、重启恢复和Prompt Injection场景，真实调用`deepseek-v4-pro` 56次。严格场景通过`26/29`；8组对照中With Context为`8/8`，Without Context为`2/8`。Evidence Grounding、指代、Summary、持续偏好、临时覆盖、任务隔离和恢复目标均为100%，但Task Memory混合输入的Recent来源隔离连续`0/3`，英文回答引用契约为`30/33`，因此Context组合可展示但Memory v1暂不封版。该轮案例、脚本、临时数据库和原始结果统一保存在`E:\PaperHunterData\smolagent-tests\`且不进入Git。

`v0.17.0`完成任务内多轮上下文工程：最近10条有效消息保留原文，更早历史由DeepSeek滚动摘要；Task Memory独立保存后续仍应持续生效的任务内要求。新增纯装配`followup_context.build_followup_context()`，把上游已经准备好的Memory、Summary、Recent、Current Question、Report、Evidence和Access Limitations稳定组织为`research-followup-context-v4`，不重复查询数据库、检索证据或调用模型。当前已知Recent来源越权仍使Task Memory暂不封版；本版不实现跨任务长期用户画像或向量记忆，完整离线测试`287 passed`。

`v0.16.0`把报告生成后的追问升级为同页实时流式对话。浏览器提交追问后立即追加用户消息和助手占位消息，后端重新召回已选论文证据并通过DeepSeek真实流式API逐段返回；完成后一次性保存完整回答、证据和模型信息，刷新页面仍可恢复。原跨论文报告不会被追问覆盖，旧持久Worker追问入口继续作为无JavaScript回退。本版不增加历史摘要、长期记忆或跨任务用户画像，完整离线测试`237 passed`。

`2026-08-24`已完成多来源搜索MCP的四步接入：首页来源多选由后端白名单渲染，默认全部勾选并支持全选、清空和零来源提交拦截；任务来源列表保存于`research_tasks.sources_json`，LangGraph Checkpoint携带执行副本，Worker恢复时以任务表为准；同一任务的2至4条Planner queries共用一次stdio MCP Session。搜索层只调用第三方`search_papers`，没有调用其下载Tool，也没有修改现有`PaperCandidate`、下载与报告流程。完整离线测试`224 passed`。

同日第5.1步根据真实联网验收兼容`paper-search-mcp==0.1.4`的`structuredContent.result`返回结构，并把`sources_used/source_results/raw_total/total`带入Checkpoint Tool Trace。相同问题和来源配置的复验任务均完成4条query、进入现有去重和Paper Selection；完整离线测试`233 passed`。全21来源和Agent记忆场景仍存在来源故障未完整上报、候选排序噪声等问题，当前不把这些质量问题包装成已解决。

`v0.15.0` 将生产环境的 PDF 默认解析器替换为 MinerU 云端精准解析 API。现有“解析 PDF”和“准备论文问答”入口不变，Worker 自动完成申请上传地址、上传 PDF、轮询云端任务、下载结构化结果并落库；章节、表格、公式、列表、页码和版面坐标会进入后续 Seed + TF-IDF 检索。系统不会在 MinerU 失败时静默切回 `pypdf`，避免同一功能产生不可预测的两套知识库；原 `pypdf` 分块仅保留给历史实验和受控对照。完整离线测试 `209 passed`，未调用真实 MinerU、DeepSeek、Seed 或论文源。

`v0.14.0` 将原来的普通搜索和智能研究入口合并为一个连续研究工作台。用户输入主题后先获得可修改的检索计划，确认后搜索并选择论文，再单独说明报告要回答的问题；报告完成后仍可在同一任务中基于所选论文证据继续追问。会话消息保存到SQLite，报告后追问通过持久Worker执行；报告轮询同时在浏览器和接口层禁用缓存，完成或失败后页面会自动重载。本版尚未加入历史消息摘要、长期记忆和自动上下文压缩。完整离线测试`205 passed`。

`v0.12.3` 完成A7.4真实stdio MCP Client冒烟验证，并结束整个A7阶段。新增`scripts/smoke_mcp_client.py`，使用当前Python解释器启动独立`my_agent_project.mcp_server`子进程，通过stdin/stdout完成协议初始化和Tool发现，不调用业务Tool。真实运行确认Server为`paper-hunter`、协议版本`2025-11-25`，并准确发现三个Tool；MCP专项`13 passed`、完整离线测试`157 passed`。

`v0.12.2` 完成A7.3 MCP论文证据检索Tool。新增`retrieve_paper_evidence(paper_id, question, top_k=3)`，通过现有Tool Registry读取已索引文本块并返回论文标题、合法访问链接、物理页码和评分。它不会自动下载、解析或索引论文；无证据时返回结构化失败。若Seed已配置，查询向量可能调用Embedding API并写入内部向量缓存，但不会修改论文正文或处理状态。MCP专项`12 passed`、完整离线测试`156 passed`。

`v0.12.1` 完成A7.2 MCP文献检索Tool。新增`search_literature(query, max_results=10)`，MCP层通过现有白名单Registry调用LangChain StructuredTool，复用关键词清洗、输入上限、arXiv/Crossref检索、来源统计及`partial/failed`降级，不复制搜索逻辑。该Tool只返回元数据，不写数据库、不下载PDF；实际调用会访问外部搜索源，离线测试使用mock。MCP专项`9 passed`、完整离线测试`153 passed`。

`v0.12.0` 完成A7.1最小MCP只读接入。新增独立`mcp_server.py`，通过官方MCP Python SDK暴露`get_paper_metadata` Tool；它复用`db.get_paper()`与现有访问链接规则，只返回公开论文元数据和状态，明确过滤本地文件路径。协议级内存Client测试真实执行`initialize -> list_tools -> call_tool`并验证标准Tool错误，专项测试`6 passed`、完整离线测试`150 passed`。本版没有开放下载、写库、外部检索或模型调用，A7.2再接`search_literature`。

`v0.11.4` 完成A6.5 Agent Evaluation Report与整个A6阶段。新增11条冻结场景清单、JUnit解析、`agent-evaluation-report-v1` JSON结构、可读文本渲染和原子文件写入；开发命令会运行A6.2/A6.3场景并把受控测试结果与A6.4运行指标分区展示。报告保存到Git忽略的`outputs/agent_evaluation/`，源码和测试进入仓库，生成产物默认不提交。本次使用`--skip-runtime-metrics`避免读取本地运行库，真实结果为`11/11 passed`；完整离线测试`144 passed`。

`v0.11.3` 完成A6.4 Agent Metrics。新增`agent_metrics_service.py`，从真实`research_tasks`、`research_reports`和`agent_traces`聚合任务完成率、Tool成功率、审批遵守率、Checkpoint恢复率、fallback成功率、任务/节点耗时及失败分布；每个比例都返回分子、分母、排除数和定义。等待用户操作的任务不会被算作系统失败，没有真实usage时Token与模型调用统计返回`null`。`GET /api/agent/metrics`提供内部JSON结果，本版不建设普通用户Dashboard。专项测试`4 passed`，完整离线测试`140 passed`。

`v0.11.2` 完成A6.3 Checkpoint恢复专项验证。`tests/agent/test_checkpoint_recovery.py`分别从计划确认、论文选择和报告确认三个`interrupt()`暂停点关闭旧SQLite连接并创建新图恢复；调用计数与Trace共同证明Planner、搜索、已处理论文和报告模型不会因恢复而重复执行。报告测试还会在第一篇论文准备完成后人为中断，再从第二篇继续。专项测试`3 passed`，完整离线测试`136 passed`。这只证明LangGraph业务状态恢复，不代表进程内`BackgroundTasks`能在服务重启后自动续跑。

`v0.11.1` 完成A6.2固定Agent场景测试。`tests/agent/test_scenarios.py`把8条关键可靠性契约集中成独立离线测试：Planner失败降级、人工拒绝后零Tool调用、非法Tool无副作用、单搜索源失败降级、单篇论文失败隔离、无合法证据零模型调用、原`thread_id`恢复不重跑Planner，以及重复下载/报告触发只允许一个请求取得执行资格。场景组`8 passed`，完整离线测试`133 passed`，全部使用mock和临时SQLite，不调用真实外部API。

`v0.11.0` 完成A6.1统一Agent Trace。两个LangGraph的节点都通过同一个`trace_node()`包装器记录`task_id/thread_id/node/status/起止时间/耗时/Tool摘要/重试次数/错误`，不在每个节点复制日志代码。首次规划发生在业务任务创建前，轨迹先按`thread_id`保存并在任务创建后回填；`interrupt()`记为`interrupted`，恢复后的同名节点形成新尝试。Trace失败采用尽力记录，不改变原节点结果或异常。模型调用次数和Token字段只保留数据库位置，在API尚未可靠返回并持久化usage前保持空值。

`v0.10.1` 为用户触发的PDF下载增加数据库原子抢占。普通下载只能用一条条件`UPDATE`把`available`改成`downloading`，失败重试只能从`failed`进入；只有实际更新一行的请求才会创建`BackgroundTasks`。因此快速双击、两个页面同时提交或“仅下载”和“准备问答”同时提交时，不会为同一篇论文重复启动下载。该规则直接使用SQLite事务和受影响行数，以后迁移MySQL仍可保留。

`v0.10.0` 在原搜索表单中加入LangGraph“智能研究”模式。DeepSeek先返回2至4条结构化学术检索词，生产图使用`SqliteSaver + thread_id + interrupt() + Command(resume=...)`在计划确认处原生暂停；确认后才调用现有arXiv/Crossref工具。检索完成后图在论文选择处第二次暂停，用户只能从当前任务候选中冻结1至5篇，系统不会自动下载。检查点保存在独立SQLite文件中，应用重启后使用同一`thread_id`恢复，不重新规划或重复已完成节点。

A2把检索能力封装为LangChain标准`StructuredTool`：工具注册表只开放`search_literature`，参数Schema限制查询词与结果数量，输出统一包含候选论文、来源计数、状态、错误和耗时。LangGraph保存调用摘要，后台在第二次中断后将其写入`research_tool_calls`，任务详情与状态API均可回放工具轨迹。

A3把第二次人工选择后的论文接入原有RAG链路。用户点击“生成研究报告”后，后台依次调用标准论文准备工具和证据检索工具；可取得全文的论文按物理页返回Top-3证据，无法下载或需要购买的论文只记录合法访问链接和限制说明。只有至少存在一条证据时才调用DeepSeek生成跨论文报告；没有证据时由代码直接拒答，不消耗生成Token。单篇准备或检索失败只影响当前论文，其余论文继续处理。

A4把A3报告阶段纳入同一个LangGraph检查点线程。论文选择完成后，图停在第三个`interrupt()`等待用户点击；恢复后按论文逐个执行准备节点和证据检索节点，每完成一篇就由`SqliteSaver`保存索引位置、证据和工具摘要。DeepSeek生成失败或应用重启后可从未完成节点继续，不重复已完成论文；报告完成后图停在“重新生成”中断点。代码还会校验报告中的`[论文标题, p. 页码]`是否来自已召回证据，该检查不调用第二个大模型。

A5-0/A5-1新增确定性的多论文Context Packer。系统保留全部原始召回证据用于审计，但DeepSeek默认只接收`fair_budget_v1`选出的上下文：总预算6000估算Token、每篇至少目标1条且最多2条，先轮询覆盖不同论文，再分配第二轮证据。报告保存压缩前后证据数、字符数、估算Token、论文覆盖、最低配额是否满足及每条删除原因；该过程不增加模型调用。

A5-2在预算分配前增加保守证据去重：同一篇论文中的规范化精确重复直接合并；只有同页或相邻页、文本重叠率至少`0.85`的较长证据才按高重叠重复处理，并保留检索分数更高的一条。不同论文绝不互相去重，每次删除都记录原因、重叠率和保留块引用。它是确定性的文本重叠规则，不是Embedding语义去重，也不增加外部API调用。

A5-3/A5-4完成结构化上下文和质量门禁。模型不再接收自由拼接文本，而是接收`research-context-json-v1`不可信JSON；系统Prompt禁止执行问题、标题、正文、访问原因或URL中的命令。证据进入模型前还要通过`context-quality-gate-v1`字段、页码、预算和计数检查，门禁失败时由代码拒答且不调用DeepSeek。门禁状态随LangGraph检查点持久化并展示在页面和API中。

`v0.9.0` 把原有下载、PDF解析和向量索引连接成用户触发的论文问答准备工作流。用户点击一次“准备问答”后，后台按当前状态依次执行缺失步骤，失败时停在对应阶段，再次点击可从失败位置继续；普通搜索、仅下载和单步调试入口继续保留。

`v0.8.2` 新增论文多粒度父子融合候选策略：固定父块和完整句子子块使用同一套缓存分别召回，再按共同父块做RRF融合。开发集在保持 `Hit@5=88.33%` 的同时，将 `Hit@3` 从 `66.67%` 提高到 `76.67%`。

`v0.8.3` 完成M1在`heldout_v2`上的一次性独立验证。M1相对固定边界使`Hit@1`从`50.00%`提高到`55.00%`、MRR从`0.6675`提高到`0.6925`、覆盖率从`87.04%`提高到`88.66%`，`Hit@5`保持`90.00%`，但预注册主指标`Hit@3`从`82.50%`下降到`77.50%`，其差值95%区间为`[-12.50%, 0.00%]`。因此M1未通过正式采用门槛，只保留为实验选项，普通解析继续使用固定边界。规则、指纹与完整结果见[HELDOUT_V2_DATASET.md](HELDOUT_V2_DATASET.md)。

## 当前策略与可复现结果

项目不是只展示一个RAG页面，而是用28篇论文、140道人工页码证据题，分别建立开发集和两套冻结独立集，对分块、混合召回、重排和生成质量进行分层验证。

| 层级 | 使用策略 | 可复现结果 | 当前状态 |
| --- | --- | --- | --- |
| 基础召回 | 固定边界`1200/150` + Seed语义向量 + TF-IDF | 在M1独立集达到`Hit@5=90.00%`、覆盖率`87.04%` | 默认稳定链路 |
| 列表重排 | DeepSeek将初召回Top-20重排为Top-5 | 在另一套8篇/40题独立集上，`Hit@3`由`67.50%`提高到`90.00%`，MRR由`0.6204`提高到`0.7675` | 已验证、问答页可选 |
| 自定义分块 | M1父块/句子子块双路RRF融合 | 开发集`Hit@3`提高10个百分点且`Hit@5`不变；新独立集`Hit@5`保持90%、覆盖率提高1.62个百分点 | 实验候选，不替换默认 |
| 生成评测 | 完整Top-K证据 + DeepSeek受约束回答与裁判 | 独立集Answer relevance由`99.00%`提高到`100.00%`，同时记录Faithfulness与引用正确性 | 已验证 |

M1的核心不是简单缩短文本块：系统同时索引约1200字符父块和约600字符完整句子子块，分别召回Top-20，再按共同父块ID执行RRF，最后返回上下文完整的父块。它证明了多粒度候选可以改善部分排序和覆盖，但独立集`Hit@3`下降，因此项目没有事后修改门槛或只报告有利指标。这套“冻结数据、配对bootstrap、缓存复跑、严格禁止静默降级”的实验治理，也是项目的重要工程成果。

`v0.8.1` 修复了生成评测的证据一致性问题：新建检索评测运行会把完整 Top-K 文本块保存到 SQLite，后续 DeepSeek 生成评测看到的证据与 Hit@K 计算使用的证据保持一致。普通网页 RAG 原本就直接使用完整检索块，不受该缺陷影响；旧实验记录仍保留作审计，修正版结果需要通过新运行重新生成。

在 12 篇论文、60 道人工问题的修正版复核中，完整证据落库后的检索命中排名达到 `0 mismatch`；DeepSeek 重排使 Answer relevance 从 `97.00%` 提高到 `99.83%`，配对 bootstrap 95% 区间为 `[+0.50,+6.83]` 个百分点。Reference correctness 同时提高3个百分点，但区间仍跨0，因此不作稳定提升声明。

在额外8篇论文、40道冻结问题的独立未见集上，重排继续把Hit@3从`67.50%`提高到`90.00%`、MRR从`0.6204`提高到`0.7675`，并使完整证据生成的Answer relevance从`99.00%`提高到`100.00%`；三项配对区间均不跨0。该测试集已经关闭，不再用于后续调参。

第一版优先保证稳定与合规：

- arXiv 的 PDF 地址格式稳定，因此优先识别为可下载来源，但需要用户点击下载按钮。
- Crossref 的链接结构较复杂，只有遇到明确的 `.pdf` 绝对直链才下载，否则保存 DOI 或网页地址。
- “仅下载”和“失败重试”通过SQLite持久队列与独立Worker执行，搜索结果不必等待下载完成，Web重启也不会丢失已入队任务。
- 单篇论文下载失败不会中断其他论文的处理。

## 主要功能

- 通过网页输入关键词或论文标题。
- 首页只有一个研究工作台入口；输入主题、论文标题或问题后，LangGraph生成可调整的检索计划，经用户确认才执行搜索。
- 在计划确认前可通过同一对话区补充范围并重新规划；选择论文后，报告问题与最初搜索主题分开保存。
- 报告完成后可继续提问：网页主链路重新召回所选论文页码证据，并结合任务滚动摘要、最近10条消息和当前报告调用DeepSeek真实流式回答；Worker保留为无JavaScript回退。
- 多路检索完成后由用户从候选结果中选择最多5篇论文，冻结后续解析和跨论文研究的输入范围。
- 使用LangGraph原生SQLite Checkpointer保存图状态；两个审批点均由`interrupt()`暂停并通过`Command(resume=...)`恢复。
- 用户确认论文集合后可显式启动跨论文报告；系统复用下载、解析、索引和RAG检索服务，输出带论文标题与物理页码的证据报告。
- 报告准备、逐篇检索、生成和引用检查均属于同一LangGraph线程；应用重启后可从最后一个检查点继续。
- 生成报告前使用跨论文公平配额和总Token预算整理上下文，原始证据与最终模型上下文分别保存，便于审计。
- 全文受限论文不会被模型猜测内容，而是显示购买或机构订阅说明及合法访问链接；全部论文均无证据时不会调用DeepSeek。
- 通过统一MCP按任务的明确来源列表检索论文；首页默认全选后端白名单提供的21个来源，升级前旧任务回退arXiv和Crossref。
- 保存标题、作者、摘要、发表时间、来源、出版商、DOI、网页 URL 和 PDF URL。
- 将论文元数据和下载状态保存到 SQLite。
- 为具有明确 PDF 地址的论文显示下载按钮，由用户决定是否下载。
- 点击下载后在后台执行任务，并在历史页查看 `available`、`downloading`、`downloaded`、`link_only` 和 `failed` 状态。
- 将记录导出为 CSV 或 JSON。
- 清洗论文标题中的非法文件名字符，避免 Windows 保存失败。
- 统一使用 UTF-8 输出，减少中文路径和日志乱码。
- 导出适合 Origin 的统计数据，并可选调用 Origin/OriginPro 自动绘图。
- 接入 DeepSeek API，实现基于论文原文证据的 RAG 问答。
- 论文问答可按题开启 DeepSeek 重排：从初召回 Top-20 中重新选择 Top-5；默认关闭，失败时自动退回原始排序并保存降级原因。
- 支持 Seed1.6 `Doubao-embedding-vision-251215` 稠密语义向量与本地 TF-IDF 的双路混合召回，同时兼容旧文本 Embedding 接口。
- Seed API 未配置或暂时失败时自动降级为本地 TF-IDF，不影响论文问答主流程。
- 支持通过 MinerU 云端精准解析 API 处理已下载 PDF，将章节、表格、公式、列表、物理页码和版面坐标保存到 SQLite。
- 对未取得全文的论文提示可能需要购买或机构订阅，并提供论文原始链接。
- 下载与解析状态每 2 秒自动刷新，无需手动刷新整个页面。
- 保存并展示论文实际下载成功日期。
- 历史页可清空数据库记录，同时保留本地 PDF 文件。
- 内置分块检索评测页，计算 Hit@K、Recall@K、MRR 和证据覆盖率，并绘制当前运行与历史运行曲线。
- 支持在评测页配置块大小、重叠、Top-K 和 Seed/TF-IDF 融合权重；每次实验按配置差异复用或重建分块与索引。
- 支持 `length_boundary` 固定边界、`academic_section` 论文章节感知和 `academic_section_no_prefix` 章节前缀消融策略，并保存每次实验的策略、参数、章节数和指标。
- 支持 `paper_multi_granularity` 候选策略：固定父块与句子子块各自召回Top-20，按父块ID融合去重后返回父块证据。
- 已通过受控消融验证章节路径前缀：在相同 794 个块上使 Hit@5 提高 `8.33` 个百分点、MRR@5 提高约 `11.95%`。
- 内置 12 篇论文、60 道人工标注问题的平衡检索基准，每篇包含五种问题类型各 1 题。
- 使用额外8篇论文、40道冻结问题完成独立未见集验证，并在验证结束后关闭该测试集，防止继续调参造成测试泄漏。

## 快速开始

### 1. 安装 uv

本项目使用 `uv` 管理 Python 环境和依赖。安装方式请参考 [uv 官方文档](https://docs.astral.sh/uv/)。

### 2. 配置 MinerU 默认解析器

复制 `.env.example` 为 `.env`，至少填写：

```text
MINERU_API_TOKEN=你的 MinerU API Token
```

Token 只由后端 Worker 读取，不会发送到浏览器。每次生产解析都会把对应 PDF 上传到 MinerU 云端；如果论文涉及隐私、保密或第三方数据处理限制，请不要提交。未配置 Token 时搜索和下载仍可运行，但 PDF 解析会明确失败，不会静默使用旧解析器。

### 3. 启动 Web 服务

在项目根目录运行：

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

然后在浏览器打开：

```text
http://127.0.0.1:8001/
```

如果 `8001` 端口已被占用，可以换成其他端口，例如：

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8002
```

### 4. 启动任务 Worker

另开一个终端，在同一项目根目录运行：

```powershell
uv run python -m my_agent_project.worker
```

Web负责校验请求并把论文处理、问答、研究搜索和研究报告任务持久化到SQLite，Worker负责真正执行。两者分开后，即使Web服务重启，已经入队的任务仍然存在；Worker进程异常退出时，超过租约时间的任务会由后续Worker重新领取。只处理当前已经到期的任务并退出，可用于调试：

```powershell
uv run python -m my_agent_project.worker --once
```

当前用户研究闭环中的下载、失败重试、完整准备、单独解析、单独索引、单篇RAG问答、智能研究搜索和跨论文报告全部走持久Worker。离线评测页面仍使用FastAPI `BackgroundTasks`，不属于面向用户的长任务闭环。

### 5. 运行测试

```powershell
uv run pytest
```

### 6. 生成Agent评测报告

运行冻结的11条可靠性场景，并组合当前本地运行Trace指标：

```powershell
uv run python scripts/run_agent_evaluation.py
```

只生成受控场景报告、不读取本地运行SQLite：

```powershell
uv run python scripts/run_agent_evaluation.py --skip-runtime-metrics
```

JSON与文本报告保存到`outputs/agent_evaluation/`。该目录由`.gitignore`排除；报告可能包含本地运行统计，不应默认提交公开仓库。

## 使用方法

统一研究工作台的完整流程：

1. 在首页输入主题、论文标题或研究问题，系统创建一个研究任务并生成2至4条检索计划。
2. 在任务页补充研究范围可更新计划；只有点击确认后才执行arXiv/Crossref检索。
3. 从本次任务的候选论文中选择1至5篇，冻结后续报告与追问的论文范围。
4. 在对话区单独输入报告要回答的问题，再点击“生成研究报告”。Worker准备全文、检索页码证据并生成报告。
5. 报告完成后继续在同一对话区提问；支持同页实时流式显示，完成后答案持久保存，刷新页面仍可恢复。

候选论文仍可从历史页进入单篇详情。对明确可下载的论文点击“准备问答”后，Worker会按`download -> parse -> index`处理，页面每2秒刷新状态；进入`ready`后也可使用原有单篇RAG问答。

当前会话边界：页面保存并展示全部原始消息；上游先分别准备Active Memory、滚动摘要、最近10条有效消息、当前问题、报告、证据和访问限制，再由统一ContextBuilder一次性装配成不可信JSON交给模型。优先级为System规则、当前明确要求、Task Memory、Summary/Recent；论文事实优先级为Evidence、Existing Report、Conversation Context。当前没有动态Token窗口、跨任务用户画像、向量记忆或Memory UI。

下载状态说明：

- `downloading`：已经进入后台下载任务。
- `available`：已经发现可下载 PDF，等待用户点击下载按钮。
- `downloaded`：PDF 下载成功，本地路径已经保存。
- `link_only`：没有明确可下载的 PDF，只保存网页或 DOI 地址。
- `failed`：下载失败，数据库中会记录错误原因。

### MCP只读服务

本地MCP Host可通过标准输入输出启动A7.1服务：

```powershell
uv run python -m my_agent_project.mcp_server
```

当前注册两个Tool：

- `get_paper_metadata(paper_id)`：读取Paper Hunter数据库中的既有论文记录，返回标题、作者、摘要、来源、出版商、DOI、公开访问链接和处理状态；不会返回`local_path`。
- `search_literature(query, max_results=10)`：调用现有arXiv/Crossref元数据检索工具，返回候选论文、来源计数、状态、错误和耗时；不写数据库、不下载PDF，但实际调用需要网络。
- `retrieve_paper_evidence(paper_id, question, top_k=3)`：从已索引论文读取带物理页码的Top-K证据；不会自动准备论文，无证据时明确失败。Seed可用时可能调用Embedding并更新查询向量缓存。

该命令启动后会等待MCP客户端通信，不会像普通脚本一样打印页面地址。

验证独立stdio连接和Tool清单：

```powershell
uv run python scripts/smoke_mcp_client.py
```

该冒烟命令只初始化协议并列出Tool，不查询数据库记录、不访问论文搜索源，也不调用Embedding或大模型；成功时输出JSON，失败时返回非零退出码。

## 分块评测使用方法

评测页面用于检验“用户提问后，系统能否把包含正确答案的论文文本块排到前面”。它只评测分块、Embedding 和召回排序，不调用 DeepSeek 生成答案，因此不会把大模型回答能力混入检索指标。

打开：

```text
http://127.0.0.1:8001/evaluation
```

第一次使用时按以下顺序操作：

1. 将待评测的 PDF 放入 `downloads/papers/`。
2. 在 `benchmarks/chunking_baseline.csv` 中填写论文标题、问题类型、问题、参考答案、证据物理页码和连续证据原文；当前文件已包含 12 篇论文、60 条案例。
3. `paper_title` 应与 PDF 文件名或系统论文记录标题尽量一致；`evidence_page` 使用 PDF 阅读器显示的物理页码，而不是论文正文印刷页码。
4. 打开“分块评测”页面，点击“导入并准备基准”。系统会注册本地论文、解析 PDF、分块并建立向量索引。
5. 等待页面显示全部案例已建立索引，在实验配置中选择固定边界、论文章节或章节无前缀对照策略，并填写实验名称、块大小、重叠、Top-K 和 Seed 语义权重。
6. 点击“运行实验”。系统会按 PDF 内容哈希和实验参数自动复用页文本、分块、论文索引及问题向量，只计算发生变化的部分。
7. 查看总体指标、Top-K 曲线和逐题结果；展开召回片段可以检查具体内容。

CSV 必须保留以下表头：

```text
paper_title,question_type,question,reference_answer,evidence_page,evidence_text
```

每次实验都会创建新的 `evaluation_runs` 记录，保存分块策略、块大小、重叠、Top-K、Embedding 模型、融合权重、缓存命中统计和总耗时。当前运行曲线展示 Hit@1、Hit@3、Hit@5；历史趋势图按运行编号比较结果。`academic_section` 会识别论文标题层级、过滤参考文献、保留附录，并把章节路径写入文本块；`academic_section_no_prefix` 复用相同块布局但只用正文建立索引，用于隔离章节前缀效果；固定边界策略继续作为对照基线。

实验缓存按变化范围失效：只修改 Top-K 或融合权重时直接复用当前分块、索引和问题向量；修改分块策略、块大小或重叠时复用 PDF 页文本和问题向量，仅重新生成文本块并补算缓存中不存在的块向量；替换 PDF 或 Embedding 模型时才重建对应缓存。

`v0.7.0` 真实验证中，缓存预热运行 `#7` 用时 `105.876` 秒，全缓存同配置运行 `#8` 用时 `4.406` 秒，约快 `24.0` 倍；`#8` 命中 794 个分块、12 篇论文索引和 60 个问题向量，Seed 请求为 0，且各项检索指标与 `#7` 完全一致。完整步骤见 [EXPERIMENTS.md](EXPERIMENTS.md)。

## 目录结构

```text
.
|-- my_agent_project/
|   |-- main.py
|   |-- config.py
|   |-- schemas.py
|   |-- search_service.py
|   |-- paper_search_mcp_service.py
|   |-- download_service.py
|   |-- mineru_service.py
|   |-- paper_workflow.py
|   |-- context_service.py
|   |-- agent_trace_service.py
|   |-- agent_metrics_service.py
|   |-- agent_evaluation_service.py
|   |-- mcp_server.py
|   |-- research_agent.py
|   |-- research_checkpoint.py
|   |-- research_tools.py
|   |-- research_workflow.py
|   |-- research_report_service.py
|   |-- db.py
|   |-- model_service.py
|   |-- pdf_service.py
|   |-- rag_service.py
|   |-- evaluation_service.py
|   |-- origin_service.py
|   |-- templates/
|   `-- static/
|-- scripts/
|   |-- run_agent_evaluation.py
|   `-- smoke_mcp_client.py
|-- tests/
|-- benchmarks/
|   `-- chunking_baseline.csv # 60 条人工标注检索案例
|-- downloads/              # 运行时生成，不提交到 Git
|-- AGENTS.md                # 项目背景和后续对话记忆
|-- ROADMAP.md               # 后续路线与逐步实施日志
|-- ORIGIN.md                # Origin 绘图说明
|-- PROJECT.md               # 项目概述和简历描述
|-- pyproject.toml           # 依赖与测试配置
|-- uv.lock                  # 锁定依赖版本
`-- README.md
```

## 模块说明

### `main.py`

FastAPI 应用入口，负责注册路由、创建统一研究任务、校验人工操作、持久化后台任务并返回 HTML 页面；长任务由独立Worker执行。

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
POST /papers/{paper_id}/download
POST /papers/{paper_id}/prepare
POST /papers/{paper_id}/parse
POST /papers/{paper_id}/index
POST /papers/{paper_id}/ask
POST /papers/clear
GET  /api/papers/statuses
GET  /export.csv
GET  /export.json
GET  /origin
GET  /evaluation
POST /evaluation/prepare
POST /evaluation/run
GET  /evaluation/runs/{run_id}
```

### `config.py`

负责项目路径和运行环境初始化，创建 `downloads/`、`downloads/papers/`、`downloads/exports/`，并处理 Windows UTF-8 标准输出。

### `schemas.py`

定义各模块共享的数据结构 `PaperCandidate`，以及四种下载状态常量。

### `search_service.py`

保留`search_all()`单query兼容入口，并通过`search_many()`把任务queries批量交给论文搜索MCP。旧arXiv XML和Crossref JSON解析函数只作为离线测试夹具保留，不再属于生产搜索fallback。

该模块只负责“查找论文和解析信息”，不负责把文件写入本地。

### `paper_search_mcp_service.py`

负责启动并管理第三方论文搜索MCP的stdio Session，只调用统一`search_papers`。同一任务的多条query复用一个Session，来源必须是明确的白名单ID；返回结果在本层映射为现有`PaperCandidate`，候选PDF地址不在搜索阶段联网验证。

### `download_service.py`

负责下载 PDF、清洗文件名、处理同名文件、更新数据库状态，以及捕获单篇论文的下载异常。`raise_on_error=True`只供可靠Worker使用，使下载服务在记录论文失败状态后把异常交给队列判断是否重试；其他旧调用保持原来的异常隔离行为。

文件名清洗的核心规则是：

```python
re.sub(r'[\\/*?:"<>|]', "_", title)
```

### `task_queue_service.py`

负责B1持久任务的业务分发，不复制下载、解析、索引或RAG问答实现：

- `enqueue_download_job()`在数据库层把论文状态抢占与任务入队放进同一事务，避免状态已改但任务没有创建。
- `enqueue_paper_preparation_job()`按论文当前状态原子锁定下载、解析或索引阶段，并创建`prepare_paper`任务；已完成阶段由原工作流自动跳过。
- `enqueue_parse_job()`与`enqueue_index_job()`迁移详情页单步操作；活动论文任务统一互斥，完成后仍允许用户显式重新处理。
- `enqueue_rag_answer_job()`在同一事务中创建`rag_queries`记录和`answer_rag_query`任务；同一论文、问题和重排模式的活动重复请求返回原任务，不重复消耗模型Token。
- `run_one_job()`通过租约领取一个到期任务，调用注册处理器，并按尝试次数进入`succeeded`、`retry_wait`或`failed`。
- 下载、准备或问答失败使用指数退避；达到`max_attempts`后停止重试并同步当前阶段失败原因。
- `recover_expired_jobs()`回收Worker中断后超过租约的`running`任务；仍有尝试次数时重新排队，否则最终失败。
- `_maintain_job_lease()`在处理器执行期间启动守护心跳；默认每30秒或租约三分之一续期，任务结束后立即停止。

### `worker.py`

独立Worker进程入口。它轮询SQLite队列、回收过期租约并调用`task_queue_service`执行任务，不承载HTTP请求。当前注册`download_paper`、`prepare_paper`、`parse_paper`、`index_paper`和`answer_rag_query`；各处理器复用原Service，不在Worker中复制业务实现。默认租约为90秒，任务执行期间由心跳线程自动续期；只有持有未过期租约的当前Worker才能续租。心跳模式要求租约至少2秒，且续期间隔必须短于租约。

### `paper_workflow.py`

负责把已有下载、PDF解析和向量索引服务编排成一次用户触发的准备流程，不复制各模块内部实现：

- `paper_preparation_status()`：根据论文、解析和索引记录推导统一准备状态。
- `mark_preparation_started()`：保留的旧流程状态抢占辅助函数；Web主路由已改用原子入队函数。
- `prepare_paper_for_qa()`：从尚未完成的步骤继续执行；下载、解析或索引任一步失败时立即停止。
- 已下载论文跳过下载，已解析论文跳过解析，已完成索引直接返回 `ready`。
- 没有明确 PDF 直链的 Crossref 记录保持 `link_only`，不会进入自动处理流程。

### `context_service.py`

负责在报告生成前构造受预算约束的多论文上下文：

- `ContextConfig`：默认`max_estimated_tokens=6000`、`min_chunks_per_paper=1`、`max_chunks_per_paper=2`。
- `estimate_text_tokens()`：中文字符按1个Token、其他非空白字符约4个折算1个Token；这是可复现估算，不冒充DeepSeek官方Tokenizer。
- `normalize_evidence_text()`：使用NFKC、大小写折叠和空白归一化，为精确重复判断生成稳定文本。
- `evidence_overlap_ratio()`：使用5-token shingle containment计算文本覆盖率；证据过短时不触发高重叠去重。
- `deduplicate_paper_evidence()`：同一论文内保留高分候选；精确重复不限页距，高重叠重复只比较同页或相邻页，阈值为`0.85`。
- `pack_research_context()`：先按论文独立去重，再按轮次跨论文选择证据，防止第一篇论文占满上下文。
- `evidence_validation_errors()`：在去重和预算分配前拒绝缺少论文ID、块ID、标题、正物理页码或正文的证据。
- 输出同时包含最终`context_evidence`和`context_stats`；未选证据记录`exact_duplicate`、`overlap_duplicate`、`token_budget`或`paper_quota`原因。
- 重复审计包含`overlap_ratio`和`duplicate_of`，可以定位被删除块及其对应的保留块；不同论文永远不会互相去重。
- `quality_gate`记录`passed/warning/failed`、五项检查、接受/拒绝数量和`model_call_allowed`；失败时不得调用模型。
- 极小预算无法满足每篇最低配额时不偷偷突破上限，而是将`minimum_quota_satisfied`设为`false`并列出论文。

### `agent_trace_service.py`

负责A6.1统一节点可观测性，不修改节点原有业务逻辑：

- `trace_node()`：集中包装LangGraph节点，记录`success/partial/interrupted/failed`状态、耗时、Tool摘要和异常。
- Trace只有在生产`configurable`显式开启时落库；普通直接构图和旧单元测试不会写入运行数据库。
- 第一次`interrupt()`形成中断轨迹，使用同一`thread_id`恢复后形成新尝试并递增`retry_count`。
- Trace写入失败时继续执行原节点；节点异常记录后仍抛出原异常。
- `model_calls/input_tokens/output_tokens`等待真实API usage贯通，当前保持空值。

### `agent_metrics_service.py`

负责A6.4内部运行指标聚合，不调用额外模型，也不修改业务数据：

- `build_agent_metrics()`读取任务、报告和Node Trace，返回`agent-metrics-v1`结构化结果。
- 所有比例同时返回`numerator/denominator/excluded/value_percent/definition`，分母为0时百分比为`null`。
- Task Completion只比较报告完成与系统失败；等待审批、等待选择、未生成报告和用户取消不进入分母。
- Approval Compliance只检查明确拒绝计划的线程是否保持零搜索；Checkpoint Recovery只统计确实发起后续尝试的中断。
- 节点耗时按Node分别输出平均值和样本数；`failed`与`partial`分开统计，避免把降级混同为崩溃。
- 模型调用和Token只聚合Trace中真实非空usage；当前未贯通usage时返回`null`并报告观测覆盖率。
- pytest场景结果没有写入运行数据库，因此`scenario_pass_rate`暂为`not_persisted`，由A6.5报告层单独提供。

### `agent_evaluation_service.py`

负责A6.5评测报告建模、渲染与保存，不在FastAPI请求中运行pytest：

- `AGENT_SCENARIOS`冻结A6.2的8条可靠性场景和A6.3的3条Checkpoint恢复场景。
- `parse_junit_scenario_results()`解析pytest JUnit XML，区分`passed/failed/skipped/not_run`并保留失败原因和耗时。
- `build_agent_evaluation_report()`生成`agent-evaluation-report-v1`，受控场景与真实运行指标分别保存。
- `render_agent_evaluation_text()`输出包含样本数、分子/分母、失败场景、延迟和限制说明的文本报告。
- `save_agent_evaluation_report()`原子写入带时间戳及`latest`版本的JSON和文本文件。
- `scripts/run_agent_evaluation.py`是开发命令入口；默认读取运行Metrics，`--skip-runtime-metrics`可只执行离线场景。

### `research_agent.py`

负责LangGraph研究检索编排，不复制现有搜索实现：

- `ResearchSearchState`：保存研究问题、结构化计划、人工确认标记、候选/已选论文、工作流状态和错误。
- `plan_research()`：调用DeepSeek JSON接口生成2至4条学术检索词，并校验数量、长度和重复项。
- `route_after_plan()`：未确认时进入`await_confirmation`；确认后再根据规划状态进入多查询或单查询降级节点。
- `plan_research_search()`：只生成计划并在检索前结束，不调用arXiv/Crossref。
- `run_confirmed_research_search()`：复用数据库中的已确认计划继续执行，不重复调用DeepSeek。
- `multi_query_search()` / `single_query_search()`：只通过白名单工具注册表调用`search_literature`，模型不能直接下载论文或绕过原下载策略。
- `deduplicate_results()`：按DOI、网页URL和规范化标题合并重复结果，执行用户指定的结果上限，并把图状态推进到`awaiting_selection`。
- `request_plan_confirmation()`与`request_paper_selection()`：只构造JSON可序列化审批载荷并调用`interrupt()`，中断前不执行非幂等副作用。
- `request_report_confirmation()`：第三个人工审批点；选完论文不会立即下载，只有点击生成报告后才恢复图。
- `prepare_report_paper_node()`与`retrieve_report_evidence_node()`：使用自循环节点逐篇推进，每篇完成后形成独立SQLite检查点。
- `begin_report_generation()`、`synthesize_research_report()`与`validate_report_citations_node()`：先暴露生成状态，再调用DeepSeek，最后执行零Token的引用来源校验。
- `begin_report_generation()`会先调用`pack_research_context()`；后续生成和引用检查只使用模型实际看到的`report_context_evidence`。
- `request_report_regeneration()`：报告完成后继续保留可恢复中断，使重新生成仍使用同一`thread_id`。
- `build_checkpointed_research_graph()`：编译生产图并注入SQLite Checkpointer；原直接运行入口只保留给兼容和单元测试。

### `research_tools.py`

负责把已有搜索、论文准备和RAG检索能力封装为标准Agent Tool，不复制底层实现：

- `search_literature`：LangChain `StructuredTool`，自动暴露`query`和`max_results`参数Schema。
- `prepare_paper_for_research`：接收论文ID，复用`prepare_paper_for_qa()`完成用户已批准论文的下载、解析和索引。
- `retrieve_paper_evidence`：接收论文ID、研究问题和Top-K，复用`retrieve_relevant_chunks()`返回物理页码证据。
- 工具输出包含标准化候选、`succeeded/partial/failed`、各来源结果数、错误摘要与毫秒耗时。
- `RESEARCH_TOOL_REGISTRY`：工具白名单；未知工具名由`invoke_research_tool()`直接拒绝。
- 工具参数由Schema约束，报告编排只能调用白名单能力；DeepSeek不直接控制下载、数据库写入或版权判断。

### `research_checkpoint.py`

负责LangGraph原生持久化和恢复：

- `get_checkpointed_research_graph()`：以`check_same_thread=False`创建线程安全的`SqliteSaver`并延迟编译生产图。
- `start_checkpointed_research()`：使用新的稳定`thread_id`启动图并暂停在计划确认。
- `resume_plan_confirmation()`：通过`Command(resume=...)`恢复同一线程，继续搜索并暂停在论文选择。
- `update_checkpoint_candidate_ids()`：把业务数据库生成的候选论文ID同步回暂停状态，供第二次恢复校验。
- `resume_paper_selection()`：恢复第二个中断并结束当前研究检索图。
- `stream_checkpointed_research_report()`：恢复第三个中断或未完成报告节点，并逐次返回已经持久化的完整图状态。
- `research_checkpoint_info()`：返回检查点ID、待执行节点和工作流状态，供状态接口审计。

### `research_workflow.py`

负责后台执行已经人工确认的研究任务：

- `execute_research_task()`读取SQLite中冻结的计划并恢复同一个LangGraph检查点；`raise_on_error=True`只供Worker识别失败并进行有限重试。
- 检索成功后复用`insert_papers()`归档论文，用`replace_research_tool_calls()`幂等保存工具轨迹，并把论文ID写回研究任务。
- 任意异常只将当前任务标记为`failed`并保存错误，不影响其他任务或普通检索。
- `execute_research_report()`识别当前检查点位置，逐节点同步报告状态、证据、工具摘要和完成数量；没有A4检查点的旧任务继续调用A3兼容入口。

### `research_report_service.py`

负责把已冻结论文集合连接到原有问答准备和RAG服务：

- `generate_research_report()`：按论文执行`prepare_paper_for_research → retrieve_paper_evidence`，汇总证据和访问限制，再调用DeepSeek生成报告。
- `build_research_context_payload()`：为问题、证据和访问限制生成`research-context-json-v1`，证据使用`E1...En`编号并保留来源元数据。
- `build_research_report_prompt()`：把载荷放入明确的不可信JSON数据区；系统Prompt只允许把字段作为研究数据读取，禁止执行正文中的命令式文本，并要求每个事实使用`[论文标题, p. 页码]`引用。
- `build_no_evidence_report()`：全部论文均无全文证据时由代码生成明确拒答和访问说明，不调用大模型。
- `build_context_gate_failure_report()`：存在输入证据但未通过质量门禁时生成可解释拒答，不消耗生成Token。
- `validate_research_report_citations()`：用正则和证据集合校验引用标题、物理页码及无效引用；它只检查引用来源结构，不冒充事实语义裁判。
- 每篇论文具有独立异常边界；一篇下载、解析、索引或检索失败不会中断其余论文。

### `db.py`

负责 SQLite 的全部数据读写，包括初始化表、持久化研究任务、插入检索结果、查询历史与详情、更新下载状态、缓存 PDF 页文本与 Seed 向量，以及导出 CSV/JSON。

`background_jobs`保存持久任务载荷、活动去重键、状态、尝试次数、可执行时间和Worker租约。`enqueue_download_job()`把论文状态转换与任务创建放在同一事务；领取、完成、失败和租约恢复也都使用数据库条件更新，不依赖Web进程内存。

`research_tasks`保存研究问题、结果上限、计划目标、查询列表、规划模型、规划/执行错误、`checkpoint_thread_id`、业务任务状态、候选论文ID、已选论文ID和各阶段时间。业务表用于列表与审计，LangGraph执行位置由独立Checkpointer负责。

`research_tool_calls`按任务和调用序号保存工具名称、JSON输入、执行状态、结果数、来源分布、错误与耗时；同一任务恢复时整体替换，避免重复审计记录。

`research_reports`按研究任务保存`preparing → retrieving → generating → completed/failed`状态、报告正文、模型、证据摘要、访问限制、工具调用摘要和阶段时间。`start_research_report()`使用事务锁防止重复提交。

`details_json.evidence`保存完整原始召回，`details_json.context_evidence`保存真正发送给DeepSeek的证据，`details_json.context_stats`保存`fair_budget_v1`配置、压缩率、论文覆盖和删除审计，`details_json.context_quality`保存质量门禁结果。

应用启动时会把中断的报告标记为可重试`failed`，并将没有活动持久任务的遗留`downloading`以及`parsing/indexing`阶段恢复成可继续状态；存在`queued/running/retry_wait`下载任务时必须保留`downloading`，由Worker租约负责恢复。

主要字段包括：

```text
id, query, title, authors, summary, published, source, publisher,
doi, page_url, pdf_url, local_path, status, downloaded_at, error,
created_at, updated_at
```

### `model_service.py`

统一管理 DeepSeek 模型能力，并吸收了原来内容很少的 `agent_service.py`：

- `is_deepseek_configured()`：判断是否已经配置 DeepSeek API Key 和模型。
- `enhance_query()`：保留稳定的搜索词入口，当前不会自动消耗模型额度。
- `ask_deepseek()`：使用 `httpx` 调用 DeepSeek `/chat/completions`，处理鉴权、超时、HTTP 错误和响应解析。
- `is_seed_configured()`：判断火山方舟 Seed Embedding 是否已配置。
- `embed_with_seed()`：根据模型自动调用火山方舟 `/embeddings` 或 `/embeddings/multimodal`，校验并按输入顺序返回稠密向量。
- `seed_embedding_cache_namespace()`：使用服务地址、模型和接口模式生成向量缓存命名空间，模型配置变化时自动隔离旧向量。
- `ModelConfigurationError`：缺少 `.env` 配置时给出明确提示。
- `ModelRequestError`：统一包装网络错误和 DeepSeek 返回错误。

### `pdf_service.py`

负责 RAG 的 PDF 预处理基础能力：

- `normalize_pdf_text()`：清理 PDF 提取文本中的空字符、重复空格和多余换行。
- `split_page_text()`：按页将正文切分为带重叠区域的文本块。
- `detect_section_heading()`：识别数字、罗马数字、IEEE 字母小节和附录标题，并排除常见正文/表格误判。
- `split_academic_page_text()`：跨页维护章节树，在章节内部继续生成定长子块，并过滤参考文献噪声。
- `pdf_source_hash()`：计算 PDF 的 SHA-256 内容指纹，文件变化后自动失效旧页文本。
- `extract_pdf_pages()` 与 `build_pdf_chunks()`：把昂贵的 PDF 文本提取和可重复的分块策略拆开，使不同策略共享同一份页文本。
- `parse_paper_pdf()`：生产调用不传实验策略时固定走 MinerU 云端结构化解析；显式传入实验策略时才使用历史 `pypdf` 分块链路。
- `extract_pdf_chunks()`：仅服务于受控实验，使用 `pypdf` 提取每页正文，再按指定实验策略生成带页码的文本块。
- `parse_paper_pdf()`：按 PDF 指纹和分块配置复用页文本或当前分块，只在缓存未命中时读取文件和生成新块。

### `rag_service.py`

负责后续大模型问答所需的访问提示和 Prompt 构造：

- `tokenize()` 与 `term_frequency()`：将中英文正文转换为本地稀疏词频向量。
- `index_paper_chunks()`：在后台建立 Seed 稠密向量和 `local-tfidf-v1` 关键词向量；Seed 不可用时自动降级。
- `embedding_content_hash()`：以文本内容哈希作为 Seed 向量缓存键，相同文本不重复调用 API。
- `cache_active_chunk_embeddings()`：升级或切换策略前将现有论文块向量回填到通用缓存。
- `retrieve_relevant_chunks()`：融合 Seed 语义相似度和 TF-IDF 关键词相似度，复用问题向量并返回相关页码和原文。
- `answer_rag_query()`：组合检索、Prompt、DeepSeek 调用和问答状态更新。
- `paper_access_url()`：优先返回论文网页地址，没有网页地址时生成 DOI 链接。
- `build_access_notice()`：在系统没有全文时提示读者可能需要购买、学校或机构订阅，并附上合法访问链接。
- `build_rag_prompt()`：把论文信息、页码、全文片段和用户问题组合成受约束的 RAG Prompt。
- `RAG_SYSTEM_PROMPT`：要求模型只根据已解析证据回答、标注论文和页码，不得假装读过未下载的付费全文，也不得提供绕过版权限制的方法。

### `origin_service.py`

从 SQLite 汇总来源、状态、出版商和年份数据，生成 CSV；本机安装 Origin/OriginPro 和 `originpro` 后，还可以自动创建图表。详细说明见 [ORIGIN.md](ORIGIN.md)。

### `evaluation_service.py`

负责分块实验：按论文标题导入本地 PDF 与人工标注 CSV，校验策略和实验参数，根据配置差异决定复用或重建分块与 Seed/TF-IDF 索引，再使用“同页 + 规范化 token 覆盖率”识别正确证据并聚合指标。`run_configured_experiment()` 同时记录页文本、分块、块向量、问题向量和论文索引的命中统计及耗时。评测只调用检索器，不调用 DeepSeek 生成答案。

### `templates/` 与 `static/`

`templates/` 保存 Jinja2 HTML 模板，`static/styles.css` 负责页面布局、表格、状态标签和响应式样式。

### `tests/`

包含不依赖网络的核心测试，覆盖 arXiv/Crossref 解析、PDF 下载策略、文件名清洗、来源异常降级、Embedding 请求、证据匹配、指标聚合和评测持久化等行为。

## 固定边界与论文章节实验

`benchmarks/chunking_baseline.csv` 保存 12 篇论文、60 个问题及其参考答案、PDF 物理页码和连续原文证据。每篇论文包含事实型、方法型、参数型、结果型和原因型各 1 题。当前默认参数为 `chunk_size=1200`、`overlap=150`、语义权重 `0.75`、关键词权重 `0.25`、`top_k=5`。

`v0.6.1` 使用完整 12 篇、60 题基准完成了同参数实验。旧 3 篇、15 题结果只作为历史记录，不参与本次判断。

| 策略 | 文本块 | 章节路径 | Hit@1 | Hit@3 | Hit@5 | MRR@5 | 平均最佳覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `length_boundary`（运行 `#5`） | 785 | 0 | 43.33% | 66.67% | 81.67% | 0.5747 | 79.25% |
| `academic_section`（运行 `#6`） | 794 | 195 | 50.00% | 66.67% | 75.00% | 0.5950 | 74.34% |

章节策略让 `Hit@1` 提升 `6.67` 个百分点、`MRR@5` 提升 `0.0203`，说明正确证据更容易排到第一位；但 `Hit@5` 降低 `6.67` 个百分点，说明它也把部分正确证据挤出了前五。60 题中排名改善 `17` 题、变差 `12` 题、持平 `31` 题；Top-5 新救回 `3` 题，同时丢失 `7` 题。检查确认丢失题的证据仍存在于章节语料中，因此这是召回排序问题，不是分块删除正文。

按题型看，方法型提升最明显（`Hit@1` 从 `33.33%` 到 `58.33%`，`MRR@5` 从 `0.5069` 到 `0.6625`），参数型也有改善；事实型和结果型的 Top-5 则下降。当前不把 `academic_section` 替换为默认策略，下一步优先测试跨语言查询扩展和重排序。

## 章节路径前缀消融

`v0.8.0` 在 `academic_section` 的相同 794 个文本块上进行受控消融。两组实验的论文、问题、页码、正文、块边界、`1200/150` 参数、Seed/TF-IDF 权重和 Top-K 均相同，唯一变量是建立索引时是否保留 `[Section: 父章节 > 子章节]`。

| 索引文本 | Hit@1 | Hit@3 | Hit@5 | MRR@5 | 证据覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 章节路径 + 正文（运行 `#8/#11`） | 51.67% | 66.67% | 75.00% | 0.6033 | 74.34% |
| 仅正文（运行 `#9/#10`） | 43.33% | 63.33% | 66.67% | 0.5389 | 65.47% |

章节前缀让 14 题排名提高、4 题下降、42 题不变，Top-5 净增加 5 题。因此项目正式保留章节路径前缀，并将“论文结构感知上下文前缀 + 受控消融验证”作为技术亮点。这个结论只证明前缀优于同布局的无前缀版本；固定边界运行 `#5` 的 Hit@5 仍为 `81.67%`，不能写成“章节感知分块全面优于固定边界”。

## PDF 解析与 RAG 基础层

论文下载成功后，可以在论文详情页点击“解析 PDF”。系统通过后台任务执行以下流程：

```text
本地 PDF
  -> SHA-256 检查 PDF 内容是否变化
  -> 命中 MinerU 结果缓存则直接复用
  -> 未命中时申请预签名地址并上传 PDF
  -> Worker 轮询 MinerU 批次并下载结果 ZIP
  -> build_structured_chunks() 读取 content_list.json
  -> paper_documents 保存解析后端、模型、任务ID和阶段
  -> paper_chunks 保存章节、类型、页码、bbox和正文
  -> embedding_cache 复用或补算 Seed 向量
  -> 详情页展示解析结果预览
```

`pypdf`、固定边界和项目自定义分块仍保留在评测路径中，用于复现既有实验；它们不再是普通用户解析的自动后备方案。

解析状态包括：

- `not_started`：尚未解析。
- `parsing`：后台解析中。
- `parsed`：解析成功。
- `parse_failed`：解析失败，错误原因会写入数据库。

新增数据库表：

- `paper_documents`：每篇论文的解析状态、页数、文本块数量和错误原因。
- `paper_chunks`：每个文本块所属论文、页码、顺序编号和正文内容。
- `pdf_text_cache`：按 PDF 内容哈希和物理页码保存原始页文本，供不同分块策略共享。
- `embedding_cache`：按 Seed 配置命名空间和文本内容哈希保存向量，供文本块和评测问题复用。

这一版已经完成可运行的传统 RAG 闭环：PDF 解析、Seed1.6 语义向量、本地 TF-IDF 关键词向量、双路混合召回、DeepSeek 后台问答和页码证据展示。后续可增加重排序与效果评测。

## 配置 DeepSeek

项目不使用 OpenAI。复制 `.env.example` 为 `.env`，填写：

```env
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_THINKING=true
DEEPSEEK_TIMEOUT=120

SEED_API_KEY=你的火山方舟密钥
SEED_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
SEED_EMBEDDING_MODEL=doubao-embedding-vision-251215
SEED_EMBEDDING_API_MODE=auto
SEED_TIMEOUT=120
```

`.env` 已被 `.gitignore` 排除，禁止把真实 API Key 上传到 GitHub。推荐将 `SEED_EMBEDDING_MODEL` 设置为 `doubao-embedding-vision-251215`；如果填写该模型的 `ep-...` Endpoint ID，还需设置 `SEED_EMBEDDING_API_MODE=multimodal`。没有 Seed 配置时索引自动使用 TF-IDF；没有 DeepSeek 配置时只有最终模型问答不可用。

## RAG 问答流程

```text
用户点击“准备问答”
  -> paper_workflow.prepare_paper_for_qa()
  -> 下载开放 PDF（已下载则跳过）
  -> 解析 PDF（已解析则跳过）
  -> Seed1.6 Doubao-embedding-vision 生成稠密语义向量
  -> 本地 TF-IDF 生成关键词向量
  -> 用户提交问题
  -> 0.75 × Seed语义分数 + 0.25 × TF-IDF分数
  -> retrieve_relevant_chunks() 召回相关页码
  -> build_rag_prompt() 组合证据
  -> ask_deepseek() 调用 DeepSeek
  -> 页面展示回答、模型名称和原文证据
```

索引和模型回答均已使用持久Worker。问答状态包括`answering`、`answered`和`answer_failed`，页面会自动轮询并更新。

## 状态自动刷新与历史清理

- `GET /api/papers/statuses` 返回轻量状态数据，包括下载、解析、索引、问答准备状态、下载日期、页数、文本块数量和错误。
- `status_poll.js` 每 2 秒查询一次状态接口，只更新页面上的状态和日期。
- 下载或解析任务结束时，详情页会自动重载一次，以显示本地路径、解析按钮或文本块。
- `downloaded_at` 记录实际下载成功时间；旧的已下载记录使用原 `updated_at` 近似补全。
- `POST /papers/clear` 调用 `clear_paper_history()` 清空论文、解析状态和文本块。
- 清空历史记录不会删除 `downloads/papers/` 中已有的 PDF，页面提交前会要求二次确认。

## 完整工作流程

```text
浏览器输入关键词或研究问题
  -> POST /search
  -> main.py 接收表单
  -> 普通检索：model_service.enhance_query() 保持确定性搜索词
  -> 智能研究：为任务生成稳定checkpoint_thread_id
  -> start_checkpointed_research()运行plan_research()
  -> request_plan_confirmation()调用interrupt()保存检查点并暂停
  -> db.create_research_task()保存thread_id与awaiting_confirmation业务状态
  -> 用户在研究任务页确认或取消
  -> 确认路由在同一SQLite事务中写入running状态与research_search任务
  -> 独立Worker领取任务并调用Command(resume={approved: true})
  -> 条件边选择多查询或单查询降级节点
  -> invoke_research_tool("search_literature", arguments)
  -> StructuredTool校验参数并复用search_service.search_all()
  -> deduplicate_results() 按 DOI、URL 和标题合并多查询结果
  -> request_paper_selection()第二次interrupt()，不重复已完成搜索节点
  -> db.insert_papers() 写入 SQLite
  -> replace_research_tool_calls()保存输入、来源、状态、错误和耗时
  -> update_checkpoint_candidate_ids()把数据库论文ID同步回检查点
  -> 任务进入 awaiting_selection，页面自动刷新候选论文
  -> 用户从当前任务候选中选择1至5篇
  -> Command(resume={paper_ids: [...]})恢复第二个中断
  -> 图内与业务层双重校验后冻结论文ID
  -> 任务进入 papers_selected，仍不会自动下载
  -> 用户点击生成研究报告
  -> main.py原子创建preparing报告记录与research_report任务
  -> 独立Worker恢复报告确认或重新生成检查点
  -> 逐篇准备、证据检索、上下文门禁、DeepSeek生成和引用检查
  -> 报告完成后进入completed；任务重放不重复调用模型
  -> 用户可继续使用“准备问答”和“仅下载”
  -> 用户点击 POST /papers/{paper_id}/prepare
  -> main.py 原子抢占当前准备阶段并写入 background_jobs
  -> 独立Worker租约领取 prepare_paper
  -> paper_workflow.prepare_paper_for_qa() 判断当前断点
  -> download_service.download_paper() 下载开放 PDF
  -> pdf_service.parse_paper_pdf() 提取页码和文本块
  -> rag_service.index_paper_chunks() 建立 Seed + TF-IDF 索引
  -> GET /api/papers/statuses 每 2 秒返回统一准备状态
  -> ready 后用户在详情页提问并获得带页码证据的回答
```

## 为什么使用持久队列和后台任务

检索和 PDF 下载都可能耗时。如果全部在 `POST /search` 中同步执行，页面会长时间等待，甚至发生请求超时。

普通检索仍同步完成元数据检索和入库。智能研究先保存计划并返回确认页，用户确认后才执行多路网络检索，任务页每2秒检查状态。发现明确 PDF 地址时状态为`available`，用户点击“仅下载”后，Web只负责在同一事务中把论文改为`downloading`并创建持久任务，独立Worker再执行网络下载。这样既避免研究规划自动调用工具，也避免Web重启丢失已入队下载。

B1完成后，面向用户的论文获取、建库、单篇RAG问答、智能研究搜索和跨论文报告都已进入持久Worker。需要准确说明的是：当前提供SQLite单机持久队列和至少一次执行语义，不等同于Redis/Celery集群，也不保证外部副作用严格只执行一次。

## 为什么 Crossref 不强制下载

Crossref 是论文元数据平台，不是统一的 PDF 下载平台。返回地址可能是 DOI 页面、出版商页面、HTML 页面、跳转链接或需要权限的页面。

因此项目采用以下策略：

```text
存在明确的 .pdf 绝对直链 -> 尝试下载
没有明确 PDF 直链        -> 保存 DOI/网页地址并标记 link_only
```

这项取舍强调稳定性和合规性，也避免单个异常链接拖垮整个任务。

## 数据保存位置

程序运行后会创建：

```text
downloads/
|-- metadata.db
|-- papers/
`-- exports/
```

- `metadata.db`：SQLite 数据库。
- `papers/`：下载成功的 PDF。
- `exports/`：CSV、JSON 和 Origin 导出文件。

这些都是运行产物，已通过 `.gitignore` 排除，不应上传到 GitHub。

## 常用 Git 命令

```powershell
git status
git add .
git commit -m "说明本次修改"
git push
```

## 简历描述参考

```text
基于FastAPI设计学术文献检索与开放获取下载系统，聚合arXiv与Crossref API；使用SQLite持久任务队列和独立Worker执行用户确认的PDF下载，通过原子入队、租约抢占、有限重试和过期任务恢复避免Web重启丢任务及重复下载，并保留Crossref链接保守降级、文件名安全清洗和单篇失败隔离。
```

## 后续扩展方向

- 接入 OpenAlex、Unpaywall 或 Semantic Scholar。
- 支持 DOI 精确搜索、结果去重、融合和重排序。
- 增加日期、来源、状态和出版商筛选。
- 增加收藏、标签与下载状态自动刷新。
- 增加 Cross-Encoder 重排序、RAG 评测集和回答质量指标。
- 使用项目自己的论文问题集评估 Seed 与 TF-IDF 的权重。
- 使用 PostgreSQL、对象存储、独立任务队列和 Docker 部署为公网服务。
