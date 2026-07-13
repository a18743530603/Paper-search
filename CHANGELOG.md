# Paper Hunter 版本更新记录

本文件按时间倒序记录项目的功能变化。每次功能更新都必须先确定版本号，并同步更新 `README.md`、`PROJECT.md`、`AGENTS.md`、`pyproject.toml` 和 FastAPI 应用版本。

## `v0.2.0` - 2026-07-12

### 新增功能

- 新增 `pdf_service.py`，支持 PDF 文本清理、按页提取、重叠分块和后台解析。
- 新增 `rag_service.py`，支持论文访问链接、全文缺失提示和 RAG Prompt 构造。
- 新增 `POST /papers/{paper_id}/parse` 接口。
- 新增 `paper_documents` 数据表，保存解析状态、页数、文本块数量和错误原因。
- 新增 `paper_chunks` 数据表，保存论文 ID、页码、文本块顺序和正文。
- 论文详情页新增 PDF 解析按钮、解析状态、错误信息和文本块预览。
- 未取得全文时，页面和 Prompt 会提示论文可能需要购买或机构订阅，并提供论文网页或 DOI 链接。
- 新增 `pypdf` 依赖。
- 新增 `GET /api/papers/statuses`，提供下载与解析状态的轻量轮询数据。
- 新增 `status_poll.js`，每 2 秒自动更新页面状态，无需手动刷新。
- 新增 `downloaded_at` 字段，记录并展示论文实际下载成功日期。
- 新增 `clear_paper_history()` 和 `POST /papers/clear`，支持在历史页清空数据库记录。
- 清空历史记录前增加二次确认，并明确保留本地 PDF 文件。
- 将 PDF 获取方式由“搜索后自动下载”改为“发现可用 PDF 后显示下载按钮”。
- 新增 `available` 状态和 `POST /papers/{paper_id}/download` 接口。
- 用户点击下载后才进入 `downloading` 并由 `BackgroundTasks` 后台处理。
- 接入 DeepSeek `/chat/completions`，实现真实大模型论文问答。
- 新增 `model_service.py`，统一处理 DeepSeek 配置、鉴权、请求、超时和异常。
- 删除 `agent_service.py`，其确定性搜索词入口合并到 `model_service.py`。
- 新增本地 `local-tfidf-v1` 向量索引，不依赖 OpenAI 或其他 Embedding API。
- 新增 `index_paper_chunks()`、`retrieve_relevant_chunks()` 和 `answer_rag_query()`。
- 新增 `rag_queries` 表，保存问题、回答、页码证据、模型、状态和错误。
- `paper_chunks` 新增 `vector_json`，`paper_documents` 新增索引状态、向量器和索引时间字段。
- 新增 `POST /papers/{paper_id}/index` 和 `POST /papers/{paper_id}/ask`。
- 新增 `.env.example`，使用 `DEEPSEEK_API_KEY` 等环境变量，真实 `.env` 不提交 Git。
- 接入火山方舟 `/api/v3/embeddings`，支持 Seed1.5-Embedding 或对应 Endpoint ID。
- 新增 `is_seed_configured()` 和 `embed_with_seed()`，支持批量稠密向量生成与响应顺序校验。
- `paper_chunks` 新增 `dense_vector_json` 保存 Seed 稠密向量。
- 检索升级为 `0.75 × Seed语义相似度 + 0.25 × TF-IDF关键词相似度` 的双路混合召回。
- Seed 未配置或调用失败时自动降级为 TF-IDF，索引仍保持可用并记录降级原因。
- `.env.example` 新增 `SEED_API_KEY`、`SEED_BASE_URL`、`SEED_EMBEDDING_MODEL` 和 `SEED_TIMEOUT`。

### 规则与安全

- 大模型只能根据提供的元数据和已解析全文片段回答。
- 没有全文时不得假装已经阅读论文，也不得断言论文一定收费。
- 不提供绕过付费墙、登录验证或版权限制的方法。

### 文档与工程

- `README.md`、`PROJECT.md` 和 `AGENTS.md` 已同步记录新增模块、函数、路由、数据库表和状态。
- 新增版本号和更新日期维护规范。
- 新增 `CHANGELOG.md` 作为统一版本历史入口。
- 将 `outputs/` 加入 `.gitignore`，避免提交临时检查产物。

### 验证结果

- 自动化测试：`17 passed`，覆盖 DeepSeek 请求封装、TF-IDF 召回、问答入库和问答页面表单。
- Seed API 封装与混合召回测试已经新增，测试文件现有 19 个用例；本轮因执行额度限制未能重新运行完整 `pytest`，需在下次运行时复核。
- FastAPI 首页、历史页和论文详情页响应检查：均返回 HTTP `200`。
- `/papers/{paper_id}/parse`、`/index` 和 `/ask` 等路由已完成注册检查。

### 尚未实现

- Cross-Encoder 或大模型重排序。
- 多论文联合问答。
- RAG 评测集和自动质量指标。

## `v0.1.x` - 日期未记录

### 已有功能

- 完成 FastAPI 搜索、历史记录、论文详情和 CSV/JSON 导出。
- 接入 arXiv 与 Crossref 检索。
- 使用 `BackgroundTasks` 后台下载 PDF。
- 实现 Crossref `link_only` 平滑降级策略。
- 使用 SQLite 保存论文元数据和下载状态。
- 增加出版商字段、旧数据库迁移及页面展示。
- 增加 Origin 数据导出和可选自动绘图功能。
- 将项目文档重写为中文。

> 这些功能来自项目早期迭代，但当时没有逐次记录可靠日期，因此统一保留为 `v0.1.x` 历史说明。
