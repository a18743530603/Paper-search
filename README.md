# Paper Hunter：文献检索与开放获取下载助手

> 当前版本：`v0.6.0`
>
> 更新日期：`2026-07-13`
>
> 版本历史：见 [CHANGELOG.md](CHANGELOG.md)

Paper Hunter 是一个面向新手、适合写入简历的 FastAPI 项目。用户输入关键词或论文标题后，系统会从 arXiv 和 Crossref 检索相关论文，保存论文网页地址和元数据；如果发现明确可免费获取的 PDF，则在后台下载到本地。

第一版优先保证稳定与合规：

- arXiv 的 PDF 地址格式稳定，因此优先识别为可下载来源，但需要用户点击下载按钮。
- Crossref 的链接结构较复杂，只有遇到明确的 `.pdf` 绝对直链才下载，否则保存 DOI 或网页地址。
- PDF 下载通过 FastAPI 后台任务执行，搜索结果不必等待全部下载完成。
- 单篇论文下载失败不会中断其他论文的处理。

## 主要功能

- 通过网页输入关键词或论文标题。
- 同时检索 arXiv 和 Crossref。
- 保存标题、作者、摘要、发表时间、来源、出版商、DOI、网页 URL 和 PDF URL。
- 将论文元数据和下载状态保存到 SQLite。
- 为具有明确 PDF 地址的论文显示下载按钮，由用户决定是否下载。
- 点击下载后在后台执行任务，并在历史页查看 `available`、`downloading`、`downloaded`、`link_only` 和 `failed` 状态。
- 将记录导出为 CSV 或 JSON。
- 清洗论文标题中的非法文件名字符，避免 Windows 保存失败。
- 统一使用 UTF-8 输出，减少中文路径和日志乱码。
- 导出适合 Origin 的统计数据，并可选调用 Origin/OriginPro 自动绘图。
- 接入 DeepSeek API，实现基于论文原文证据的 RAG 问答。
- 支持 Seed1.6 `Doubao-embedding-vision-251215` 稠密语义向量与本地 TF-IDF 的双路混合召回，同时兼容旧文本 Embedding 接口。
- Seed API 未配置或暂时失败时自动降级为本地 TF-IDF，不影响论文问答主流程。
- 支持对已下载 PDF 进行后台解析，按页切分文本并保存到 SQLite。
- 对未取得全文的论文提示可能需要购买或机构订阅，并提供论文原始链接。
- 下载与解析状态每 2 秒自动刷新，无需手动刷新整个页面。
- 保存并展示论文实际下载成功日期。
- 历史页可清空数据库记录，同时保留本地 PDF 文件。
- 内置分块检索评测页，计算 Hit@K、Recall@K、MRR 和证据覆盖率，并绘制当前运行与历史运行曲线。
- 支持在评测页配置块大小、重叠、Top-K 和 Seed/TF-IDF 融合权重；每次实验会重新分块并重建索引。
- 支持 `length_boundary` 固定边界与 `academic_section` 论文章节感知两种分块策略，并保存每次实验的策略、参数、章节数和指标。

## 快速开始

### 1. 安装 uv

