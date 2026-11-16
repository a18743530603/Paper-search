# Paper Hunter 系统架构

本文只描述当前 `v0.18.0` 的生产主链路，不保留按版本堆叠的开发日志。

## 1. 职责边界

| 层 | 主要职责 |
| --- | --- |
| FastAPI Web | 接收短请求、渲染工作台、创建持久任务、输出 SSE |
| LangGraph | 编排检索计划、人工确认、论文选择和检查点恢复 |
| MySQL Business DB | 保存论文、Chunk、消息、任务、报告、Trace 与业务终态 |
| SQLite Checkpoint | 保存 LangGraph 图执行位置，不作为业务事实来源 |
| Independent Worker | 执行下载、解析、索引、报告和兼容 Follow-up 等长任务 |
| Research Agent | 依据问题要求、Evidence Coverage 与 Gap 选择研究动作 |
| Context Builder | 校验、去重、分配证据预算并构造稳定回答上下文 |

Web 请求不直接承担耗时的 PDF 下载与解析。任务先以短事务写入 MySQL，随后由 Worker 领取并执行，从而允许请求快速返回，也允许进程中断后继续恢复。

## 2. 从论文检索到研究问答

```text
用户输入研究主题
  → DeepSeek 生成 2–4 条检索词
  → LangGraph interrupt 等待用户确认
  → Paper Search MCP 从白名单来源检索元数据
  → 去重并保存候选论文
  → 用户选择 1–5 篇论文
  → Worker 执行 Download / MinerU Parse / Index
  → Workspace 开放 Research Chat
  → Research Agent 多轮获取论文证据
  → Context Builder 组织最终证据
  → DeepSeek 生成带引用回答
  → SSE 流式返回文字与视觉附件事件
```

LangGraph 主图在论文选择后结束；论文准备由业务队列管理，避免把分钟级任务绑定在一次图调用或 Web 请求上。

## 3. PDF 解析与多粒度证据

MinerU 返回结构化 Block、章节信息、页码、内容类型和 bbox。项目将其转换为两层证据：

- **Child Chunk**：粒度较小，用于建立向量和词面索引。
- **Parent Chunk**：由同一论文、同一章节路径、同一物理页的连续 Block 组成，用于命中后的上下文扩展。

Parent 采用受控目标长度并设置硬上限，不跨章节、不跨物理页。Child 保存真实 `parent_chunk_id`；不存在父子数据时系统显式降级，而不会伪造 Parent 命中。

章节、物理页、内容类型和 bbox 会沿检索链路保留。回答模型看到的不只是标题和正文，还能看到 Evidence 所属章节及真实页码。

## 4. 多阶段检索

```text
original_query_zh ──→ Seed Semantic ─┐
                                      ├─→ Weighted Hybrid 0.75 / 0.25
lexical_query_en  ──→ TF-IDF ─────────┘
  → Pseudo Relevance Feedback
  → Candidate Pool Top50
  → BGE Reranker
  → Reranker Top2 Safeguard
  → MMR 补齐 Final Top5
  → Parent Expansion
  → Dynamic Evidence Allocation
```

英文 Retrieval Query 是检索辅助信息：它不会替换用户原问题、不会进入回答 Prompt，也不能作为论文事实。生成失败时 TF-IDF 自动回退到原问题，Research Chat 继续运行。

动态证据分配分为两步：先尽量让每篇有合法候选的论文至少进入一条证据，再按稳定轮询利用剩余预算。单条过长时跳过并继续尝试后面的短证据，不因一条放不下而提前终止。

## 5. Coverage-driven Research Loop

Agent 首先按用户实际提问拆分 Answer Requirements。每轮只执行一个动作：

```text
Decision
  → 一个 Research Tool
  → ResearchObservation
  → 将 Observation 以 role=tool 回传模型
  → 全局 Coverage Reflection
  → 下一次 Decision 或 complete_research
```

Coverage 状态：

- `SUPPORTED`：已有足够 Original Paper Evidence，且必须绑定真实 Evidence ID。
- `PARTIAL`：已有相关证据，但仍缺关键部分。
- `MISSING`：尚无足够证据，但仍存在合理研究路径。
- `NOT_AVAILABLE`：经过合理研究后，没有明显的新路径可继续获得证据。

Python 不替模型做语义判断，但执行硬合同：工具白名单、参数合法性、Evidence ID 真实性、完全重复调用拦截，以及 Observation 结构校验。

Loop 还包含两类安全机制：

- 连续两轮既没有新增 Evidence、Coverage 也未改善时，向下一轮注入 replan 提示，要求换策略。
- 达到 8 次动作或 90 秒时以 `EXHAUSTED` 安全结束，保留已有证据和未解决 Gap；只有真正的程序或合同异常才是 `FAILED`。

## 6. 可靠后台任务

多个 Worker 通过 MySQL 行锁与 `SKIP LOCKED` 竞争不同任务。领取操作只在短事务内完成：锁定候选任务、写入 owner 与 lease、提交事务，然后在事务外执行下载或模型调用。

- **租约与心跳**：识别失联 Worker，过期任务可以重新领取。
- **CAS 状态更新**：更新时比较 owner、状态或版本，受影响行数不符合预期则拒绝旧 Worker 覆盖新状态。
- **幂等结果复用**：重试前检查业务终态和已有 Artifact，尽量避免重复副作用。
- **有限重试**：可恢复错误重新入队，耗尽后进入明确失败状态。

系统保证的是可恢复执行和尽量一次的业务结果，不把网络调用、模型调用和文件写入错误地宣称为严格 Exactly Once。

## 7. Evidence Safety 与记忆

上下文被分为不同信任层：

- Original Paper Evidence：唯一可以支撑论文事实和 Citation 的内容。
- Task Memory：保存当前任务中的稳定偏好、研究重点和决定。
- Conversation Summary / Recent Messages：帮助理解上下文。
- Existing Report：可选参考，不替代新召回证据。

模型提出的 Memory 更新还需经过代码校验后才会写库。摘要、历史消息或英文 Retrieval Query 即使出现论文术语，也不能被升级为事实证据。

## 8. 视觉证据

视觉 Tool 只接受属于当前论文的 `table / image / chart` Chunk，并校验页码、bbox、PDF 哈希和页面旋转。裁图文件保存在受控目录，通过稳定的 Visual ID 和只读接口访问，客户端不能传任意本地路径。

当前 Visual ID 在同一解析快照内稳定；论文重新解析后旧 Chunk ID 可能变化，这是 v1 明确保留的边界。
