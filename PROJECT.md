# Paper Hunter 项目概述

> 当前版本：`v0.2.0`
>
> 更新日期：`2026-07-12`
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
- 自动清洗 Windows/Linux 文件名非法字符并处理重名。
- 强制使用 UTF-8 标准输出，降低 Windows 中文路径和日志乱码风险。
- 可选接入 Agent/大模型能力；没有 API Key 时仍可正常运行。
- 支持导出 Origin 统计数据，并可在安装 Origin/OriginPro 后自动绘图。
- 支持后台解析已下载 PDF，按页切分正文并保存文本块。
- 建立受约束的 RAG Prompt：只有取得全文证据时才能回答论文内容；没有全文时提示合法访问方式并提供论文链接。

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

当前已完成 DeepSeek 真实模型调用、Seed1.5 稠密语义向量、本地 TF-IDF 关键词向量、双路混合召回、后台问答和页码证据展示。下一阶段可增加重排序和 RAG 效果评测。

## DeepSeek RAG 实现

- `model_service.py` 统一封装 DeepSeek 配置、鉴权、请求和异常，原 `agent_service.py` 已合并删除。
- `rag_service.index_paper_chunks()` 为论文文本块建立本地 TF-IDF 索引。
- `rag_service.retrieve_relevant_chunks()` 使用余弦相似度召回相关片段。
- `model_service.embed_with_seed()` 调用火山方舟 Seed Embedding API。
- 混合召回默认使用 `0.75 × Seed语义分数 + 0.25 × TF-IDF关键词分数`。
- Seed 未配置或请求失败时自动降级为 TF-IDF，并在页面显示降级原因。
- `rag_service.answer_rag_query()` 在后台检索证据并调用 DeepSeek。
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
