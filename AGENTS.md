# Paper Hunter 项目记忆

> 当前版本：`v0.7.0`
>
> 更新日期：`2026-07-13`
>
> 版本历史：见 [CHANGELOG.md](CHANGELOG.md)
>
> 完整实验步骤与待办：见 [EXPERIMENTS.md](EXPERIMENTS.md)

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
- 第一版检索来源为 arXiv 和 Crossref。
- arXiv 是主要的可下载 PDF 来源，因为它的 PDF 地址格式较稳定；实际下载必须由用户点击按钮触发。
- Crossref 采用保守策略：只有元数据提供以 `.pdf` 结尾的绝对直链时才尝试下载，否则保存 DOI 或网页地址并标记为 `link_only`。
- `POST /search` 只检索并保存元数据；明确可下载的论文标记为 `available`，用户点击下载按钮后才使用 FastAPI `BackgroundTasks` 执行 PDF 下载。
- 使用 SQLite 在本地保存论文元数据和下载状态。
- 大模型使用 DeepSeek API；本地搜索下载功能保持独立，没有 API Key 时仍可运行。
- 不接入 Sci-Hub、付费数据库或必须登录的下载来源。

## 当前项目结构

主要代码位于 `my_agent_project/`。

- `main.py`：FastAPI 入口，注册页面和导出接口，连接检索、数据库与后台下载服务。
- `config.py`：配置运行路径，创建下载目录，并处理 Windows UTF-8 输出。
- `schemas.py`：定义 `PaperCandidate` 和 `available`、`downloading`、`downloaded`、`link_only`、`failed` 等状态。
- `search_service.py`：检索并解析 arXiv XML 和 Crossref JSON，生成论文元数据和下载策略。
- `download_service.py`：下载 PDF、清洗文件名、处理重名并隔离单篇论文的下载异常。
- `db.py`：初始化和操作 SQLite，保存论文、更新状态、缓存 PDF 页文本与 Seed 向量，并导出 CSV/JSON。
- `model_service.py`：统一管理 DeepSeek `/chat/completions` 与火山方舟 Seed `/embeddings`、`/embeddings/multimodal` 的配置、鉴权、请求和异常；已合并并替代原 `agent_service.py`。
- `pdf_service.py`：使用 PDF 内容哈希复用页文本，支持固定边界与论文章节感知分块、章节层级继承、参考文献过滤和附录恢复，并在后台更新解析状态。
- `rag_service.py`：按模型命名空间与文本哈希缓存 Seed 稠密向量，提供 TF-IDF 关键词向量、混合召回、受约束 Prompt 和 DeepSeek 后台问答；没有全文时必须提示可能需要购买或机构订阅并附上链接。
- `evaluation_service.py`：导入人工标注基准、校验 `ExperimentConfig`、按配置差异复用或重建分块与索引、记录缓存命中与耗时、匹配标准证据并聚合 Hit/Recall/MRR 指标。
- `origin_service.py`：整理统计数据，并在本机安装 Origin/OriginPro 时尝试自动生成图表。
- `templates/`：Jinja2 页面模板。
- `static/styles.css`：网页样式。
- `tests/`：解析、下载策略、文件名清洗和异常降级等离线测试。

主要接口：

```text
GET  /
POST /search
GET  /papers
GET  /papers/{paper_id}
POST /papers/{paper_id}/retry
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
浏览器提交关键词
  -> POST /search
  -> model_service.enhance_query() 保持确定性搜索词
  -> search_service.search_all() 检索 arXiv 和 Crossref
  -> db.insert_papers() 保存元数据
  -> 结果页立即返回，可下载论文显示下载按钮
  -> 用户点击 POST /papers/{paper_id}/download
  -> main.py 通过 BackgroundTasks 安排该论文下载
  -> download_service.download_paper() 在后台下载并更新 SQLite
  -> 用户通过 GET /papers 查看进度
```

## 运行时数据

以下内容由程序运行时生成，应由 `.gitignore` 排除，不要提交到 Git：

