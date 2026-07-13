# Paper Hunter 实验记录

> 当前适用版本：`v0.7.0`
>
> 更新日期：`2026-07-13`

本文件是项目的实验日志。`README.md` 负责使用说明，`CHANGELOG.md` 负责版本变化，本文专门记录每次实验为什么做、只改了什么、如何验证、结果是什么以及下一步是否继续。

## 实验纪律

1. 固定基准数据：当前使用 12 篇论文、60 道人工标注问题，每篇包含事实型、方法型、参数型、结果型和原因型各 1 题。
2. 一次只修改一个主要变量。分块、查询扩展、召回融合和重排不得在同一次实验中同时改变。
3. 每次运行必须保留独立的 `evaluation_runs` 记录，不覆盖旧结果。
4. 每次记录固定参数、变量参数、Embedding 模型、缓存命中、运行时间、Hit@K、MRR、证据覆盖率和逐题变化。
5. 效果结论必须基于完整 60 题，不再使用旧 3 篇、15 题结果推断当前性能。
6. 代码、测试、版本号、本文和其他项目 Markdown 必须在同一次功能更新中保持一致。

## 已完成基线

两组实验均使用 `chunk_size=1200`、`overlap=150`、`top_k=5`、Seed 权重 `0.75`、TF-IDF 权重 `0.25` 和 `doubao-embedding-vision-251215`。

| 实验 | 运行 | 唯一主要变量 | 块数 | Hit@1 | Hit@3 | Hit@5 | MRR@5 | Top-5 覆盖率 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| E0 | `#5` | `length_boundary` | 785 | 43.33% | 66.67% | 81.67% | 0.5747 | 79.25% |
| E1 | `#6` | `academic_section` | 794 | 50.00% | 66.67% | 75.00% | 0.5950 | 74.34% |

E1 相比 E0 排名改善 17 题、变差 12 题、持平 31 题；Top-5 新增命中 3 题、丢失命中 7 题。对 7 道丢失题检查全部章节语料后，标准证据最高覆盖率均不低于 `96.92%`，多数为 `100%`。因此当前证据表明主要问题是排序变化，不能仅凭 Top-5 覆盖率下降断言章节边界删除或切断了正文。

## C0：实验增量缓存

### 状态

- 代码实现：已完成。
- 离线测试：已完成，`35 passed`。
- 本地数据库迁移：已完成；`pdf_text_cache`、`embedding_cache` 和五个配置字段检查通过，迁移后两张缓存表初始均为 0 行。
- 真实缓存预热：已完成，运行 `#7`。
- 缓存复跑耗时验证：已完成，运行 `#8`。
- Git 提交与推送：待执行。

### 动机

`v0.6.1` 的 `run_configured_experiment()` 每次都会重新读取全部 PDF、删除并重建文本块、重新生成所有块向量，再为 60 道问题生成查询向量。当前 Seed 多模态接口对每段文本单独发起请求，一次约 800 个块加 60 个问题会产生接近 900 次网络请求。只修改 Top-K 或融合权重也重复这些工作，实验成本与变量变化范围不匹配。

### 本步骤唯一目标

只优化实验计算路径，不修改分块内容、召回公式、证据匹配阈值或大模型回答逻辑。缓存前后在相同配置下应得到相同检索指标。

### 代码改动

#### `my_agent_project/db.py`

- 新增 `pdf_text_cache` 表，以 `source_hash + page_number` 保存 PDF 页文本。
- 新增 `embedding_cache` 表，以 `cache_namespace + content_hash` 保存 Seed 向量。
- `paper_documents` 新增 `source_hash`、`chunk_strategy`、`chunk_size`、`chunk_overlap` 和 `embedding_namespace`。
- 新增 `get_cached_pdf_pages()`、`replace_cached_pdf_pages()`、`get_cached_embeddings()` 和 `upsert_cached_embeddings()`。
- `replace_paper_chunks()` 同步保存当前 PDF 指纹和分块配置。
- `replace_chunk_vectors()` 同步保存生成向量时使用的 Seed 命名空间。

