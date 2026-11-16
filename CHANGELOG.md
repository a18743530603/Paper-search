# Changelog

本文件只保留适合公开阅读的主要里程碑。详细开发过程、临时实验和本地运行记录不进入公开仓库。

## v0.18.0 — Research Agent 与视觉证据

- 使用 DeepSeek 原生 Function Calling 实现 Coverage-driven Research Loop。
- 提供通用检索、章节导航、精确章节读取、论文内字面定位和图表检查五类 Research Tool。
- 新增 Requirements、Coverage Reflection、Evidence ID 校验、显式完成动作和三种结束语义。
- 加入完全重复调用拦截、连续无进展重规划、8 次动作与 90 秒运行时保护。
- 打通视觉 Chunk 裁图、VisualAsset 受控访问、SSE 视觉事件和消息刷新恢复。

## v0.17.0 — Tool Contract 与章节读取

- 将固定 RAG 抽为独立 Evidence Acquisition 边界，Conversation Service 只负责对话编排。
- 统一 Research Observation 与原始 Child Chunk Evidence 数据结构。
- 实现章节目录、精确章节读取和论文内确定性文本定位。
- 保留章节路径、物理页、Chunk 与 Parent 标识，确保 Citation 可追溯。

## v0.16.0 — Section-aware 与 Parent Chunk

- MinerU 入库同时构建 Child / Parent Chunk，并建立真实父子关系。
- Parent 不跨章节、不跨物理页，正文只来自原始解析结果。
- 章节元数据贯穿检索、Parent Expansion、最终 Evidence 与回答上下文。
- 对 Conclusion 类问题加入基于真实章节列表的确定性导航规则，不把 Discussion 伪装成正式 Conclusion。

## v0.15.0 — Retrieval Selection 与 Evidence Allocation

- Candidate Pool 从 Top20 调整为 Top50。
- Reranker Top2 强制保留，MMR 从剩余候选补齐 Final Top5。
- 动态证据分配保留 MMR Seed Priority，移除固定每篇最多两条的截断。
- 多论文采用 Coverage / Fill 两阶段分配，避免长 Parent 或单篇论文独占上下文。

## v0.14.0 — 跨语言 Query Routing 与多阶段 RAG

- 中文原问题用于 Semantic Retrieval，英文检索表达用于 TF-IDF。
- 英文 Retrieval Query 仅作为检索辅助，不进入回答 Prompt 或 Citation。
- 增加 PRF、BGE CrossEncoder、MMR、Parent Expansion 与逐阶段诊断。
- 冻结 3 篇论文、12 个问题和 22 个 Target Chunks，建立 Query Routing A/B 与 Stage-Drop 分析。

## v0.13.0 — MySQL 持久任务队列

- Business Database 默认迁移到 MySQL 8。
- 使用行锁、`SKIP LOCKED`、租约、心跳、有限重试和 CAS 更新处理多 Worker 竞争。
- 长任务与 Web 请求分离，并保留幂等结果复用和中断恢复。
- LangGraph Checkpoint 继续使用独立 SQLite，明确区分业务事实与图执行位置。

## 更早版本

- FastAPI 文献检索与论文工作台。
- Paper Search MCP 多来源元数据召回。
- 合法开放 PDF 下载、MinerU 解析、索引和带页码 RAG 回答。
- LangGraph 计划确认、论文选择与暂停恢复。
- 冻结 Benchmark、缓存复用、分块策略和检索器消融。
