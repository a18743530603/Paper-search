# Paper Hunter 项目概述

> 当前版本：`v0.10.1`
>
> 更新日期：`2026-08-06`
>
> 版本历史：见 [CHANGELOG.md](CHANGELOG.md)

Paper Hunter 是一个适合简历展示的学术文献检索与开放获取下载助手。用户输入关键词或论文标题后，系统会检索 arXiv 和 Crossref，保存论文元数据、来源链接、出版商和下载状态，并在后台下载明确可免费获取的 PDF。

## 核心功能

- 提供 FastAPI Web 页面，包括搜索、结果、历史、详情和数据导出页面。
- 同时检索 arXiv 和 Crossref。
- 保存标题、作者、摘要、发表时间、来源、出版商、DOI、网页地址和 PDF 地址。
- 使用 SQLite 保存历史记录及下载状态。
- 检索到明确 PDF 地址时显示下载按钮，用户确认后再通过 FastAPI `BackgroundTasks` 后台下载。
- arXiv 论文优先显示可下载状态，由用户点击按钮后下载。
- Crossref 只有在提供明确的 `.pdf` 绝对直链时才下载，否则标记为 `link_only`。
- 下载失败只影响当前论文，并记录为 `failed` 及具体错误原因。
- 下载任务使用`claim_download()`执行原子状态抢占：普通下载只允许`available → downloading`，重试允许`failed → downloading`；数据库未更新记录时不创建第二个后台任务。
- 自动清洗 Windows/Linux 文件名非法字符并处理重名。
- 强制使用 UTF-8 标准输出，降低 Windows 中文路径和日志乱码风险。
- 可选接入 Agent/大模型能力；没有 API Key 时仍可正常运行。
- 支持导出 Origin 统计数据，并可在安装 Origin/OriginPro 后自动绘图。
- 支持后台解析已下载 PDF，按页切分正文并保存文本块。
- 建立受约束的 RAG Prompt：只有取得全文证据时才能回答论文内容；没有全文时提示合法访问方式并提供论文链接。
- 使用 `paper_workflow.py` 将用户确认后的下载、PDF解析和向量索引串成后台准备流程，支持失败停止、断点续跑和统一状态展示。
- 使用LangGraph编排研究检索：DeepSeek生成结构化多查询计划，图先停在人工确认分支；确认后后台搜索节点继续调用现有arXiv/Crossref服务并确定性去重。
- 使用`research_tasks`持久化研究计划、执行状态、错误和结果论文ID；任务支持取消、失败隔离、自动刷新及应用重启后的待确认恢复。
- 检索完成后进入第二个人工审批点：用户只能从任务候选中选择1至5篇论文，冻结后续全文准备和综合报告的输入集合，不自动下载或消耗Embedding额度。
- 使用官方`langgraph-checkpoint-sqlite`：独立`SqliteSaver`保存图检查点，业务任务保存稳定`thread_id`；两个审批点使用动态`interrupt()`，恢复统一使用`Command(resume=...)`。
- 使用LangChain `StructuredTool`封装论文检索，白名单注册表限制可执行能力；`research_tool_calls`记录参数、来源结果、状态、错误和耗时，支持页面与API审计。
- 论文选择后由用户显式启动跨论文报告：标准工具复用现有准备与RAG服务，逐篇返回物理页码证据；受限论文只保留合法链接，单篇失败不影响其余论文。
- 使用`research_reports`保存报告状态、证据、访问限制、模型和错误；只有存在全文证据时才调用DeepSeek，全部无证据时确定性拒答。
- A4将报告确认、逐篇准备、逐篇证据检索、生成和引用检查纳入同一LangGraph线程；每篇论文形成检查点，重启或模型失败后从未完成节点继续。
- A5-0/A5-1新增多论文Context Packer：保留原始证据，按6000估算Token预算和每篇最多2条的公平配额选择模型上下文，并持久化压缩率、论文覆盖和删除原因。
- A5-2在公平分配前执行同论文保守去重：规范化精确重复直接合并，同页或相邻页且5-token shingle覆盖率不低于`0.85`的高重叠证据合并；跨论文内容始终保留，删除记录可审计。
- A5-3把自由文本Prompt替换为`research-context-json-v1`不可信数据载荷，证据稳定编号并保留来源元数据；系统指令明确拒绝执行证据中的命令式文本。
- A5-4新增`context-quality-gate-v1`，在模型调用前校验证据字段、物理页码、Token预算、计数和论文配额；失败时确定性拒答并把结果写入检查点、报告、API和页面。