#### `my_agent_project/pdf_service.py`

- 新增 `pdf_source_hash()`，分块前计算 PDF 的 SHA-256 内容指纹。
- 新增 `extract_pdf_pages()`，只负责昂贵的 `pypdf` 页文本提取。
- 新增 `build_pdf_chunks()`，只负责把已提取页文本转换为指定策略的文本块。
- `extract_pdf_chunks()` 改为组合上述两个步骤，保持原公共接口兼容。
- `parse_paper_pdf()` 先比较 PDF 指纹与分块配置：完全一致时复用当前块；配置变化时读取 `pdf_text_cache`，只重新分块；PDF 变化时才重新调用 `pypdf`。

#### `my_agent_project/model_service.py`

- 新增 `seed_embedding_cache_namespace()`，使用 Seed 服务地址、模型名称和接口模式隔离缓存。
- API Key 不进入缓存键、日志或 Git 文件。

#### `my_agent_project/rag_service.py`

- 新增 `embedding_content_hash()`，使用文本 SHA-256 标识可复用向量。
- 新增 `_cached_seed_embeddings()`，先读取 SQLite，只把缺失文本发送给 Seed，并按原输入顺序恢复向量。
- 新增 `cache_active_chunk_embeddings()`，把升级前 `paper_chunks.dense_vector_json` 中已有向量回填到新缓存。
- 新增 `document_index_is_reusable()`，只有索引状态和 Seed 命名空间同时匹配时才直接复用。
- `index_paper_chunks()` 改为仅补算缓存缺失的块向量。
- `retrieve_relevant_chunks()` 改为复用相同问题的 Seed 查询向量。

#### `my_agent_project/evaluation_service.py`

- `run_configured_experiment()` 在覆盖活动分块前先回填已有向量。
- 未修改分块配置时跳过 PDF 解析、分块和论文索引。
- 修改分块配置时复用 PDF 页文本，并仅补算新块向量。
- `run_evaluation()` 记录 `cache_stats`、`preparation_seconds`、`evaluation_seconds` 和 `total_duration_seconds`。

#### `my_agent_project/templates/evaluation.html`

- 实验按钮由“重建索引并运行实验”改为“运行实验”。
- 运行详情新增总耗时，以及 PDF 页、分块配置、块向量、问题向量和论文索引的命中/新增数量。

#### `tests/test_services.py`

- 新增 PDF 页文本跨策略复用测试。
- 新增相同块和相同问题不重复调用 Seed 的测试。
- 更新配置实验测试，验证缓存感知流水线仍按论文去重执行。
- 更新评测页测试，验证缓存统计和耗时能够显示。

### 缓存失效规则

| 用户改动 | PDF 页文本 | 当前分块 | 块向量 | 问题向量 | 必须重新计算 |
| --- | --- | --- | --- | --- | --- |
| 只改 Top-K | 复用 | 复用 | 复用 | 复用 | 排名与指标 |
| 只改 Seed/TF-IDF 权重 | 复用 | 复用 | 复用 | 复用 | 融合分数、排名与指标 |
| 改分块策略/大小/重叠 | 复用 | 重建 | 内容相同则复用，否则补算 | 复用 | 新块与缺失块向量 |
| 替换 PDF | 重建 | 重建 | 内容相同部分可复用 | 复用 | 新页、新块与缺失向量 |
| 更换 Seed 模型或接口模式 | 复用 | 复用 | 重建 | 重建 | 新模型全部向量 |

### 离线验证

执行命令：

```powershell
uv run pytest
```

当前结果：`35 passed`。测试使用临时 SQLite 和伪造 Embedding，不消耗真实 API 额度。

### 本地数据库迁移验证

执行 `db.init_db()` 后检查 SQLite：

```text
tables_ok=True
columns_ok=True
pdf_cache_rows=0
embedding_cache_rows=0
```

