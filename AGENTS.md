# Paper Hunter 项目记忆

> 当前版本：`v0.5.0`
>
> 更新日期：`2026-07-13`
>
> 版本历史：见 [CHANGELOG.md](CHANGELOG.md)

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
- `db.py`：初始化和操作 SQLite，保存论文、更新状态并导出 CSV/JSON。
- `model_service.py`：统一管理 DeepSeek `/chat/completions` 与火山方舟 Seed `/embeddings`、`/embeddings/multimodal` 的配置、鉴权、请求和异常；已合并并替代原 `agent_service.py`。
- `pdf_service.py`：使用 `pypdf` 按页提取正文、清理文本、生成重叠文本块，并在后台更新解析状态。
- `rag_service.py`：Seed 稠密向量、TF-IDF 关键词向量、混合召回、受约束 Prompt 和 DeepSeek 后台问答；没有全文时必须提示可能需要购买或机构订阅并附上链接。
- `evaluation_service.py`：导入人工标注基准、校验 `ExperimentConfig`、按实验参数重建 PDF 分块与索引、匹配标准证据并聚合 Hit/Recall/MRR 指标。
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

## 用户偏好和维护约定

- 解释应面向新手，同时保留可用于面试和简历的技术细节。
- 优先让项目稳定、合规、可演示，再逐步增加复杂能力。
- 修改项目记忆时继续使用仓库根目录的 `AGENTS.md`。
- 命令、路径、接口、字段名和代码标识符应保持原样，不要为了中文化而翻译它们。
- 每次增加或修改项目功能时，必须在同一次更新中同步维护 Markdown 文档，准确记录新增模块、函数、接口、数据库字段或表、状态、测试和使用方法。
- 每次功能更新都必须确定新的语义化版本号，并在相关 Markdown 文档顶部更新“当前版本/适用版本”和“更新日期”。
- 每次功能更新都必须在 `CHANGELOG.md` 顶部新增一条记录，写明日期、版本、新增功能、修改内容、测试结果和仍未实现的范围。
- `pyproject.toml` 与 FastAPI 应用中的版本号必须和文档当前版本保持一致。
- 早期没有可靠日期的历史记录应明确写“日期未记录”，不得猜测日期。

## 已知环境信息

- 工作区位于 Windows 中文路径下。
- 推荐使用 `uv run ...` 启动和测试，减少虚拟环境与路径编码问题。
- Git 曾因仓库所有者不同配置过 `safe.directory`。
- 文档修改不涉及代码时通常不必重新运行测试；代码有变化时应执行 `uv run pytest`。
- 当前 `v0.5.0` 已于 `2026-07-13` 完成验证：评测页可配置块大小、重叠、Top-K 和 Seed/TF-IDF 权重，每次实验强制重新分块和索引，完整自动化测试为 `27 passed`。
- `pdf_service.extract_pdf_chunks()` 与 `parse_paper_pdf()` 接受 `chunk_size`、`overlap`；普通论文解析仍使用默认 `1200/150`。
- `rag_service.retrieve_relevant_chunks()` 接受并归一化 `semantic_weight`、`keyword_weight`。
- `evaluation_service.validate_experiment_config()` 校验范围并推导关键词权重；`create_experiment_run()` 保存配置；`run_configured_experiment()` 重新解析、索引并执行评测。
- `v0.5.0` 的策略选项仍只有 `length_boundary`；语义分块、自适应分块和父子分块尚未实现。
- `2026-07-13` 真实 API 连通性测试通过：`doubao-embedding-vision-251215` 返回 2048 维向量，`deepseek-v4-pro` 正常生成答案；不得在文档或 Git 中保存真实 Key。
- 固定分块基线包含 3 篇论文和 15 条人工标注问题，策略为 `length_boundary`、`chunk_size=1200`、`overlap=150`、`top_k=5`；实测 `Hit@5=93.33%`、`MRR@5=0.7667`、平均证据覆盖率 `91.49%`。
- 唯一未命中案例是 AOD 参数题，完整正确块位于第 18 名；该问题是块级排序失败，不能误判为文本被切断。
- `900/120` 参数实验共生成 `217` 块，实测 `Hit@1=53.33%`、`Hit@3=80%`、`Hit@5=93.33%`、`MRR@5=0.6856`、覆盖率 `92.80%`；相较 `1200/150` 基线，覆盖率略升但 Hit@1 和 MRR 下降。