## 当前 RAG 建设进度

已经完成第一阶段“PDF 知识库基础层”：

- 新增 `pdf_service.py`，提供文本清理、分页提取、重叠分块和后台解析函数。
- 新增 `rag_service.py`，提供论文访问链接、全文缺失提示和 RAG Prompt 构造函数。
- 新增 `paper_documents` 与 `paper_chunks` 数据表。
- 新增 `POST /papers/{paper_id}/parse` 接口。
- 论文详情页可启动解析、查看解析状态及文本块预览。
- 未取得全文时明确提示可能需要购买或机构订阅，并提供论文网页/DOI 链接。
- 下载和解析状态通过轻量接口每 2 秒自动刷新。
- 保存并展示论文下载成功日期。
- 历史页支持二次确认后清空数据库记录，并保留本地 PDF。

当前已完成 DeepSeek 真实模型调用、Seed1.6 `Doubao-embedding-vision-251215` 稠密语义向量、本地 TF-IDF 关键词向量、双路混合召回、后台问答、页码证据展示、多种论文分块消融和重排验证。`v0.8.2` 进一步把固定父块与完整句子子块组合为多粒度双路候选，按父块ID做RRF融合；该策略通过开发集门槛，但`v0.8.3`在全新8篇论文、40题独立集上发现其主指标`Hit@3`下降5个百分点，因此未升级为默认策略。

`v0.8.1` 修复生成评测的数据交接：`evaluation_service.run_evaluation()` 不再把落库证据截断到 800 字符，新建运行会保存完整 `content` 与 `retrieval_content`，使生成评测与检索命中使用同一份证据。该修复不迁移或重写旧运行，也不改变普通网页问答链路。

修正版 G2 使用新检索运行 `#36/#37` 和生成运行 `#5/#6` 完成60题配对复核，完整证据命中排名与检索报告逐题一致。重排稳定提高 Answer relevance；Reference correctness 为正向点估计但区间跨0。旧截断运行仅保留作缺陷审计，不能继续作为项目效果结论。

H2-R 使用8篇未参与开发的论文和40道冻结问题完成外部复核：重排稳定提高Hit@3、MRR和Answer relevance；Reference correctness保持正向点估计但区间跨0。`heldout_v1` 已正式关闭，新方案不得继续用它调参。

经过 G2-R/H2-R 验证后，普通论文问答已把 DeepSeek 列表重排接成默认关闭的可选能力：开启时执行 Top-20 初召回到 Top-5 重排，关闭时保持原链路；`rag_queries` 保存请求的重排器和降级错误，重排失败时使用原始 Top-5 继续回答。

当前正式推荐链路为“固定边界高召回 + Seed/TF-IDF混合检索 + 可选DeepSeek列表重排”。M1多粒度融合同时检索父块与完整句子子块，在开发集提高Hit@3，并在新独立集保持Hit@5、提高覆盖率，但因独立集Hit@3下降而只保留为实验策略。项目展示重点是可复现的指标取舍和端到端质量验证，不声称存在对所有论文都最优的单一分块方式。

`v0.9.0` 完成产品主链路编排：搜索结果中的“准备问答”由用户明确触发，后台按 `download → parse → index` 顺序调用原有服务；已完成步骤自动跳过，任一步失败只保留该阶段错误并停止，用户可再次提交从断点继续。该流程不新增业务数据表，统一状态由现有 `papers` 和 `paper_documents` 推导。