本项目使用 `uv` 管理 Python 环境和依赖。安装方式请参考 [uv 官方文档](https://docs.astral.sh/uv/)。

### 2. 启动项目

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

### 3. 运行测试

```powershell
uv run pytest
```

## 使用方法

1. 打开首页。
2. 输入关键词或论文标题，例如 `multimodal agent`。
3. 提交搜索后，页面先显示已经获得的论文元数据。
4. 可下载论文会进入后台下载任务。
5. 打开“历史记录”页面并刷新，查看状态变化。
6. 点击论文进入详情页，查看 DOI、原始网页、PDF 地址、本地路径和错误信息。

下载状态说明：

- `downloading`：已经进入后台下载任务。
- `available`：已经发现可下载 PDF，等待用户点击下载按钮。
- `downloaded`：PDF 下载成功，本地路径已经保存。
- `link_only`：没有明确可下载的 PDF，只保存网页或 DOI 地址。
- `failed`：下载失败，数据库中会记录错误原因。

## 分块评测使用方法

评测页面用于检验“用户提问后，系统能否把包含正确答案的论文文本块排到前面”。它只评测分块、Embedding 和召回排序，不调用 DeepSeek 生成答案，因此不会把大模型回答能力混入检索指标。

打开：

```text
http://127.0.0.1:8001/evaluation
```

第一次使用时按以下顺序操作：

1. 将待评测的 PDF 放入 `downloads/papers/`。
2. 在 `benchmarks/chunking_baseline.csv` 中填写论文标题、问题类型、问题、参考答案、证据物理页码和连续证据原文。
3. `paper_title` 应与 PDF 文件名或系统论文记录标题尽量一致；`evidence_page` 使用 PDF 阅读器显示的物理页码，而不是论文正文印刷页码。
4. 打开“分块评测”页面，点击“导入并准备基准”。系统会注册本地论文、解析 PDF、分块并建立向量索引。
5. 等待页面显示全部案例已建立索引，在实验配置中选择固定边界或论文章节策略，并填写实验名称、块大小、重叠、Top-K 和 Seed 语义权重。
6. 点击“重建索引并运行实验”。后台会按本次参数重新解析三篇 PDF、调用 Seed 生成新向量，再执行 15 道检索评测。
7. 查看总体指标、Top-K 曲线和逐题结果；展开召回片段可以检查具体内容。

CSV 必须保留以下表头：

```text
paper_title,question_type,question,reference_answer,evidence_page,evidence_text
```

每次实验都会创建新的 `evaluation_runs` 记录，保存分块策略、块大小、重叠、Top-K、Embedding 模型和融合权重。当前运行曲线展示 Hit@1、Hit@3、Hit@5；历史趋势图按运行编号比较结果。`academic_section` 会识别论文标题层级、过滤参考文献、保留附录，并把章节路径写入文本块；固定边界策略继续作为对照基线。

## 目录结构

```text
.
|-- my_agent_project/
|   |-- main.py
|   |-- config.py
|   |-- schemas.py
|   |-- search_service.py
|   |-- download_service.py
|   |-- db.py
|   |-- model_service.py
|   |-- pdf_service.py
|   |-- rag_service.py
|   |-- evaluation_service.py
|   |-- origin_service.py
|   |-- templates/
|   `-- static/
|-- tests/
|-- benchmarks/
|   `-- chunking_baseline.csv # 15 条人工标注检索案例
|-- downloads/              # 运行时生成，不提交到 Git
|-- AGENTS.md                # 项目背景和后续对话记忆
|-- ORIGIN.md                # Origin 绘图说明
|-- PROJECT.md               # 项目概述和简历描述
|-- pyproject.toml           # 依赖与测试配置
|-- uv.lock                  # 锁定依赖版本
`-- README.md
```

## 模块说明

### `main.py`

FastAPI 应用入口，负责注册路由、接收搜索表单、调用检索服务、写入数据库、安排后台下载并返回 HTML 页面。

主要接口：

```text
GET  /
POST /search
GET  /papers
GET  /papers/{paper_id}
POST /papers/{paper_id}/retry
POST /papers/{paper_id}/download
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

负责调用 arXiv 和 Crossref、解析 XML/JSON、统一论文元数据、生成 arXiv PDF 地址，并判断 Crossref 是否提供可直接下载的 PDF。

该模块只负责“查找论文和解析信息”，不负责把文件写入本地。

### `download_service.py`

负责后台下载 PDF、清洗文件名、处理同名文件、更新数据库状态，以及捕获单篇论文的下载异常。

文件名清洗的核心规则是：

```python
re.sub(r'[\\/*?:"<>|]', "_", title)
```

### `db.py`

负责 SQLite 的全部数据读写，包括初始化表、插入检索结果、查询历史与详情、更新下载状态以及导出 CSV/JSON。

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
- `ModelConfigurationError`：缺少 `.env` 配置时给出明确提示。
- `ModelRequestError`：统一包装网络错误和 DeepSeek 返回错误。

### `pdf_service.py`

负责 RAG 的 PDF 预处理基础能力：

- `normalize_pdf_text()`：清理 PDF 提取文本中的空字符、重复空格和多余换行。
- `split_page_text()`：按页将正文切分为带重叠区域的文本块。
- `detect_section_heading()`：识别数字、罗马数字、IEEE 字母小节和附录标题，并排除常见正文/表格误判。
- `split_academic_page_text()`：跨页维护章节树，在章节内部继续生成定长子块，并过滤参考文献噪声。
- `extract_pdf_chunks()`：使用 `pypdf` 提取每页正文，按 `length_boundary` 或 `academic_section` 生成带页码和顺序编号的文本块。
- `parse_paper_pdf()`：按指定策略后台解析单篇论文，更新解析状态，并把文本块写入 SQLite；单篇解析失败不会影响其他论文。

### `rag_service.py`

负责后续大模型问答所需的访问提示和 Prompt 构造：

- `tokenize()` 与 `term_frequency()`：将中英文正文转换为本地稀疏词频向量。
- `index_paper_chunks()`：在后台建立 Seed 稠密向量和 `local-tfidf-v1` 关键词向量；Seed 不可用时自动降级。
- `retrieve_relevant_chunks()`：融合 Seed 语义相似度和 TF-IDF 关键词相似度，返回相关页码和原文。
- `answer_rag_query()`：组合检索、Prompt、DeepSeek 调用和问答状态更新。
- `paper_access_url()`：优先返回论文网页地址，没有网页地址时生成 DOI 链接。
- `build_access_notice()`：在系统没有全文时提示读者可能需要购买、学校或机构订阅，并附上合法访问链接。
- `build_rag_prompt()`：把论文信息、页码、全文片段和用户问题组合成受约束的 RAG Prompt。
- `RAG_SYSTEM_PROMPT`：要求模型只根据已解析证据回答、标注论文和页码，不得假装读过未下载的付费全文，也不得提供绕过版权限制的方法。

### `origin_service.py`

从 SQLite 汇总来源、状态、出版商和年份数据，生成 CSV；本机安装 Origin/OriginPro 和 `originpro` 后，还可以自动创建图表。详细说明见 [ORIGIN.md](ORIGIN.md)。

### `evaluation_service.py`

负责分块实验：按论文标题导入本地 PDF 与人工标注 CSV，校验策略和实验参数，按本次配置强制重新分块和建立 Seed/TF-IDF 索引，再使用“同页 + 规范化 token 覆盖率”识别正确证据并聚合指标。`ExperimentConfig` 保存策略与参数，`run_configured_experiment()` 串联重建、章节统计与评测。评测只调用检索器，不调用 DeepSeek 生成答案。

### `templates/` 与 `static/`

`templates/` 保存 Jinja2 HTML 模板，`static/styles.css` 负责页面布局、表格、状态标签和响应式样式。

### `tests/`

包含不依赖网络的核心测试，覆盖 arXiv/Crossref 解析、PDF 下载策略、文件名清洗、来源异常降级、Embedding 请求、证据匹配、指标聚合和评测持久化等行为。

## 固定边界与论文章节实验

`benchmarks/chunking_baseline.csv` 保存 3 篇论文、15 个问题及其参考答案、PDF 物理页码和连续原文证据。当前策略为 `length_boundary`，参数是 `chunk_size=1200`、`overlap=150`、语义权重 `0.75`、关键词权重 `0.25`、`top_k=5`。

2026-07-13 实测结果：`Hit@1=66.67%`、`Hit@3=80.00%`、`Hit@5=93.33%`、`MRR@5=0.7667`、平均最佳证据覆盖率 `91.49%`。唯一未命中题的完整证据块排在第 18 名，说明该案例主要是跨语言块级排序问题，不是证据被分块切断。

同日参数实验将块大小改为 `900`、重叠改为 `120`，融合权重和 `top_k=5` 保持不变。总块数增加到 `217`，结果为 `Hit@1=53.33%`、`Hit@3=80.00%`、`Hit@5=93.33%`、`MRR@5=0.6856`、平均最佳证据覆盖率 `92.80%`。更小的块提高了证据覆盖率，但增加了相似候选与排序噪声，Hit@1 和 MRR 下降，因此不能把“块越小”直接视为优化。

`academic_section` 修正版保持 `1200/150`、融合权重和 `top_k=5` 不变，识别 `44` 条唯一章节路径并生成 `148` 个文本块。结果为 `Hit@1=60.00%`、`Hit@3=80.00%`、`Hit@5=80.00%`、`MRR@5=0.6889`、平均最佳证据覆盖率 `83.30%`。它成功排除了参考文献噪声并保留附录，但两道完整证据排在第 6、7 名，说明结构化分块不是排序器；后续应对中文问题增加英文查询扩展，或在初召回后加入重排序。

## PDF 解析与 RAG 基础层

论文下载成功后，可以在论文详情页点击“解析 PDF”。系统通过后台任务执行以下流程：

```text
本地 PDF
  -> pypdf 按页提取正文
  -> normalize_pdf_text() 清理文本
  -> split_page_text() 生成重叠文本块
  -> paper_documents 保存解析状态
  -> paper_chunks 保存页码、顺序和文本内容
  -> 详情页展示解析结果预览
```

解析状态包括：

- `not_started`：尚未解析。
- `parsing`：后台解析中。
- `parsed`：解析成功。
- `parse_failed`：解析失败，错误原因会写入数据库。

新增数据库表：

- `paper_documents`：每篇论文的解析状态、页数、文本块数量和错误原因。
- `paper_chunks`：每个文本块所属论文、页码、顺序编号和正文内容。

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
已下载 PDF
  -> 解析 PDF
  -> Seed1.6 Doubao-embedding-vision 生成稠密语义向量
  -> 本地 TF-IDF 生成关键词向量
  -> 用户提交问题
  -> 0.75 × Seed语义分数 + 0.25 × TF-IDF分数
  -> retrieve_relevant_chunks() 召回相关页码
  -> build_rag_prompt() 组合证据
  -> ask_deepseek() 调用 DeepSeek
  -> 页面展示回答、模型名称和原文证据
```

索引和模型回答都使用 `BackgroundTasks`。问答状态包括 `answering`、`answered` 和 `answer_failed`，页面会自动轮询并更新。

## 状态自动刷新与历史清理

- `GET /api/papers/statuses` 返回轻量状态数据，包括下载状态、下载日期、解析状态、页数、文本块数量和错误。
- `status_poll.js` 每 2 秒查询一次状态接口，只更新页面上的状态和日期。
- 下载或解析任务结束时，详情页会自动重载一次，以显示本地路径、解析按钮或文本块。
- `downloaded_at` 记录实际下载成功时间；旧的已下载记录使用原 `updated_at` 近似补全。
- `POST /papers/clear` 调用 `clear_paper_history()` 清空论文、解析状态和文本块。
- 清空历史记录不会删除 `downloads/papers/` 中已有的 PDF，页面提交前会要求二次确认。

## 完整工作流程

```text
浏览器输入关键词
  -> POST /search
  -> main.py 接收表单
  -> model_service.enhance_query() 保持确定性搜索词
  -> search_service.search_all() 检索 arXiv 和 Crossref
  -> db.insert_papers() 写入 SQLite
  -> 页面立即返回检索结果并显示下载按钮
  -> 用户点击 POST /papers/{paper_id}/download
  -> main.py 将该论文加入 BackgroundTasks
  -> download_service.download_paper() 在后台下载 PDF
  -> db.update_download_status() 更新状态
  -> 用户在 GET /papers 查看历史和进度
```

## 为什么使用后台任务

检索和 PDF 下载都可能耗时。如果全部在 `POST /search` 中同步执行，页面会长时间等待，甚至发生请求超时。

现在搜索接口只完成元数据检索和入库，然后立即返回结果。发现明确 PDF 地址时状态为 `available`，用户点击下载按钮后才通过 `BackgroundTasks` 下载。这样既改善响应速度，也避免一次搜索自动产生大量本地文件。

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
基于 FastAPI 设计并实现学术文献检索与开放获取下载系统，聚合 arXiv 与 Crossref API，使用 BackgroundTasks 将 PDF 下载任务后台化，并通过 SQLite 记录论文元数据、出版商、DOI、来源 URL、本地路径和下载状态。针对 Crossref 链接结构复杂的问题设计保守降级策略，实现文件名安全清洗、Windows UTF-8 编码兼容、单篇下载失败隔离及 CSV/JSON/Origin 数据导出，提高系统稳定性与可演示性。
```

## 后续扩展方向

- 接入 OpenAlex、Unpaywall 或 Semantic Scholar。
- 支持 DOI 精确搜索、结果去重、融合和重排序。
- 增加日期、来源、状态和出版商筛选。
- 增加收藏、标签与下载状态自动刷新。
- 增加 Cross-Encoder 重排序、RAG 评测集和回答质量指标。
- 使用项目自己的论文问题集评估 Seed 与 TF-IDF 的权重。
- 使用 PostgreSQL、对象存储、独立任务队列和 Docker 部署为公网服务。
