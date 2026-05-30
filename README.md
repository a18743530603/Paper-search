# Paper Hunter 文献检索与开放 PDF 下载助手

Paper Hunter 是一个适合写进简历、也方便新手阅读的 FastAPI 项目。它的目标很简单：输入关键词，系统去 arXiv 和 Crossref 检索相关论文，保存论文的网址和元数据；如果论文有明确可免费下载的 PDF，就在后台下载到本地。

第一版的设计重点是稳定和合规：

- arXiv 的 PDF 链接格式稳定，所以自动下载主要依赖 arXiv。
- Crossref 的链接来源复杂，只有遇到明确的 `.pdf` 直链才下载，否则只保存 DOI 或网页地址。
- PDF 下载放到 FastAPI 后台任务中执行，避免网页一直转圈等待。
- 每篇论文的下载失败只影响自己，不会拖垮整次搜索。

## 项目能做什么

- 在网页中输入关键词搜索论文。
- 同时查询 arXiv 和 Crossref。
- 保存论文标题、作者、摘要、发布时间、来源、DOI、网页 URL、PDF URL。
- 后台下载明确开放的 PDF。
- 在历史页面查看下载状态。
- 将历史记录导出为 CSV 或 JSON。
- 自动清洗论文标题，避免 Windows 文件名非法字符导致保存失败。
- 在 Windows 控制台中强制使用 UTF-8，减少中文乱码和编码崩溃。

## 快速运行

本项目使用 `uv` 管理依赖。如果你已经安装了 `uv`，在项目根目录运行：

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

然后在浏览器打开：

```text
http://127.0.0.1:8001/
```

如果 `8001` 被占用，可以换成其他端口，例如：

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8002
```

## 如何使用

1. 打开首页。
2. 在搜索框输入关键词，例如 `multimodal agent`。
3. 点击“开始搜索”。
4. 页面会先展示检索到的论文元数据。
5. PDF 下载任务会在后台继续执行。
6. 打开“历史”页面，刷新后可以看到状态变化。

下载状态含义：

- `downloading`：已经进入后台下载队列。
- `downloaded`：PDF 下载成功，本地路径已保存。
- `link_only`：没有明确可下载 PDF，只保存网页或 DOI 链接。
- `failed`：尝试下载失败，错误原因会记录到数据库。

## 目录结构

```text
.
├── my_agent_project/
│   ├── main.py
│   ├── config.py
│   ├── schemas.py
│   ├── search_service.py
│   ├── download_service.py
│   ├── db.py
│   ├── agent_service.py
│   ├── templates/
│   └── static/
├── tests/
│   └── test_services.py
├── downloads/
├── pyproject.toml
├── uv.lock
├── PROJECT.md
└── README.md
```

说明：

- `my_agent_project/` 是项目主代码。
- `templates/` 是网页 HTML 模板。
- `static/` 是网页 CSS 样式。
- `tests/` 是测试代码。
- `downloads/` 是运行时生成目录，用来保存 SQLite 数据库和下载的 PDF，不提交到 Git。
- `pyproject.toml` 记录项目依赖和测试配置。
- `uv.lock` 锁定依赖版本，方便别人复现环境。

## 各模块负责什么

### `main.py`

FastAPI 应用入口。它负责把各个模块连接起来。

主要职责：

- 创建 FastAPI app。
- 注册网页路由。
- 接收搜索表单。
- 调用检索服务获取论文元数据。
- 把结果写入 SQLite。
- 把 PDF 下载任务交给 `BackgroundTasks`。
- 返回 HTML 页面。

主要路由：

```text
GET  /
POST /search
GET  /papers
GET  /papers/{paper_id}
POST /papers/{paper_id}/retry
GET  /export.csv
GET  /export.json
```

### `config.py`

项目配置和运行环境初始化。

主要职责：

- 设置项目根目录。
- 设置下载目录。
- 设置数据库路径。
- 创建运行时目录。
- 强制标准输出和错误输出使用 UTF-8。

重要路径：

```text
downloads/
downloads/papers/
downloads/exports/
downloads/metadata.db
```

### `schemas.py`

放项目中共享的数据结构和状态常量。

核心数据结构是 `PaperCandidate`，表示一次检索得到的一篇论文。

它包含：

- `title`：标题。
- `authors`：作者。
- `summary`：摘要。
- `published`：发布时间。
- `source`：来源，例如 `arxiv` 或 `crossref`。
- `doi`：DOI。
- `page_url`：论文网页地址。
- `pdf_url`：PDF 地址。
- `status`：下载状态。

状态常量：

```text
downloading
downloaded
link_only
failed
```

### `search_service.py`

论文检索模块。它只负责“找论文”和“解析元数据”，不负责下载文件。

主要职责：

- 调用 arXiv API。
- 调用 Crossref API。
- 解析 arXiv 返回的 XML。
- 解析 Crossref 返回的 JSON。
- 为 arXiv 论文生成规范 PDF URL。
- 判断 Crossref 是否有明确的 PDF 直链。
- 单个来源检索失败时生成 `failed` 记录，而不是让整个搜索崩掉。

检索逻辑：

```text
用户关键词
  -> search_all()
  -> search_arxiv()
  -> search_crossref()
  -> 返回 PaperCandidate 列表
```

arXiv 下载策略：

```text
https://arxiv.org/abs/xxxx
  -> https://arxiv.org/pdf/xxxx.pdf