`v0.10.0` 完成可人工干预并可原生恢复的LangGraph研究闭环：`ResearchSearchState`连接规划、计划确认、多查询/单查询、结果去重、论文选择、报告确认、逐篇准备、逐篇证据检索、报告生成和引用检查。三个审批点使用`interrupt()`，生产图由`SqliteSaver`编译，`checkpoint_thread_id`关联业务任务与图状态；同一`thread_id`通过`Command(resume=...)`从暂停或失败节点继续。业务SQLite负责任务、工具与报告审计，Checkpointer负责图执行位置。

报告生成前由`context_service.pack_research_context()`执行`fair_budget_v1`：先在每篇论文内部保留高分证据并去除精确重复与相邻页高重叠文本，再跨论文按轮次分配，先提高论文覆盖再补充第二条证据。`context_stats`区分原始、去重后和最终证据，并记录`duplicate_of`审计关系。当前Token数为语言无关的保守估算；去重是确定性文本规则，不是Embedding语义去重。

最终上下文通过`context-quality-gate-v1`后，才由`build_research_context_payload()`序列化为不可信JSON并交给DeepSeek。门禁状态为`passed/warning/failed`，只有存在合法证据、预算未超限且计数一致时`model_call_allowed=true`。A1-A5单Agent LangGraph闭环已经完成；MCP、长期记忆和多Agent不在本阶段范围内。

## 分块评测与参数实验

- `benchmarks/chunking_baseline.csv` 保存 12 篇论文的 60 条人工标注问题、参考答案、物理页码和原文证据；每篇五种题型各 1 题。
- `evaluation_service.py` 负责本地论文关联、证据 token 覆盖匹配、后台评测和指标聚合。
- 新增 `evaluation_cases`、`evaluation_runs`、`evaluation_results` 三张 SQLite 表。
- 新增 `/evaluation`、`/evaluation/prepare`、`/evaluation/run` 和 `/evaluation/runs/{run_id}`。
- 评测页可配置块大小、重叠、Top-K 和 Seed/TF-IDF 权重；每次运行按配置差异复用或重建活动分块与索引，并将参数和结果保存为独立运行。
- 新增 `academic_section`：识别数字/罗马数字/IEEE 字母章节和附录层级，跨页继承章节路径，在章节内生成定长子块，并过滤参考文献与致谢。
- `v0.8.0` 新增 `academic_section_no_prefix` 对照策略：块布局、页码和正文与 `academic_section` 完全相同，只在 Seed 与 TF-IDF 索引时移除章节路径。
- `v0.8.2` 新增 `paper_multi_granularity`：固定父块和句子子块分别召回，再按共同父块融合；开发集 `Hit@3` 提高10个百分点、`Hit@5`保持不变。
- `v0.8.3`完成`heldout_v2`一次性独立对照：M1的`Hit@1/MRR/覆盖率`提高、`Hit@5`持平，但`Hit@3`从`82.50%`降至`77.50%`，未通过预注册主门槛；M1只保留为实验选项，普通解析默认仍为`length_boundary`。
- 新增 45 条证据已逐条核验对应物理页，最低 token 覆盖率为 `83.33%`。
- 12 篇、60 题固定边界运行 `#5`：`785` 块，`Hit@1=43.33%`、`Hit@3=66.67%`、`Hit@5=81.67%`、`MRR@5=0.5747`、覆盖率 `79.25%`。
- 同参数章节感知运行 `#6`：识别 `195` 条章节路径并生成 `794` 块，`Hit@1=50.00%`、`Hit@3=66.67%`、`Hit@5=75.00%`、`MRR@5=0.5950`、覆盖率 `74.34%`。
- 章节策略在 60 题中改善 `17` 题、变差 `12` 题、持平 `31` 题；它提高首位精度和方法型/参数型排名，但降低 Top-5 广度，暂不替换固定边界默认策略。
- `v0.7.0` 新增实验增量缓存：PDF 页文本按文件 SHA-256 复用，Seed 向量按模型命名空间与文本哈希复用，问题向量和未变化的论文索引不再重复计算。
- `paper_documents` 保存当前 PDF 指纹、分块策略、大小、重叠和 Embedding 命名空间；评测指标额外保存各层缓存命中数与总耗时。
- 只改 Top-K/权重时直接重新排名；换分块策略时复用页文本并仅补算新块向量；换 PDF 或模型时只失效对应层。
- 真实缓存预热 `#7` 用时 `105.876` 秒；同配置全缓存复跑 `#8` 用时 `4.406` 秒，约快 `24.0` 倍。`#8` 命中 794 个分块、12 篇论文索引和 60 个问题向量，0 次 Seed 请求，指标与 `#7` 完全一致。
- E2 有前缀运行 `#8/#11` 的 `Hit@5=75.00%`、`MRR@5=0.6033`、覆盖率 `74.34%`；无前缀运行 `#9/#10` 分别为 `66.67%`、`0.5389`、`65.47%`。
- E2 证明章节路径前缀在相同块布局下有效：Hit@5 提高 `8.33` 个百分点、MRR 提高约 `11.95%`、覆盖率提高 `8.87` 个百分点；正式策略保留前缀。

