# Research Chat 端到端评测与 Badcase 报告

> 评测版本：`research-chat-e2e-v1`
>
> 运行时间：`2026-11-18T10:32:00.866938+00:00`
>
> 模型：`deepseek-v4-flash-vision-exp`

## 1. 评测范围

本评测复用冻结检索集中的真实论文与问题，执行完整生产链路：

```text
用户问题 → DeepSeek Function Calling → Research Tool
→ Original Paper Evidence → Coverage Reflection → complete_research
→ 最终回答 → 引用校验 → Answer Judge
```

每条案例记录 Tool 轨迹、最终 Evidence、Target Chunk 覆盖、回答完整性、
事实忠实度和引用正确性。完整回答与证据摘要保存在本地原始 JSON，公开报告
只保留可审阅的指标和 Badcase 原因。

## 2. 汇总结果

| 指标 | 结果 |
| --- | ---: |
| 案例数 | 12 |
| 正常完成评测 | 10/12 |
| Loop 执行错误 | 2 |
| 全合同通过 | 5/12 |
| 回答完整 | 7/12 |
| 引用正确 | 7/12 |
| Target Chunk Coverage | 9/22 (40.91%) |
| Faithfulness | 100.00% |
| Answer Relevance | 97.00% |
| Reference Correctness | 91.00% |
| Citation Correctness | 81.56% |
| 平均 Tool Action | 1.5 |
| 平均总耗时 | 28.629 秒 |

结束状态：`{"COMPLETED": 10, "FAILED": 2}`

工具调用：`{"find_in_paper": 1, "gather_evidence": 9, "list_sections": 2, "read_section": 3}`

Badcase 阶段：`{"answer_completeness": 3, "citation": 3, "evidence_retrieval_or_selection": 3, "loop_execution": 2}`

## 3. 逐题结果

| Case | 类型 | 结束 | Tool 路径 | Target | 完整性 | 引用 | Badcase |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| ict-datasets | dataset | COMPLETED | gather_evidence → complete_research | 1/1 | 未通过 | 未通过 | answer_completeness, citation |
| ict-method | method | COMPLETED | gather_evidence → complete_research | 0/1 | 通过 | 未通过 | evidence_retrieval_or_selection, citation |
| ict-consistency-mechanism | fine_grained_mechanism | COMPLETED | gather_evidence → read_section → complete_research | 1/1 | 通过 | 通过 | — |
| ict-metrics | metric | COMPLETED | gather_evidence → find_in_paper → complete_research | 1/1 | 通过 | 通过 | — |
| ict-results | results | FAILED | — | 0/5 | 未通过 | 未通过 | loop_execution |
| ict-conclusion | conclusion | COMPLETED | list_sections → read_section → complete_research | 1/1 | 通过 | 通过 | — |
| hidiff-datasets | dataset | COMPLETED | gather_evidence → complete_research | 1/1 | 通过 | 通过 | — |
| hidiff-method | method | COMPLETED | gather_evidence → complete_research | 0/3 | 未通过 | 通过 | evidence_retrieval_or_selection, answer_completeness |
| hidiff-metrics | metric | COMPLETED | gather_evidence → complete_research | 1/1 | 通过 | 通过 | — |
| soil-data | dataset | COMPLETED | gather_evidence → list_sections → read_section → complete_research | 2/2 | 通过 | 未通过 | citation |
| soil-model | method | COMPLETED | gather_evidence → complete_research | 1/3 | 未通过 | 通过 | evidence_retrieval_or_selection, answer_completeness |
| soil-results | results | FAILED | — | 0/2 | 未通过 | 未通过 | loop_execution |

## 4. Badcase 分析

### ict-datasets — 这篇论文使用了哪些数据集进行实验？

- 失败阶段：`answer_completeness, citation`
- Tool 路径：`gather_evidence → complete_research`
- 最终 Evidence：`paper_chunk:377, paper_chunk:370, paper_chunk:394, paper_chunk:373, paper_chunk:385`
- Target：`[370]/[370]`
- 未解决 Coverage：无
- Judge：faithfulness=1.00, relevance=1.00, reference=0.70, citation=0.60
- 诊断备注：答案正确列出了三个数据集，但未提及参考中关于部分标注训练数据的关键细节。

### ict-method — 这篇论文采用了什么方法进行半监督学习？

