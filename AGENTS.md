# Paper Hunter 项目记忆

本文件记录创建和维护本项目时形成的重要背景，供以后在本仓库中新开的 Codex 对话读取，避免丢失项目上下文。

## 用户目标

用户希望完成一个适合写入简历的文献检索项目，能够：

- 接收用户输入的关键词或论文标题。
- 从网络检索相关学术论文。
- 保存论文网页地址和元数据。
- 在数据源提供信息时展示出版商。
- 对明确免费或开放获取的论文自动下载 PDF。
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
- arXiv 是自动下载 PDF 的主要来源，因为它的 PDF 地址格式较稳定。
- Crossref 采用保守策略：只有元数据提供以 `.pdf` 结尾的绝对直链时才尝试下载，否则保存 DOI 或网页地址并标记为 `link_only`。
- 使用 FastAPI `BackgroundTasks` 执行 PDF 下载，使 `POST /search` 能尽快返回检索结果。
- 使用 SQLite 在本地保存论文元数据和下载状态。
- Agent/大模型增强功能保持可选。当前 `agent_service.py` 只是扩展入口，不需要 API Key。
- 不接入 Sci-Hub、付费数据库或必须登录的下载来源。

## 当前项目结构

主要代码位于 `my_agent_project/`。

- `main.py`：FastAPI 入口，注册页面和导出接口，连接检索、数据库与后台下载服务。
- `config.py`：配置运行路径，创建下载目录，并处理 Windows UTF-8 输出。
- `schemas.py`：定义 `PaperCandidate` 和 `downloading`、`downloaded`、`link_only`、`failed` 等状态。
- `search_service.py`：检索并解析 arXiv XML 和 Crossref JSON，生成论文元数据和下载策略。
- `download_service.py`：下载 PDF、清洗文件名、处理重名并隔离单篇论文的下载异常。
- `db.py`：初始化和操作 SQLite，保存论文、更新状态并导出 CSV/JSON。
- `agent_service.py`：预留的关键词扩展、摘要和推荐理由接口，必须保持可选和非阻塞。
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
GET  /export.csv
GET  /export.json
GET  /origin
```

## 数据流程

```text
浏览器提交关键词
  -> POST /search
  -> agent_service.enhance_query() 可选优化关键词
  -> search_service.search_all() 检索 arXiv 和 Crossref
  -> db.insert_papers() 保存元数据
  -> main.py 通过 BackgroundTasks 安排 PDF 下载
  -> 结果页立即返回
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

- 当前系统是 arXiv 与 Crossref 的简单多来源召回，不是完整 RAG，也不是 GraphRAG。
- 建议先加入 DOI 识别、OpenAlex、Unpaywall、去重、结果融合和重排序，再扩展传统 RAG。
- 传统 RAG 可增加 PDF 解析、分块、向量化、向量数据库以及带引用回答；GraphRAG 可作为更后期的高级功能。
- 若部署为公网网站，还需要服务器、域名与 HTTPS、生产数据库、对象存储、任务队列、用户与安全限流、日志监控及容器化。

## 用户偏好和维护约定

- 解释应面向新手，同时保留可用于面试和简历的技术细节。
- 优先让项目稳定、合规、可演示，再逐步增加复杂能力。
- 修改项目记忆时继续使用仓库根目录的 `AGENTS.md`。
- 命令、路径、接口、字段名和代码标识符应保持原样，不要为了中文化而翻译它们。

## 已知环境信息

- 工作区位于 Windows 中文路径下。
- 推荐使用 `uv run ...` 启动和测试，减少虚拟环境与路径编码问题。
- Git 曾因仓库所有者不同配置过 `safe.directory`。
- 文档修改不涉及代码时通常不必重新运行测试；代码有变化时应执行 `uv run pytest`。