## DeepSeek RAG 实现

- `model_service.py` 统一封装 DeepSeek 配置、鉴权、请求和异常，原 `agent_service.py` 已合并删除。
- `rag_service.index_paper_chunks()` 为论文文本块建立本地 TF-IDF 索引。
- `rag_service.retrieve_relevant_chunks()` 使用余弦相似度召回相关片段。
- `model_service.embed_with_seed()` 自动选择火山方舟文本 `/embeddings` 或多模态 `/embeddings/multimodal` 接口。
- 混合召回默认使用 `0.75 × Seed语义分数 + 0.25 × TF-IDF关键词分数`。
- Seed 未配置或请求失败时自动降级为 TF-IDF，并在页面显示降级原因。
- `rag_service.answer_rag_query()` 在后台检索证据并调用 DeepSeek。
- `paper_workflow.prepare_paper_for_qa()` 编排下载、解析和索引，准备完成后进入现有问答链路。
- 新增 `rag_queries` 表保存问题、回答、证据、模型、状态和错误。
- 新增 `POST /papers/{paper_id}/index` 与 `POST /papers/{paper_id}/ask`。
- 页面展示 `indexing/indexed/index_failed` 和 `answering/answered/answer_failed` 状态。
- 没有 DeepSeek API Key 时，仅禁用模型问答，不影响其他功能。

## 快速运行

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

浏览器打开：

```text
http://127.0.0.1:8001/
```

运行测试：

```powershell
uv run pytest
```

## 项目亮点

- 多来源文献元数据检索与统一数据模型。
- 搜索请求与 PDF 下载解耦，改善页面响应速度。
- 针对 Crossref 不稳定下载链接设计平滑降级策略。
- 通过单篇异常隔离、状态机和错误记录提高容灾能力。
- 将下载、解析和索引组成可断点续跑的后台工作流，减少重复点击和重复计算。
- 处理 Windows 文件名和 UTF-8 编码等实际工程问题。
- 支持 SQLite 数据归档、CSV/JSON 导出及 Origin 可视化。
- 设计可复现 RAG 检索评测，对比固定边界与论文章节感知分块，并通过失败案例定位跨语言排序瓶颈。

## 简历描述参考

```text
基于 FastAPI 设计并实现学术文献检索与开放获取下载系统，聚合 arXiv 与 Crossref API，使用 BackgroundTasks 将 PDF 下载任务后台化，并通过 SQLite 记录论文元数据与处理状态；构建 DeepSeek + Seed/TF-IDF 混合检索 RAG，设计固定边界与论文章节感知分块、参考文献降噪、附录恢复和 15 题可复现实验评测，通过 Hit@K、MRR 与证据覆盖率定位跨语言排序瓶颈。
```

## 后续扩展方向

- 接入 OpenAlex、Unpaywall 或 Semantic Scholar。
- 增加 DOI 精确检索、去重、结果融合和重排序。
- 增加筛选、收藏、标签及下载状态自动刷新。
- 增加中英文查询翻译/扩展、章节标题独立向量和重排序器。
- 使用 PostgreSQL、对象存储和独立任务队列部署为公网服务。
