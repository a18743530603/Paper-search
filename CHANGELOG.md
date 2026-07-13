# Paper Hunter 版本更新记录

本文件按时间倒序记录项目的功能变化。每次功能更新都必须先确定版本号，并同步更新 `README.md`、`PROJECT.md`、`AGENTS.md`、`pyproject.toml` 和 FastAPI 应用版本。

## `v0.5.0` - 2026-07-13

### 新增功能

- 评测页新增实验配置表单，可设置实验名称、块大小、重叠、Top-K 和 Seed 语义权重；TF-IDF 权重自动取补数。
- 新增不可变 `ExperimentConfig`，以及 `validate_experiment_config()`、`create_experiment_run()` 和 `run_configured_experiment()`。
- 每次实验会对标准论文强制重新提取文本块、重新调用 Seed 建立稠密向量，并保存该次参数、总文本块数、指标和逐题召回快照。
- 历史运行表新增块大小与重叠列；运行详情新增 Top-K 和总文本块数。

### 修改内容

- `pdf_service.extract_pdf_chunks()`、`parse_paper_pdf()` 新增 `chunk_size` 和 `overlap` 参数，同时保留普通解析的默认值 `1200/150`。
- `rag_service.retrieve_relevant_chunks()` 新增语义/关键词权重参数，使用前自动归一化并拒绝无效权重。
- 当实验 `top_k` 大于 5 时，Hit@5、MRR@5 和平均证据覆盖率仍只按前 5 个结果计算，保证不同 Top-K 运行可比较；更多结果仅用于错误分析。
- 修复重叠输入 `step=50` 导致 `120` 被浏览器判定无效、无法提交的问题，步进改为 `10`。

### 真实参数实验

- 基线 `1200/150`：约 `163` 块，`Hit@1=66.67%`、`Hit@5=93.33%`、`MRR@5=0.7667`、覆盖率 `91.49%`。
- 实验 `900/120`：`217` 块，`Hit@1=53.33%`、`Hit@5=93.33%`、`MRR@5=0.6856`、覆盖率 `92.80%`。
- 结论：更小块提高了平均证据覆盖，但增加候选与排序噪声，不能单凭粒度变细判断分块策略更优。

### 验证结果

- 新增实验配置边界、权重推导、按论文去重重建、总块数统计和表单渲染测试。
- 完整自动化测试：`27 passed`。
- 已在桌面端和 `390 x 844` 窄屏下完成浏览器验收，配置表单、后台状态、双运行趋势曲线和响应式布局均正常。

### 尚未实现

- 页面策略目前仅有 `length_boundary`；语义分块、自适应分块和父子分块将在后续版本实现。
- 尚未加入 Faithfulness、Answer Correctness 等生成层指标，本版本仍只评测检索层。

## `v0.4.1` - 2026-07-13

### 新增功能

- 评测页新增“当前运行 Top-K 命中率”曲线，直观展示 Hit@1、Hit@3、Hit@5 随候选数量增加的变化。
- 评测页新增“历次运行趋势”曲线，按运行编号比较 Hit@1、Hit@3 和 Hit@5，便于后续对照不同分块及召回参数。
- `evaluation_poll.js` 新增 `drawLineChart()` 和 `renderEvaluationCharts()`，使用原生 Canvas 绘图，不依赖外部 CDN，也不会额外调用 Embedding 或 DeepSeek API。
- README 新增完整评测操作流程、基准 CSV 字段要求、物理页码说明和对照实验注意事项。

### 修改内容

- 评测运行表格增加指标数据属性，作为历史趋势图的数据来源。
- 图表会跟随窗口宽度重新绘制，并在窄屏下自动切换为单列布局。
- 项目版本由 `v0.4.0` 更新为 `v0.4.1`；本次与基线评测属于同日的小版本完善。

### 验证结果

- 新增评测模板指标数据与两个 Canvas 容器的渲染测试。
- 完整自动化测试：`24 passed`。
- 已在桌面端和 `390 x 844` 窄屏下完成浏览器验收，确认两张曲线正常绘制且布局无重叠。

## `v0.4.0` - 2026-07-13

### 新增功能

