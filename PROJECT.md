# Paper Hunter 项目概述

Paper Hunter 是一个适合简历展示的学术文献检索与开放获取下载助手。用户输入关键词或论文标题后，系统会检索 arXiv 和 Crossref，保存论文元数据、来源链接、出版商和下载状态，并在后台下载明确可免费获取的 PDF。

## 核心功能

- 提供 FastAPI Web 页面，包括搜索、结果、历史、详情和数据导出页面。
- 同时检索 arXiv 和 Crossref。
- 保存标题、作者、摘要、发表时间、来源、出版商、DOI、网页地址和 PDF 地址。
- 使用 SQLite 保存历史记录及下载状态。
- 使用 FastAPI `BackgroundTasks` 在后台下载 PDF，减少搜索请求等待时间。
- arXiv 论文优先自动下载。
- Crossref 只有在提供明确的 `.pdf` 绝对直链时才下载，否则标记为 `link_only`。
- 下载失败只影响当前论文，并记录为 `failed` 及具体错误原因。
- 自动清洗 Windows/Linux 文件名非法字符并处理重名。
- 强制使用 UTF-8 标准输出，降低 Windows 中文路径和日志乱码风险。
- 可选接入 Agent/大模型能力；没有 API Key 时仍可正常运行。
- 支持导出 Origin 统计数据，并可在安装 Origin/OriginPro 后自动绘图。

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
- 处理 Windows 文件名和 UTF-8 编码等实际工程问题。
- 支持 SQLite 数据归档、CSV/JSON 导出及 Origin 可视化。

## 简历描述参考

```text
基于 FastAPI 设计并实现学术文献检索与开放获取下载系统，聚合 arXiv 与 Crossref API，使用 BackgroundTasks 将 PDF 下载任务后台化，并通过 SQLite 记录论文元数据、出版商、DOI、来源 URL、本地路径和下载状态。针对 Crossref 链接结构复杂的问题设计保守降级策略，实现文件名安全清洗、Windows UTF-8 编码兼容、单篇下载失败隔离及 CSV/JSON/Origin 数据导出，提高系统稳定性与可演示性。
```

## 后续扩展方向

- 接入 OpenAlex、Unpaywall 或 Semantic Scholar。
- 增加 DOI 精确检索、去重、结果融合和重排序。
- 增加筛选、收藏、标签及下载状态自动刷新。
- 加入 PDF 解析、向量检索和带引用回答，升级为传统 RAG。
- 使用 PostgreSQL、对象存储和独立任务队列部署为公网服务。