```

Crossref 下载策略：

```text
有 https://.../*.pdf 绝对直链 -> 标记为 downloading
没有明确 PDF 直链 -> 标记为 link_only
```

### `download_service.py`

PDF 下载模块。它负责后台下载和容灾。

主要职责：

- 判断一篇论文是否可以尝试下载。
- 清洗文件名。
- 避免文件重名覆盖。
- 下载 PDF。
- 更新数据库中的状态。
- 捕获单篇论文异常。

文件名清洗逻辑：

```python
re.sub(r'[\\/*?:"<>|]', "_", title)
```

这一步很重要，因为论文标题里经常有冒号、问号、斜杠等字符，这些字符不能直接作为 Windows 文件名。

下载失败时不会抛到主流程，而是写入：

```text
status = failed
error = 错误原因
```

### `db.py`

SQLite 数据库模块。它负责所有数据读写。

主要职责：

- 初始化数据库表。
- 插入检索结果。
- 查询历史记录。
- 查询单篇论文详情。
- 更新下载状态。
- 导出 CSV。
- 导出 JSON。

数据库表叫 `papers`，主要字段包括：

```text
id
query
title
authors
summary
published
source
doi
page_url
pdf_url
local_path
status
error
created_at
updated_at
```

### `agent_service.py`

可选 Agent 增强模块。

当前第一版为了保证项目稳定，默认不依赖大模型 API。这个模块提供了一个 `enhance_query()` 入口，未来可以扩展成：

- 根据用户关键词生成更适合学术搜索的检索词。
- 对搜索结果做中文总结。
- 给论文生成推荐阅读理由。

注意：Agent 不参与 PDF 下载决策。下载是否执行仍然由规则控制，这样更稳定，也更合规。

### `templates/`

网页模板目录，使用 Jinja2。

文件说明：

- `base.html`：公共页面框架，包含导航栏和样式引用。
- `index.html`：搜索首页。
- `results.html`：搜索结果页。
- `papers.html`：历史记录页。
- `paper_detail.html`：单篇论文详情页。
- `table.html`：论文表格组件，被结果页和历史页复用。

### `static/styles.css`

网页样式文件。

它负责：

- 页面布局。
- 搜索表单样式。
- 表格样式。
- 状态标签颜色。
- 移动端适配。

### `tests/test_services.py`

测试文件，主要测试不依赖网络的核心逻辑。

目前覆盖：

- arXiv XML 解析。
- arXiv PDF URL 生成。
- Crossref 无 PDF 直链时降级为 `link_only`。
- Crossref 有 PDF 直链时进入下载状态。
- 文件名非法字符清洗。
- 下载策略判断。
- 检索来源失败时生成 `failed` 记录。

运行测试：

```powershell
uv run pytest
```

## 整体工作流程

下面是一次搜索从前端到后台下载的完整流程：

```text
浏览器输入关键词
  -> POST /search
  -> main.py 接收表单
  -> agent_service.enhance_query() 可选优化关键词
  -> search_service.search_all() 检索 arXiv 和 Crossref
  -> db.insert_papers() 写入 SQLite
  -> main.py 把可下载论文加入 BackgroundTasks
  -> 页面立即返回检索结果
  -> download_service.download_paper() 在后台下载 PDF
  -> db.update_download_status() 更新状态
  -> 用户在 GET /papers 查看历史和进度
```

这个设计的好处是：网页不用等 PDF 全部下载完才响应，用户体验更好，也不容易请求超时。

## 为什么要用后台任务

检索论文和下载 PDF 都可能比较慢。如果全部放在 `POST /search` 里同步执行，用户点击搜索后页面可能会一直等待。

现在的设计是：

1. 搜索接口只做元数据检索。
2. 搜索结果尽快返回给用户。
3. PDF 下载交给后台任务慢慢执行。
4. 用户通过历史页查看下载进度。

这就是 `BackgroundTasks` 在项目里的作用。

## 为什么 Crossref 不强行下载

Crossref 是论文元数据平台，不是统一的 PDF 下载平台。它返回的链接可能是：

- DOI 页面。
- 出版商网页。
- HTML 页面。
- 跳转链接。
- 需要权限的页面。
- 偶尔才有真正的 PDF 直链。

如果第一版强行对所有 Crossref 链接尝试下载，项目会变得很不稳定。因此这里采用保守策略：

```text
只有明确的 .pdf 绝对直链才尝试下载。
其他情况只保存链接。
```

这样更适合做简历项目展示，因为稳定性比“看起来能下载很多东西”更重要。

## 数据保存在哪里

运行后会自动创建：

```text
downloads/
├── metadata.db
├── papers/
└── exports/
```

说明：

- `metadata.db`：SQLite 数据库。
- `papers/`：下载成功的 PDF。
- `exports/`：导出的 CSV 和 JSON。

这些都是运行产物，已经被 `.gitignore` 排除，不会提交到 GitHub。

## 常用命令

启动服务：

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

运行测试：

```powershell
uv run pytest
```

查看 Git 状态：

```powershell
git status
```

提交修改：

```powershell
git add .
git commit -m "Update README"
git push
```

## 简历描述参考

可以这样写：

```text
基于 FastAPI 设计并实现学术文献检索与开放获取下载系统，聚合 arXiv 与 Crossref API，使用 BackgroundTasks 将 PDF 下载异步化，并通过 SQLite 记录论文元数据、URL、本地文件路径和下载状态。针对 Crossref 链接结构复杂的问题设计保守降级策略，实现文件名安全清洗、Windows UTF-8 编码兼容和单篇下载失败隔离，提高系统稳定性与可演示性。
```

## 后续可以扩展什么

- 加入 Semantic Scholar 或 OpenAlex。
- 做前端自动刷新下载状态。
- 给每篇论文增加标签和收藏。
- 加入大模型摘要和推荐理由。
- 支持按日期、来源、状态筛选历史记录。
- 支持 Docker 部署。