- 新增 `benchmarks/chunking_baseline.csv`，保存 3 篇论文、15 条人工标注问题、参考答案、PDF 物理页码和连续原文证据。
- 新增 `evaluation_service.py`，提供 `find_local_pdf()`、`import_benchmark_cases()`、`prepare_benchmark()`、`evidence_coverage()`、`create_baseline_run()` 和 `run_evaluation()`。
- 新增 `evaluation_cases`、`evaluation_runs`、`evaluation_results` 三张 SQLite 表，分别保存标准案例、运行参数/指标和逐题召回结果。
- 新增 `db.register_local_paper()`，支持将 `downloads/papers/` 中已有 PDF 注册为可解析论文记录。
- 新增 `GET /evaluation`、`POST /evaluation/prepare`、`POST /evaluation/run` 和 `GET /evaluation/runs/{run_id}`。
- 新增评测页面，展示运行参数、总体指标、历史运行、标准案例和逐题 Top-K 召回片段。
- 新增 `evaluation_poll.js`，评测运行期间每 2 秒自动刷新。

### 指标与匹配规则

- 使用“PDF 物理页码一致 + 规范化 token 覆盖率不低于 0.65”判断召回块是否命中标准证据。
- 计算 `Hit@1/3/5`、`Recall@1/3/5`、`MRR@5` 和平均最佳证据覆盖率。
- 第一版每题只有一条标准证据，因此 `Recall@K` 与 `Hit@K` 数值相同；后续多证据问题会让两者产生区别。
- 匹配逻辑容忍 PDF 提取造成的换行、断词和符号差异，不使用整句字符串全等。

### 真实基线结果

- 固定分块策略：`length_boundary`，`chunk_size=1200`，`overlap=150`，`top_k=5`。
- 混合召回：Seed1.6 语义权重 `0.75`，TF-IDF 关键词权重 `0.25`。
- 15 条问题实测：`Hit@1=66.67%`、`Hit@3=80.00%`、`Hit@5=93.33%`、`MRR@5=0.7667`、平均最佳证据覆盖率 `91.49%`，无 API 错误。
- 唯一未命中案例为 AOD 参数题；正确证据块内容完整、覆盖率 100%，但排名第 18，属于块级排序失败，不是文本被切断。

### 验证结果

- 新增证据断词容错、指标聚合和评测数据库持久化测试。
- 完整自动化测试：`23 passed`。

### 尚未实现

- 结构化分块、语义分块和父子层级分块。
- 多条标准证据及跨段问题评测。
- Cross-Encoder 重排序、查询翻译和分块策略对比图。

## `v0.3.0` - 2026-07-13

### 新增功能

- 接入 Seed1.6 `Doubao-embedding-vision-251215`，用于论文文本块和用户问题的稠密向量生成。
- `model_service._seed_api_mode()` 根据模型名称和 `SEED_EMBEDDING_API_MODE` 自动选择文本或多模态接口。
- `model_service._request_seed_embedding()` 统一处理火山方舟鉴权、超时、HTTP 错误和 JSON 响应。
- `model_service._extract_multimodal_vector()` 兼容多模态接口的嵌套向量响应，并校验向量有效性。
- `.env.example` 新增 `SEED_EMBEDDING_API_MODE`，推荐模型改为 `doubao-embedding-vision-251215`。

### 修改内容

- `embed_with_seed()` 对 `embedding-vision` 模型调用 `/api/v3/embeddings/multimodal`，每个论文文本块使用一个纯文本输入对象。
- 继续兼容旧文本模型的 `/api/v3/embeddings` 批量请求；使用 `ep-...` 接入点时可显式设置 `text` 或 `multimodal`。
- 火山方舟请求失败时仍自动降级到本地 TF-IDF，不中断论文索引。
- `pyproject.toml` 与 FastAPI 应用版本同步升级为 `0.3.0`。

### 安全说明

- 真实 DeepSeek 与火山方舟 API Key 只能写入被 Git 忽略的 `.env`，不得写入代码、`.env.example`、测试或 Markdown 文档。
- 在聊天、截图或公开页面中暴露过的密钥必须撤销并重新生成。

### 验证结果

- 新增多模态 Embedding 请求结构、逐文本向量顺序和嵌套响应解析测试。
- 完整自动化测试：`20 passed`，覆盖旧文本接口、新版多模态接口、混合召回、DeepSeek 请求封装和 Web 页面流程。
- `2026-07-13` 完成真实 API 连通性验收：`doubao-embedding-vision-251215` 成功返回 1 个 2048 维向量，`deepseek-v4-pro` 成功返回预期短答。

### 尚未实现

- 暂未使用新模型的图片与视频向量能力，当前只向量化 PDF 提取后的文本。
- 尚未使用真实论文完成“下载、解析、索引、检索、生成答案”的浏览器全流程验收。

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

- 自动化测试：`19 passed`（`2026-07-13` 复核），覆盖 DeepSeek 请求封装、Seed API 请求封装、Seed + TF-IDF 混合召回、问答入库和问答页面表单。
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
