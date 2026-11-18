# Paper Hunter 评测说明

项目将“功能是否正确”和“检索效果是否更好”分开验证：代码测试检查合同与可靠性，冻结 Benchmark 检查召回与排序，真实 Smoke 只验证外部协议和完整链路。

## 1. Query Routing A/B

### 目标

验证中文问题面对英文论文时，TF-IDF 是否应使用英文检索表达，而语义检索继续保留中文原问题。

### 控制变量

- 3 篇冻结论文
- 12 个真实问题
- 22 个 Target Chunks
- 相同分块、索引、融合权重和排名计算方式
- 唯一变量：TF-IDF 使用中文原问题或冻结英文 Retrieval Query

### 结果

| 指标 | 中文 TF-IDF | 英文 TF-IDF | 变化 |
| --- | ---: | ---: | ---: |
| Recall@3 | 35.00% | 39.17% | +4.17pp |
| Recall@5 | 40.83% | 53.61% | +12.78pp |
| Recall@10 | 56.39% | 60.83% | +4.44pp |
| Recall@20 | 69.44% | 73.61% | +4.17pp |
| Mean Rank | 17.00 | 15.05 | -1.95 |
| Median Rank | 10.5 | 7.5 | -3.0 |

因此生产采用：中文原问题进入 Semantic Retrieval，英文 Retrieval Query 进入 TF-IDF，再以 `0.75 × semantic + 0.25 × tfidf` 融合。

该实验不能证明最终回答质量同比提升，只能证明目标证据在检索排序中的可见性改善。

## 2. Target Chunk Stage-Drop

### 目标

仅看最终 Recall 无法解释证据为什么丢失，因此评测记录每个 Target 在以下阶段的真实去向：

```text
Hybrid 原始候选
→ Candidate Pool
→ BGE Reranker
→ Final Seed Selection
→ Parent Expansion
→ Dynamic Evidence Allocation
```

Child 阶段优先用稳定 `chunk_id` 精确匹配；Parent 阶段只有真实父节点覆盖 Target Child 时才计为覆盖。没有父子关系的数据必须标记为 fallback，不能伪造 Parent 命中。

### 诊断结论与修复

旧链路的代表性漏斗为：

| 阶段 | Target Coverage |
| --- | ---: |
| Hybrid | 22/22 |
| Candidate Top20 | 17/22 |
| Reranker | 17/22 |
| MMR Top3 | 7/22 |
| Parent | 7/22 |
| Allocation | 7/22 |

主要问题不是 Hybrid 没召回，而是 Top20 过早截断，以及 MMR Top3 在追求多样性时删除高相关证据。

生产修复保持 Hybrid 权重与 MMR λ 不变：

- Candidate Pool：Top20 → Top50
- Final Seed：Top3 → Top5
- Reranker Top1、Top2 强制保留
- MMR 从剩余候选补 3 条，并把 Top2 纳入冗余度计算

验收后最终精确 Target Coverage 从 `7/22` 提升到 `10/22`。这仍是 Chunk 级诊断指标，不应表述成 Answer Accuracy。

## 3. Evidence Allocation 验证

Dynamic Evidence Allocation 的测试重点不是调整检索相关性，而是保证检索选择不会在打包上下文时被破坏：

- Parent Expansion 输入顺序即 MMR Seed Priority，不再按 Hybrid Score 重排。
- 移除固定每篇最多两条的限制，主要由共享 Token Budget 控制。
- Coverage Pass 先尽量覆盖每篇论文，Fill Pass 再稳定轮询补充。
- 当前 Evidence 放不下时继续尝试后面的短 Evidence，而不是直接停止。
- 文本去重保留第一次出现的高优先级证据。

## 4. Function Calling E2E Smoke

真实 Smoke 的通过标准是协议闭环，而不是主观评价答案好不好：

1. DeepSeek 返回原生 `message.tool_calls`。
2. Arguments 能解析为合法对象并通过 Tool Contract。
3. Python Dispatcher 执行真实业务 Tool。
4. 原始 assistant tool call 与对应 `role=tool + tool_call_id` Observation 一起回传。
5. 模型能基于 Observation 继续下一次 Decision。
6. `complete_research` 作为 Completion Action 结束 Loop，而不是被 Tool Dispatcher 执行。

## 5. Research Chat 端到端评测

检索指标只能说明 Target Chunk 的位置，不能说明 Agent 是否选对工具、最终回答是否完整或引用是否正确。`research-chat-e2e-v1` 因此复用3篇冻结论文的12个真实问题，真实执行生产 Research Loop 与回答模型。

每条案例记录：

- Agent 调用的 Research Tool 与参数；
- 最终进入回答上下文的 Evidence ID；
- Requirement Coverage 和结束状态；
- Answer Relevance、Reference Correctness 与 Faithfulness；
- 引用标题/页码是否合法，以及声明是否由对应页支持；
- Badcase 首次暴露在 Loop、Evidence、Answer 还是 Citation 阶段。

首轮运行正常完成10/12题，回答完整7/12，引用正确7/12，全合同通过5/12。两条失败来自模型生成的 Tool Arguments 包含合同未声明字段，证明单独的检索 Recall 无法覆盖 Agent 协议可靠性问题。完整逐题结果和评测边界见 [端到端评测报告](END_TO_END_EVALUATION.md)。

可复现命令：

```powershell
uv run python scripts/evaluate_research_chat_e2e.py
```

原始 JSON 写入 Git 忽略的 `outputs/`，公开仓库只保留冻结案例、可复现脚本和去除正文后的汇总报告。

## 6. 如何理解指标

- **Recall@K**：所有标注目标证据中，有多少在前 K 个结果内出现。
- **Hit@K**：按问题统计，前 K 个结果中至少命中一个目标证据的问题占比。
- **MRR@K**：第一个正确结果越靠前得分越高，超过 K 则记为 0。
- **Exact Target Coverage**：指定 Target Chunk ID 是否仍存在于某阶段，用于定位 Stage Drop。

这些指标衡量的是检索和选择，不自动等于最终答案正确性。最终答案还受证据粒度、Prompt、模型能力和 Citation 约束影响。

## 7. 可复现性边界

- 冻结问题、Target 标注和评测脚本保存在仓库的 `benchmarks/` 与 `scripts/`。
- 本地 PDF、数据库、模型缓存和原始运行输出不提交 Git。
- 依赖在线模型的首次运行可能受网络、模型版本和供应商服务状态影响。
- 本项目报告真实实验范围，不把小规模冻结集外推成通用学术问答准确率。