本步骤只创建表和字段，没有重新解析 PDF、调用 Seed 或修改 `evaluation_runs`。

### 真实缓存预热：运行 `#7`

固定配置仍为 `academic_section`、`1200/150`、`top_k=5`、Seed/TF-IDF 权重 `0.75/0.25`。本次不改变算法，只初始化 `v0.7.0` 缓存。

```text
总耗时：105.876 秒
准备耗时：19.763 秒
评测耗时：86.113 秒
PDF：12 个文件首次写入，211 个物理页首次写入
块向量：794 个旧向量回填，794 命中，0 次 Seed 补算
问题向量：0 命中，60 次 Seed 首次计算
缓存结果：pdf_text_cache=211 行，embedding_cache=854 行
```

运行 `#7` 指标为 `Hit@1=51.67%`、`Hit@3=66.67%`、`Hit@5=75.00%`、`MRR@5=0.6033`、覆盖率 `74.34%`。其中 Hit@1 和 MRR 与旧运行 `#6` 有轻微差异。原因是 `v0.6.1` 没有保存历史问题向量，预热时必须重新请求 60 个 Seed 查询向量；这批新向量可能造成临界排序变化。`v0.7.0` 已保存这批查询向量，后续同配置复跑应固定使用相同向量。是否真正可复现由运行 `#8` 验证，不能提前判定缓存改变了算法。

### 全缓存复跑：运行 `#8`

运行参数与 `#7` 完全一致，没有修改分块、权重或 Top-K。

```text
总耗时：4.406 秒
准备耗时：0.239 秒
评测耗时：4.167 秒
分块配置：794 命中
论文索引：12 命中，0 重建
问题向量：60 命中，0 次 Seed 请求
```

运行 `#8` 的 `Hit@1=51.67%`、`Hit@3=66.67%`、`Hit@5=75.00%`、`MRR@5=0.6033`、覆盖率 `74.34%`，与 `#7` 完全一致。预热到全缓存复跑的总耗时从 `105.876` 秒降低到 `4.406` 秒，约快 `24.0` 倍，节省约 `95.8%` 时间。该比较只衡量同配置重复实验，不代表首次运行或新分块策略一定提升相同比例。

### C0 结论

- 同一配置复跑不再调用 `pypdf` 或 Seed，指标可重复。
- 只改 Top-K 或融合权重时可以直接使用现有缓存，预计保持秒级。
- 切换到新分块策略时仍需生成新块，但会复用 211 页原始文本、60 个问题向量和内容完全相同的旧块向量。
- C0 达到目标；下一步不得自动进入 E2，需先由用户确认。

### 尚未得出的结论

- 尚未记录真实缓存预热耗时和第二次复跑耗时，不能在完成实测前写“已经提升多少倍”。
- 缓存不改变 E0/E1 的效果结论，也不能提升 Hit@K；它只降低重复实验时间和 API 调用次数。
- 本步骤完成前不开始章节前缀、中英查询、RRF、重排、父子块或表格分块实验。

## 后续实验队列

以下项目只登记，不在 `v0.7.0` 同时实现。

1. `E2`：章节前缀消融。保持 E1 的块边界不变，只比较“正文向量”和“章节路径 + 正文向量”。论文标题暂不加入，因为当前评测在单篇论文内部检索，标题不能区分同一论文中的不同块。
2. `E3`：冻结的中英文双路查询 + BM25/RRF。先把 60 道问题的英文查询和关键词固化到基准，避免每次运行由大模型随机改写。
3. `E4`：Embedding/BM25 初召回 Top-20 后重排到 Top-5。
4. `E5`：600-900 字符子块检索、1200-1800 字符父块返回。
5. `E6`：表格标题、表头、数据行和注释一体化分块。
6. 原子命题索引、Late Chunking 和 RAPTOR 暂缓；当前基准以单段明确证据为主，且 Seed API 不提供 Late Chunking 所需的 token 级长上下文输出。