- 失败阶段：`evidence_retrieval_or_selection, citation`
- Tool 路径：`gather_evidence → complete_research`
- 最终 Evidence：`paper_chunk:335, paper_chunk:347, paper_chunk:596, paper_chunk:333, paper_chunk:336`
- Target：`[]/[334]`
- 未解决 Coverage：无
- Judge：faithfulness=1.00, relevance=1.00, reference=1.00, citation=0.60
- 诊断备注：答案准确描述了 ICT 方法及其一致性正则化核心，与参考回答完全一致，所有引用页面均支持相应主张。

### ict-results — 这篇论文的主要实验结果是什么？

- 执行错误：`ResearchLoopContractError: Tool arguments包含未声明字段：paper_id`

### hidiff-method — HiDiff 的核心方法是什么？

- 失败阶段：`evidence_retrieval_or_selection, answer_completeness`
- Tool 路径：`gather_evidence → complete_research`
- 最终 Evidence：`paper_chunk:619, paper_chunk:631, paper_chunk:632, paper_chunk:759, paper_chunk:630`
- Target：`[]/[686, 687, 692]`
- 未解决 Coverage：无
- Judge：faithfulness=1.00, relevance=0.90, reference=0.70, citation=1.00
- 诊断备注：Answer accurately describes HiDiff's components and training but omits inference details (prior mask initialization, DDIM acceleration) present in the reference.

### soil-data — 这篇论文使用了哪些数据来预测土壤含水量？

- 失败阶段：`citation`
- Tool 路径：`gather_evidence → list_sections → read_section → complete_research`
- 最终 Evidence：`paper_chunk:765, paper_chunk:764, paper_chunk:763, paper_chunk:774, paper_chunk:766, paper_chunk:773, paper_chunk:775, paper_chunk:776, paper_chunk:777, paper_chunk:778, paper_chunk:779, paper_chunk:780`
- Target：`[775, 776]/[775, 776]`
- 未解决 Coverage：无
- Judge：faithfulness=1.00, relevance=1.00, reference=1.00, citation=0.45
- 诊断备注：答案全面覆盖了参考回答中的所有数据来源和细节，所有声明均有证据支持，引用页码有效。

### soil-model — 这篇论文使用了什么模型进行土壤含水量预测？

- 失败阶段：`evidence_retrieval_or_selection, answer_completeness`
- Tool 路径：`gather_evidence → complete_research`
- 最终 Evidence：`paper_chunk:765, paper_chunk:764, paper_chunk:763, paper_chunk:772, paper_chunk:783`
- Target：`[783]/[779, 780, 783]`
- 未解决 Coverage：无
- Judge：faithfulness=1.00, relevance=0.80, reference=0.70, citation=1.00
- 诊断备注：答案正确描述了模型架构，但未提及论文比较了AE与LSTM两类方案，且未明确模型名称为卷积序列到序列自编码器。

### soil-results — 这篇论文最后得到的主要实验结果是什么？

- 执行错误：`ResearchLoopContractError: Tool arguments包含未声明字段：paper_id`

## 5. 跨案例诊断

- Tool 参数合同错误：`ict-results, soil-results`。这类问题发生在业务 Tool 执行前，说明当前单次纠正只覆盖多 Tool Call，尚未覆盖参数中出现未声明字段的情况。
- 未命中精确 Target 但回答完整：`ict-method`。这说明 Agent 可能找到同页或同主题的等价证据，Exact Target 不能单独代表回答质量。
- 回答不完整：`ict-datasets, hidiff-method, soil-model`。主要表现为缺少参考答案中的实验设置、推理过程或对比方案，需要结合最终 Evidence 判断是召回不足还是 Agent 过早完成。
- 页码合法但声明级引用未达阈值：`ict-datasets, ict-method, soil-data`。这类回答通常在段末集中引用多个页面，代码能确认页码来自 Evidence，但 Judge 认为部分原子主张没有紧邻引用。

## 6. 评测边界

- Answer Judge 与被评模型使用同一 DeepSeek 配置，分数用于同项目内诊断，不能视为人工金标准。
- 引用采用两层检查：代码先验证标题和页码来自最终 Evidence，Judge 再判断事实与引用页是否匹配。
- Target Chunk Exact Coverage 用于定位检索与选择损失，不等同于最终答案准确率。
- 当前三篇冻结论文没有持久化 Parent Chunk，因此本轮无法验证真实 Parent 覆盖，只能按 Child ID 统计 Target。
- 本报告不根据结果自动修改 RAG 或 Agent 参数；Badcase 只作为后续工程决策依据。
