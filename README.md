# Paper Hunter：文献检索与开放获取下载助手

Paper Hunter 是一个面向新手、适合写入简历的 FastAPI 项目。用户输入关键词或论文标题后，系统会从 arXiv 和 Crossref 检索相关论文，保存论文网页地址和元数据；如果发现明确可免费获取的 PDF，则在后台下载到本地。

第一版优先保证稳定与合规：

- arXiv 的 PDF 地址格式稳定，因此自动下载主要依赖 arXiv。
- Crossref 的链接结构较复杂，只有遇到明确的 `.pdf` 绝对直链才下载，否则保存 DOI 或网页地址。
- PDF 下载通过 FastAPI 后台任务执行，搜索结果不必等待全部下载完成。
- 单篇论文下载失败不会中断其他论文的处理。

## 主要功能

- 通过网页输入关键词或论文标题。
- 同时检索 arXiv 和 Crossref。
- 保存标题、作者、摘要、发表时间、来源、出版商、DOI、网页 URL 和 PDF URL。
- 将论文元数据和下载状态保存到 SQLite。
- 在后台下载明确开放获取的 PDF。
- 在历史页查看 `downloading`、`downloaded`、`link_only` 和 `failed` 状态。
- 将记录导出为 CSV 或 JSON。
- 清洗论文标题中的非法文件名字符，避免 Windows 保存失败。
- 统一使用 UTF-8 输出，减少中文路径和日志乱码。
- 导出适合 Origin 的统计数据，并可选调用 Origin/OriginPro 自动绘图。
- 为未来的大模型关键词扩展、摘要和 RAG 问答预留接口。

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
- `downloaded`：PDF 下载成功，本地路径已经保存。
- `link_only`：没有明确可下载的 PDF，只保存网页或 DOI 地址。
- `failed`：下载失败，数据库中会记录错误原因。

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
|   |-- agent_service.py
|   |-- origin_service.py
|   |-- templates/
|   `-- static/
|-- tests/
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
GET  /export.csv
GET  /export.json
GET  /origin
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
doi, page_url, pdf_url, local_path, status, error, created_at, updated_at
```

### `agent_service.py`

这是可选的大模型扩展入口。当前项目不依赖任何模型 API；未来可在这里实现关键词改写、中文摘要和推荐理由。大模型不参与版权或下载决策。

### `origin_service.py`

从 SQLite 汇总来源、状态、出版商和年份数据，生成 CSV；本机安装 Origin/OriginPro 和 `originpro` 后，还可以自动创建图表。详细说明见 [ORIGIN.md](ORIGIN.md)。

### `templates/` 与 `static/`

`templates/` 保存 Jinja2 HTML 模板，`static/styles.css` 负责页面布局、表格、状态标签和响应式样式。

### `tests/`

包含不依赖网络的核心测试，覆盖 arXiv/Crossref 解析、PDF 下载策略、文件名清洗、来源异常降级等行为。

## 完整工作流程

```text
浏览器输入关键词
  -> POST /search
  -> main.py 接收表单
  -> agent_service.enhance_query() 可选优化关键词
  -> search_service.search_all() 检索 arXiv 和 Crossref
  -> db.insert_papers() 写入 SQLite
  -> main.py 将可下载论文加入 BackgroundTasks
  -> 页面立即返回检索结果
  -> download_service.download_paper() 在后台下载 PDF
  -> db.update_download_status() 更新状态
  -> 用户在 GET /papers 查看历史和进度
```

## 为什么使用后台任务

检索和 PDF 下载都可能耗时。如果全部在 `POST /search` 中同步执行，页面会长时间等待，甚至发生请求超时。

现在搜索接口先完成元数据检索和入库，然后立即返回结果；PDF 下载由 `BackgroundTasks` 继续处理。这样既改善响应速度，也能让每篇论文独立更新状态。

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
- 解析 PDF 并加入向量检索和带引用回答，升级为传统 RAG。
- 使用 PostgreSQL、对象存储、独立任务队列和 Docker 部署为公网服务。