```text
downloads/
downloads/metadata.db
downloads/papers/
downloads/exports/
.venv/
.pytest_cache/
__pycache__/
```

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
- 新增 `POST /papers/{paper_id}/parse`，通过 `BackgroundTasks` 执行 PDF 解析。
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
- C0 缓存步骤完成后必须停下，未经用户确认不得继续实现 E2 章节前缀消融。
- 后续实验优先级：章节前缀消融；冻结的中英双路查询 + BM25/RRF；Top-20 重排；随后再评估父子块和表格专用分块。Late Chunking 依赖 token 级长上下文向量，当前 Seed API 不支持；RAPTOR 与原子命题索引暂不适合当前单段证据基准。

## 用户偏好和维护约定

- 解释应面向新手，同时保留可用于面试和简历的技术细节。
- 优先让项目稳定、合规、可演示，再逐步增加复杂能力。
- 修改项目记忆时继续使用仓库根目录的 `AGENTS.md`。
- 命令、路径、接口、字段名和代码标识符应保持原样，不要为了中文化而翻译它们。
- 每次增加或修改项目功能时，必须在同一次更新中同步维护 Markdown 文档，准确记录新增模块、函数、接口、数据库字段或表、状态、测试和使用方法。
- 每次功能更新都必须确定新的语义化版本号，并在相关 Markdown 文档顶部更新“当前版本/适用版本”和“更新日期”。
- 每次功能更新都必须在 `CHANGELOG.md` 顶部新增一条记录，写明日期、版本、新增功能、修改内容、测试结果和仍未实现的范围。
- 每次实验必须同步更新 `EXPERIMENTS.md`，记录假设、唯一变量、固定参数、代码改动、运行编号、缓存统计、指标、异常、结论和未完成项；一次只完成一个实验变量，得到用户确认后再进入下一项。
- `pyproject.toml` 与 FastAPI 应用中的版本号必须和文档当前版本保持一致。
- 早期没有可靠日期的历史记录应明确写“日期未记录”，不得猜测日期。

## 已知环境信息

- 工作区位于 Windows 中文路径下。
- 推荐使用 `uv run ...` 启动和测试，减少虚拟环境与路径编码问题。
- Git 曾因仓库所有者不同配置过 `safe.directory`。
- 文档修改不涉及代码时通常不必重新运行测试；代码有变化时应执行 `uv run pytest`。
- 当前 `v0.7.0` 已于 `2026-07-13` 完成验证：正式基准包含 12 篇论文、60 题，固定边界与章节感知完整对照实验均已完成，增量缓存自动化测试为 `35 passed`。
- `pdf_service.extract_pdf_chunks()` 与 `parse_paper_pdf()` 接受 `chunk_size`、`overlap`；普通论文解析仍使用默认 `1200/150`。
- `rag_service.retrieve_relevant_chunks()` 接受并归一化 `semantic_weight`、`keyword_weight`。
- `evaluation_service.validate_experiment_config()` 校验范围并推导关键词权重；`create_experiment_run()` 保存配置；`run_configured_experiment()` 重新解析、索引并执行评测。
- `v0.6.0` 已提供 `length_boundary` 与 `academic_section`；查询翻译、章节标题独立向量、父子召回和重排序尚未实现。
- 12 篇、60 题当前正式结果：固定边界 `#5` 的 `Hit@1/3/5=43.33%/66.67%/81.67%`、`MRR@5=0.5747`；章节感知 `#6` 的 `Hit@1/3/5=50.00%/66.67%/75.00%`、`MRR@5=0.5950`。旧 3 篇结果不得参与当前结论。
- `2026-07-13` 真实 API 连通性测试通过：`doubao-embedding-vision-251215` 返回 2048 维向量，`deepseek-v4-pro` 正常生成答案；不得在文档或 Git 中保存真实 Key。
- `v0.5.0` 至 `v0.6.0` 的 3 篇、15 题及 `900/120` 参数结果仅为历史实验，当前结论统一以 12 篇、60 题运行 `#5/#6` 为准。
