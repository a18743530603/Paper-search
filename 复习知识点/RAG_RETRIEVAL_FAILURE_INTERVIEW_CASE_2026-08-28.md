# Paper Hunter RAG 召回偏差：“正文已入库，模型却说证据不足”

> 记录编号：`PH-RAG-CASE-001`
> 用途：Paper Hunter 项目复盘，以及 AI Agent、RAG、后端开发岗位的面试准备
> 基线日期：2026-08-28
> 文档版本：`v2.0-evidence-selection-stack`
> 当前状态：**基线诊断与四轮离线实验已完成；Query Routing 已用冻结集验证并接入生产，Semantic-guided PRF、Candidate Pool、BGE CrossEncoder Reranker 与 MMR 已完成生产接入和专项回归；后四层尚未运行同一冻结 Benchmark，因此不能宣称原 CITISEN Case 已被端到端证明修复**

## 0. 案例摘要

这不是一次“大模型不会回答”的故障，而是一次典型的 **Evidence Retrieval 与 Evidence Sufficiency 失配**。

论文的 Section IV 实验正文和 Section V Conclusion 已被 MinerU 完整解析、写入 MySQL 并建立混合索引，但导航型句子因同时出现 `experimental setup` 和 `concluding remarks` 而获得更高语义分数。正文证据随后被全局 Top-3 和“每篇论文最多 2 块”的上下文配额过早截断，DeepSeek 最终只知道“答案在哪个章节”，却没有看到“答案是什么”。

本案例的核心工程认识是：

> **Relevant ≠ Sufficient：检索相关，不等于证据足以回答。**

当前已完成的不再只有根因诊断：Query Routing 已先通过冻结 Ground Truth A/B 再接入生产；其后又按“术语补全、粗召回、精排、去冗余”逐层实现 Semantic-guided PRF、Candidate Pool、BGE CrossEncoder Reranker 与 MMR。后四层均有开关、诊断、失败回退和专项回归，但除 Query Routing 外尚未用同一冻结集量化 Recall 增益，因此工程实现完成不等于质量结论已经完成。

## 1. 问题背景

Paper Hunter 的 Follow-up 回答链路会从当前任务已选论文中重新召回证据，再把 Task Memory、Conversation Summary、Recent Messages、已有 Report 和本轮 Evidence 组装成统一上下文。模型被严格限制为只能使用当前 Evidence 中的论文标题、物理页码和正文片段回答，不允许凭摘要、常识或模型记忆补全事实。

Paper Hunter 不会把整篇 PDF 直接发送给 DeepSeek。生产 RAG 的证据链路为：

```text
论文 PDF
→ MinerU 结构化解析
→ paper_chunks 写入 MySQL
→ TF-IDF + Seed Embedding 混合索引
→ Query Routing：中文原问题给 Semantic，英文检索表达给 TF-IDF
→ Semantic-guided PRF：用当前论文 Semantic Top-3 正文补充作者术语
→ 0.75 Semantic + 0.25 TF-IDF Hybrid 全量排序
→ Parent Dedup
→ Candidate Pool Top-20
→ BGE CrossEncoder Reranker
→ MMR Evidence Diversity Selection
→ Final Top-K
→ ContextBuilder 校验、去重、配额和 Token Budget
→ DeepSeek 基于 Evidence 回答
→ Citation Validation
→ Citation Format Normalization
```

对 MinerU 结构化块，索引文本包含 Section Path、Content Type 和正文；MySQL 中还保留物理页码、bbox、稀疏向量和稠密向量。当前默认混合分数为：

```text
Hybrid Score = 0.75 × Seed Semantic Similarity
             + 0.25 × TF-IDF Similarity
```

Report / Follow-up 当前会对每篇论文先取 Top-3，之后 `fair_budget_v1` 在约 6000 Token Evidence Budget 内做校验和去重，并对每篇论文最多保留 2 块。这意味着单篇论文可能经历：

```text
131 个已索引 chunks
→ 全局 Top-3
→ 每篇最多保留 2 块
→ DeepSeek
```

因此，DeepSeek 最终“知道多少论文内容”，取决于 Retrieval 和 ContextBuilder 选中了什么，而不是取决于 PDF 整体是否已经被解析。

用户对论文《CITISEN: A Deep Learning-Based Speech Signal-Processing Mobile Application》追问：

> 实验是如何设置的，最后 conclusion 讲了什么？

系统却只回答：当前证据只知道 Section IV 是实验，Section V 是结论，但没有具体内容，因此证据不足。

这个现象容易被误判为：

- MinerU 没有解析出 Section IV / V；
- MySQL 迁移后丢失了正文块；
- 向量索引失效；
- DeepSeek “没有读懂论文”。

但最终证明，这些都不是根因。

## 2. 这个问题为什么难

难点不是查一条报错日志，而是要在一条长 RAG 链路中确认证据在哪一层丢失：

```text
PDF
→ MinerU 解析
→ paper_chunks 持久化
→ TF-IDF / Seed 向量索引
→ Hybrid Retrieval Top-K
→ ContextBuilder 去重与配额
→ Follow-up Prompt
→ DeepSeek 回答
→ Citation Validation / Normalization
```

页面上只显示最终的“证据不足”，无法直接说明是解析、召回、上下文裁剪还是模型生成出了问题。如果不按数据流逐层排查，很容易在错误的层次修补 Prompt 或更换模型。

## 3. 证据链与事实确认

### 3.1 最终消息实际使用的 Evidence

从 `research_messages.evidence_json` 查到，这次 Follow-up 最终只保存了两个证据块：

| Chunk | 物理页码 | 内容 | 得分 |
| ---: | ---: | --- | ---: |
| 98 | 3 | 论文结构导航：Section IV 介绍实验，Section V 给出结论 | 0.570165 |
| 90 | 2 | `The contribution of this study is summarized as follows:` | 0.564817 |

这与页面上的回答完全一致：模型只获得了章节位置和一个没有展开的贡献引导句。

### 3.2 MySQL 中的解析与索引事实

`paper_documents` 显示该论文：

- `parse_status = parsed`；
- `index_status = indexed`；
- 物理页数：18；
- 结构化块数：131；
- 解析器：MinerU Cloud VLM；
- 索引：Seed 稠密向量 + TF-IDF 混合索引。

`paper_chunks` 中确实存在可直接回答问题的正文：

| Chunk | 物理页码 | Section | 已入库内容 |
| ---: | ---: | --- | --- |
| 153 | 8 | IV. EXPERIMENTS | 实验概述，说明要评估 SE、MA 和 BNC |
| 154 | 8 | A. EXPERIMENTAL SETUP | TMHINT 语料、16 kHz/16-bit、云端离线实验等设置 |
| 157–159 | 8–9 | SE experiments | 训练/测试语料、噪声、SNR、主观听感测试、PESQ/STOI |
| 160–161 | 9 | MA experiments | MA(N)、MA(S)、MA(S+N) 的数据与微调设置 |
| 162 等 | 9 起 | BNC experiments | BNC 的人类与机器评估方法 |
| 211 | 15 | V. CONCLUSION | 完整总结 SE、MA、BNC 的实验结果和应用价值 |

因此，“没有正文”这个假设被排除。问题发生在入库和索引之后。

### 3.3 当时排名的可复现基线

为避免调试时再次请求 Seed API 或改写数据库，使用已缓存的原始问题向量进行只读重放。重放前后 `embedding_cache` 均为 216 条，没有产生新写入。

原始 Top-3 实际为：

| Rank | Chunk | 页码 | Hybrid | Semantic | TF-IDF | 类型 |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 98 | 3 | 0.570165 | 0.754316 | 0.017712 | 导航型句子 |
| 2 | 90 | 2 | 0.564817 | 0.741438 | 0.034955 | 贡献引导句 |
| 3 | 97 | 3 | 0.530426 | 0.697986 | 0.027743 | 功能贡献描述 |

与问题真正相关的块排名为：

| Rank | Chunk | 页码 | Hybrid | 含义 |
| ---: | ---: | ---: | ---: | --- |
| 4 | 153 | 8 | 0.526349 | Section IV 实验概述 |
| 8 | 211 | 15 | 0.507468 | Section V 完整结论 |
| 39 | 154 | 8 | 0.452897 | 实验总体设置 |
| 59 | 157 | 8 | 0.429598 | SE 训练/测试数据设置 |
| 63 | 158 | 9 | 0.426157 | 主观听感实验 |
| 68 | 160 | 9 | 0.419519 | MA 实验设置 |
| 73 | 159 | 9 | 0.417051 | 客观评价指标 |
| 120 | 162 | 9 | 0.380716 | BNC 评估设计 |

结论是：正文块不是没有进入候选集。131 个块都获得了正分，但目标正文被全局 Top-3 截断。

### 3.4 召回后的上下文裁剪事实

Follow-up 复用 Report 证据召回入口，每篇论文默认召回 3 个块。但 `ContextBuilder` 的 `fair_budget_v1` 默认每篇论文最多保留 2 个块。

因此：

```text
原始 Top-3：98、90、97
→ 每篇论文最多 2 块
→ 最终 Evidence：98、90
→ DeepSeek 看不到实验正文和 Conclusion
```

chunk 97 的丢弃原因被明确记录为 `paper_quota`，不是 Token Budget 不足。

## 4. 根因结论

根因是**多意图问题下的召回排序和上下文覆盖失配**，不是 PDF 解析、MySQL 持久化或 DeepSeek 生成故障。

### 4.1 一条 Query 同时包含两个独立意图

问题同时要求：

1. 实验是如何设置的；
2. Conclusion 讲了什么。

当前链路不会拆分子问题，只对整句话做一次全局排名，因此 Top-K 只保证“整体相似”，不保证每个子意图都有证据。

### 4.2 导航句同时命中两个意图的表面语义

chunk 98 同时出现：

- `experimental setup and results`；
- `concluding remarks`。

虽然它没有任何实验细节或结论事实，但对向量模型来说，它同时覆盖了两个语义主题，所以得分最高。

### 4.3 完整论文标题稀释了真正的区分信号

用户 Query 包含完整论文标题；MinerU 块的索引文本又会加入论文标题和 Section Path。几乎所有块都共享大量标题语义，使“这是那篇论文”的信号压过“这个块能否回答实验设置”的信号。

### 4.4 中英混合 Query 对词法召回不利

问题中“实验是如何设置的”是中文，论文正文是英文。TF-IDF 无法直接建立“实验设置”与 `experimental setup` 的词面对应，使排名更依赖 Seed 语义分数。

### 4.5 当前没有答案覆盖度门禁

现有质量门禁会检查 Evidence 字段、Token Budget、论文配额等结构正确性，但不会判断“实验设置”和“结论”两个问题是否都有实质证据。因此这组 Evidence 在结构上合法，但在语义上不完整。

### 4.6 模型的“证据不足”是正确降级

既然最终 Prompt 只包含 chunks 98 和 90，DeepSeek 如果自行回答实验数据和结论，反而会违反项目的证据安全边界。因此“证据不足”是一个合理的末端行为，真正需要修复的是上游 Retrieval。

## 5. 初始修复方向决策（历史冻结方案，部分已经实施）

> 本节保留 2026-08-28 当时的决策顺序，便于说明方案如何演进。Query Routing、PRF、Candidate Pool、CrossEncoder Reranker 和 MMR 的实际实现见第 16、18–21 节；Evidence Allocation、Intent Decomposition、Section/Parent/Neighbor Expansion 仍未实施。

### Phase 0：固化失败样例和可复现基线

将本次 CITISEN 问题作为固定回归样例，保留：

- 原始 Query；
- 基线 Top-3：`98, 90, 97`；
- 最终 Evidence：`98, 90`；
- 目标 Section IV 证据：`153/154/157–162`；
- 目标 Section V 证据：`211`；
- 基线回答：两个子意图均没有获得足以直接回答的 Evidence。

实验必须单变量进行。不应在同一次实验中同时改 Query、Candidate K、Reranker、Context 配额和 Prompt，否则无法判断是哪一层产生改善或回归。

### Phase 1：先修 Evidence Allocation 和 Candidate Recall

当前 Top-3 同时承担了“候选集”和“最终证据”两种角色，导致系统还未判断 Evidence 是否足以回答，正文就已被永久淘汰。第一阶段先重新区分：

```text
Candidate Pool：尽量保证目标正文有机会进入后续判断
最终 Evidence：在 Token Budget 内保留能够回答的证据
```

计划将约 6000 Token Evidence Budget 作为主要边界，而不是对所有请求统一使用“每篇论文最多 2 块”。配额应与当前问题的目标论文范围联动：

- 单篇细问：深度优先，将大部分 Evidence Budget 给目标论文；
- 两篇比较：优先保证双方都有可比证据；
- 多篇综述：继续启用跨论文公平配额。

候选集大小不在本文档中预先写死。`Top-20` 可作为首组实验参数，但最终 K 必须由召回命中、时延、Token 和 API 成本实验决定。

### Phase 2：解决复合问题的 Intent Coverage

“实验如何设置，Conclusion 讲了什么”不是一个单一证据需求，而是两个子意图：

1. Experimental Setup；
2. Conclusion。

计划将论文标题作为目标论文识别条件，而不是继续让完整标题主导内容相似度；同时对复合问题做受控拆分，例如：

1. `What is the experimental setup, including datasets, training/testing protocol and evaluation metrics?`
2. `What conclusions does the paper report?`

两路候选合并、去重后，需要检查每个子意图是否都有实质 Evidence，而不是仅检查“总共有几个块”。中文 Follow-up 仍保持原语言用于最终回答，英文展开只是待验证的 Retrieval Query 策略。

### Phase 3：再优化 Evidence Precision 和完整性

只有当 Candidate Pool 足够、Evidence Allocation 合理、Intent Coverage 可测量之后，再逐项验证：

- Section-aware 候选扩展或加权；
- Parent / Section / Neighbor Expansion；
- Small-to-Big Retrieval；
- 项目已有 DeepSeek Listwise Reranker。

这些方案的目标是把“找到章节”升级为“找到章节内足以回答的完整正文”。不建议只按 Section 名硬过滤，因为子节可能名为 `SE experiments`、`MA experiments` 或 `BNC experiments`，不再显式包含 `Section IV`。

Reranker 也不预设为必然改善。需要同时记录排名效果、时延、Token 成本和缓存命中，再决定是否进入生产链路。

### 不采用的快捷方案

#### 只增大 Top-K

将 Top-3 提高到 Top-10 可能让排名第 8 的 Conclusion 进入候选，但排名第 39 的实验总体设置仍可能缺失。因此增大 K 只是 Candidate Recall 的一部分，不能单独解决 Intent Coverage 和 Evidence Sufficiency。

#### 放宽 DeepSeek 的 Evidence 约束

允许模型在 Evidence 不足时使用自身知识，可能产生看似完整的回答，但会破坏论文事实可追溯性、Citation Correctness、Page Grounding 和幻觉控制。本案例的修复目标是“让 Retriever 提供正确 Evidence”，而不是绕过 Evidence。

### 必须保持的安全边界

- 模型只能使用最终 Evidence；
- 引用标题和物理页码必须通过 Citation Validation；
- Citation Normalization 只统一展示格式；
- 无证据时仍应拒绝猜测；
- Follow-up Streaming 和 Report 现有行为不能被一个局部 Retrieval 修复意外重构。

## 6. 实验设计

### 6.1 单变量实验矩阵

| 实验 | 阶段 | 唯一主变量 | 要回答的问题 | 状态 |
| --- | --- | --- | --- | --- |
| Baseline | Phase 0 | 现有 Hybrid Top-3 + 每篇 2 块 | 固化当前失败结果 | 已完成 |
| E1 | Phase 1 | 将 Hybrid 候选与 Final Evidence 解耦，建立 Top-20 Candidate Pool | 较后正文是否获得精排和多样性选择机会 | 已生产接入；专项契约通过，冻结质量 Benchmark 待跑 |
| E2 | Phase 1 | 仅按目标论文数量动态分配 Evidence Budget | 单篇细问是否仍被每篇 2 块浪费预算 | 计划/待验证 |
| E3 | Phase 2 | 仅从 Retrieval Query 中移除完整论文标题 | 标题是否稀释了具体意图 | 计划/待验证 |
| E4 | Phase 2 | 仅将复合问题拆成实验/结论两个 Query | 两个意图是否都获得正文 Evidence | 计划/待验证 |
| E5 | Phase 2 | 仅按子意图保留 Evidence | Context Selection 能否稳定覆盖两个问题 | 计划/待验证 |
| E6 | Phase 3 | 仅加 Section-aware 候选扩展/加权 | 目标章节与子节能否进入前列 | 计划/待验证 |
| E7 | Phase 3 | 仅加 Parent / Section / Neighbor Expansion | 能否从命中导航线索扩展到完整正文 | 计划/待验证 |
| E8 | Phase 3 | 在完整 Candidate Pool 上使用 `BAAI/bge-reranker-v2-m3` CrossEncoder | 精排能否区分“提到主题”和“提供答案” | 已生产接入；真实三块 Smoke 通过，冻结质量 Benchmark 待跑 |
| E8.5 | Phase 3 | 在完整 reranked Pool 上使用 MMR | Final Top-K 能否减少近重复证据并保留不同信息面 | 已生产接入；专项契约通过，冻结质量 Benchmark 待跑 |
| E9 | Final | 组合前述已证明有效的最小改动 | 端到端回答、引用、时延和成本是否同时达标 | 计划/待验证 |

### 6.2 评价指标

| 指标 | 口径 |
| --- | --- |
| Intent Coverage@K | 实验设置与结论两个意图中，有多少在最终 Evidence 中得到实质覆盖 |
| Evidence Hit@K | 标注的目标正文块是否进入 Top-K |
| MRR | 第一个可回答正文块的倒数排名 |
| Navigation Contamination | 最终 Evidence 中只描述章节安排、不提供事实的块占比 |
| Answer Completeness | 实验设置和结论是否均被回答 |
| Citation Validity | 标题和物理页码是否全部来自当前 Evidence |
| Evidence Budget Utilization | 最终有效 Evidence 占可用 Token Budget 的比例，以及是否因固定配额留出大量空闲预算 |
| Retrieval Latency | 拆分 Query 和 Reranker 带来的时延增量 |
| Model Cost | Query expansion / rerank 新增的 Token 与 API 调用成本 |

### 6.3 最小验收标准

1. 最终 Evidence 至少包含一个可直接回答实验设置的 Section IV 正文块。
2. 最终 Evidence 包含 chunk 211 或其等价的 Section V 结论证据。
3. 回答不再只复述“Section IV 讲实验、Section V 讲结论”。
4. 引用使用物理页码 8/9/15，并通过现有 Citation Validation / Normalization。
5. Follow-up Streaming、Report、ContextBuilder 现有安全边界不被破坏。
6. 在更大的固定问题集上验证，不只针对 CITISEN 写特例。

## 7. 当前修复结果与尚未取得的证据

### 已完成的生产链路

```text
中文 Original Query → Semantic
英文 Lexical Query → TF-IDF
当前论文 Semantic Top-3 → PRF 作者术语 → Enhanced TF-IDF
0.75 Semantic + 0.25 TF-IDF → Parent Dedup → Candidate Top-20
→ BGE CrossEncoder Reranker → MMR（λ=0.5）→ Final Top-K → Evidence
```

- Final Top-K 没有被机械扩大；Candidate Pool 默认 20，只负责让更多正文有机会进入后续判断。
- CrossEncoder 使用 `BAAI/bge-reranker-v2-m3` 对原始中文问题与候选原文成对打分，不调用 DeepSeek。
- MMR 使用 rerank score 作为 relevance、已有 chunk dense embedding 的余弦相似度作为 redundancy；不生成新 embedding。
- Query Routing、PRF、Reranker 和 MMR 都有独立开关及失败回退；任何增强失败都不应让 Follow-up 整体失败。
- Internal Candidate、Reranker、MMR 分数和向量会在进入最终 Evidence 前剥离，不能成为回答事实或 Citation。

### 已有质量证据

- Query Routing 冻结 A/B：Recall@5 `0.4083 → 0.5361`，Recall@20 `0.6944 → 0.7361`，平均目标排名 `17.00 → 15.05`，中位排名 `10.5 → 7.5`。
- 真实 CrossEncoder 三块 Smoke：评价指标正文块排第 1，模型可加载并返回 3 个有限分数；CPU 冷启动约 `217.011s`，同进程热推理约 `0.568s`。该 Smoke 只证明模型接线和基本语义判断，不代表冻结集 Recall 已提升。
- MMR 专项构造用例证明：rerank rank 1 始终保留，近重复块会让位给正交信息块，完整候选池中的 rerank rank 8 可以进入 Final Top-K。

### 仍然缺少的证据

- PRF、Candidate Pool、CrossEncoder 和 MMR 尚未在相同的 3 篇论文、12 个问题、22 个 Target Chunks 上做逐层开关 A/B。
- 原 CITISEN 问题的最终 Evidence 是否稳定包含实验设置和 Conclusion，尚未以相同输入做正式端到端复验。
- Intent Coverage@K、Navigation Contamination 和完整链路的冷/热 P95 时延尚未测量。
- 本阶段按约束只跑专项与邻接回归，没有运行 canonical pytest，也没有据此修改 ContextBuilder、Prompt、Citation、Report 或 Streaming 架构。

## 8. 面试表达

### 8.1 90 秒讲法（当前可如实使用）

> 我在 Paper Hunter 的 Follow-up RAG 中遇到过一个问题：论文正文已经解析并写入 MySQL，但系统只召回“实验在第四节、结论在第五节”的导航句，所以模型只能按证据边界回答“证据不足”。我先沿解析、索引、检索、上下文裁剪逐层核验，确认不是 PDF、数据库或模型幻觉问题；然后冻结 3 篇论文、12 个真实问题和 22 个 Target Chunks 做单变量实验。RRF 在正确处理 TF-IDF 零命中后没有收益，真正的第一层根因是中文问题对英文正文的词法检索失效。于是我按 Retriever 特性做 Query Routing：中文保留给跨语言 Semantic，英文检索表达只给 TF-IDF，使 Recall@5 从 0.4083 提升到 0.5361，中位目标排名从 10.5 改善到 7.5。生产上我没有停在“翻译 Query”，而是继续把证据选择拆成四层：Semantic-guided PRF 从当前论文正文补作者术语；Top-20 Candidate Pool 把粗召回和 Final Top-K 解耦；本地 BGE CrossEncoder 对完整候选池精排；MMR 再用已有 dense embedding 去除近重复证据。每层都有开关、诊断和失败回退，内部 Query、反馈词、分数与向量都不能进入 Evidence。这里我会明确区分证据强度：Query Routing 有冻结 A/B 的 Recall 数据，CrossEncoder 有真实模型 Smoke，其余层目前只有工程专项回归，尚不能宣称原 CITISEN Case 已被端到端证明修复。

### 8.2 当前 STAR 表达

**Situation：**
Follow-up 问题同时要求实验设置和 Conclusion，但系统只召回了章节导航句，最终返回证据不足。

**Task：**
在不破坏 Citation Validation、真实 Streaming 和无证据拒答边界的前提下，提高复合问题的实质证据覆盖。

**Action：**
先通过数据库正文与缓存向量重放定位为 Retrieval/Context 问题；再冻结 3 篇论文、12 个问题和 22 个 Target Chunks，依次验证 Weighted 与 RRF、中文与全英文 Query、中文 Semantic 与英文 TF-IDF 分路由。确认 Query Routing 有收益后，将生产检索继续分层：Semantic-guided PRF 解决作者术语错配，Top-20 Candidate Pool 解耦粗召回与最终证据，BGE CrossEncoder 区分“提到主题”与“真正回答问题”，MMR 在精排后减少重复证据。各层都使用 Feature Flag、诊断、稳定排序和 fallback，并保持 Original Paper Chunks 是唯一论文事实 Evidence。

**Result：**
Query Routing 将 Target 的英文 TF-IDF 非零覆盖从 3/22 提高到 21/22；Recall@5 从 0.4083 到 0.5361，平均目标排名从 17.00 到 15.05，中位排名从 10.5 到 7.5。后续 Evidence Selection Stack 已接入生产并通过专项与邻接回归；真实 BGE Smoke 能把包含 `classification error rates / test error` 的正文排到三个候选的第 1。当前结论是架构和降级路径已经成立，但 PRF、Candidate Pool、Reranker、MMR 的冻结 Recall 增量仍待验证，不能把工程测试包装成最终质量提升。

### 8.3 面试官可能追问

1. 为什么不直接把 Top-K 从 3 调到 20？
2. 为什么不直接用 Section 名做硬过滤？
3. 如何判断是召回问题而不是向量模型能力问题？
4. 多意图 Query 的 Evidence Coverage 怎么量化？
5. 增加 Reranker 后怎么控制延迟和 Token 成本？
6. 为什么还要保留“证据不足”回答？
7. 如何避免只针对这一篇论文过拟合？
8. 如何保证修复 Retrieval 不改变 Citation 事实校验边界？
9. 为什么不把整条中文 Query 直接翻译成英文？
10. 为什么 Query Translation 只给 TF-IDF，不给 Semantic？
11. 这是不是某篇论文里已有的标准算法？
12. 12 个问题是否足以决定生产上线？
13. Candidate Pool 为什么是 20，而 Final Top-K 为什么不一起增大？
14. CrossEncoder 为什么放在粗召回之后，不能直接扫描 131 个 chunks？
15. 为什么 Reranker 用原始中文问题，而不是英文 Lexical Query？
16. MMR 为什么必须作用于完整 reranked Pool，而不是只对 Reranker Top-K 去重？
17. MMR 的 relevance 和 redundancy 分别来自哪里，是否增加模型调用？
18. 如何证明 internal query、反馈词、分数和向量没有污染 Evidence 与 Citation？

## 9. 可用于项目素材的候选表达

> 以下是当前可用于面试素材库的版本。Query Routing 与后续 Evidence Selection Stack 已接入生产，但只有 Query Routing 有冻结质量 A/B；不得把专项契约测试说成完整 Recall 提升。

本案例值得在面试中展开的不是“调了一个 Top-K 参数”，而是以下工程判断：

- 把长链路问题分解为解析、持久化、索引、候选召回、上下文选择和生成安全多个可验证边界；
- 用持久 Evidence 和已缓存 Query Embedding 复现当时结果，避免调试过程改变数据或引入新的模型波动；
- 区分“语义相关”与“证据充分”，将问题从单一排名参数上升为 Candidate Recall、Intent Coverage、Evidence Allocation 和 Context Selection 的管线设计；
- 用 Candidate Pool 将“召回多少候选”与“最终给模型多少证据”解耦，再分别用 CrossEncoder 解决 relevance、用 MMR 解决 redundancy，而不是让一个 Hybrid Score 同时承担所有职责；
- 为每层增加 Feature Flag、确定性 tie-break、Diagnostics 和 fallback，使高级检索能力失败时仍能退回已验证的上一层；
- 没有用放宽 Prompt 和幻觉约束换取“看起来完整”的回答。
- 没有把外部论文当作结论，而是用外部研究建立假设，再用项目内 Ground Truth 决定采用或放弃。

> 围绕 Follow-up RAG 中“正文已解析但证据不足”问题，通过 MySQL 分块核验、持久 Evidence 追踪和缓存 Query Embedding 排名重放定位根因；基于 3 篇论文、12 个真实问题和 22 个 Target Chunks 完成跨语言 Query Routing A/B，并将生产检索演进为 Query Routing、Semantic-guided PRF、Top-20 Candidate Pool、BGE CrossEncoder Reranker 与 MMR 分层证据选择链路。全程保留 Feature Flag、失败回退、Original Paper Evidence 与 Citation 边界，并明确区分已量化的 Recall 收益和仅通过工程专项验证的后续层。

## 10. 代码与运行证据

| 证据 | 证明内容 | 状态 |
| --- | --- | --- |
| `my_agent_project/research_conversation_service.py::prepare_research_followup_context` | Follow-up 按已选论文调用报告证据召回入口 | code-verifiable |
| `my_agent_project/research_report_service.py::REPORT_EVIDENCE_PER_PAPER` | 每篇默认 Top-K 为 3 | code-verifiable |
| `my_agent_project/research_tools.py::retrieve_paper_evidence` | 调用 `retrieve_relevant_chunks()` 并返回带页码证据 | code-verifiable |
| `my_agent_project/rag_service.py::retrieve_relevant_chunks` | Query Routing、PRF、Hybrid、Parent Dedup、Candidate Pool、Reranker、MMR 与 Final Evidence 的生产编排 | code-verifiable |
| `my_agent_project/reranker_service.py::rerank_candidates` | BGE CrossEncoder 延迟加载、批量打分、稳定排序和模型单例缓存 | code-verifiable |
| `my_agent_project/mmr_service.py::select_mmr_candidates` | rerank relevance 归一化、dense cosine redundancy、稳定 MMR 选择 | code-verifiable |
| `tests/test_candidate_pool.py` / `tests/test_candidate_reranker.py` / `tests/test_mmr_selection.py` | 粗召回与最终证据解耦、真实精排接线、MMR 去冗余、fallback 和 Evidence 清理契约 | test-verified |
| `my_agent_project/rag_service.py::chunk_index_text` | MinerU 块索引时加入 Section Path 和 Content Type | code-verifiable |
| `my_agent_project/context_service.py::ContextConfig` | 每篇论文最多保留 2 个块 | code-verifiable |
| MySQL `paper_documents` / `paper_chunks` | 18 页、131 块，Section IV/V 正文确实存在 | runtime-verified |
| MySQL `research_messages.evidence_json` | 最终只向模型提供 chunks 98 和 90 | runtime-verified |
| 已缓存 Query Embedding 只读重放 | 原始 Top-3 为 98/90/97，目标正文排名较后，且缓存数未变 | runtime-verified |

## 11. 当前验证状态与表达风险

- **已验证：** PDF 已解析、Section IV/V 已入库、向量已索引，原始失败发生在召回排序和上下文覆盖层；排名重放没有新增 embedding cache。
- **已验证：** Query Routing 的冻结 A/B 四档 Recall、平均排名和中位排名均改善，并已通过开关、缓存、改写失败回退和 Evidence Safety 测试后接入 Follow-up。
- **已验证：** PRF、Candidate Pool、CrossEncoder 和 MMR 已接入生产链路；专项测试覆盖算法契约、稳定排序、降级和内部字段清理。真实 BGE 三块 Smoke 通过。
- **未完成：** PRF、Candidate Pool、CrossEncoder 和 MMR 尚未用冻结 Target Chunks 做逐层 A/B；没有原 CITISEN 失败问题的正式修复后 Top-K、Intent Coverage 或完整 E2E 结论。
- **仍未实施：** Query/Intent Decomposition、Dynamic Evidence Allocation、Section/Parent/Neighbor Expansion、Small-to-Big 与 ContextBuilder 配额重构。
- **表达边界：** 可以说“完成了分层生产接入和安全回归”；只有 Query Routing 可以引用 Recall 增益。其余层只能说“具备改善机会且通过工程验证”，不能声称已提高 Recall 或彻底解决证据不足。
- **下一项最高价值实验：** 在相同冻结集上依次关闭/开启 PRF、Candidate Pool+Reranker、MMR，逐层记录 Target Rank、Recall@K、冗余率和时延，避免一次比较整个组合后无法归因。

## 12. 冻结说明与后续更新规则

### 12.1 本次冻结内容

`v1.0-frozen` 冻结以下事实和决策：

- 本次真实问题、论文、原始 Query 和失败回答的语义；
- MySQL 中 18 页、131 块及 Section IV/V 正文存在的运行事实；
- 原始 Top-3、目标正文排名和最终 Evidence 的可复现基线；
- 根因属于 Retrieval Ranking、Candidate Recall、Evidence Allocation 和 Intent Coverage；
- 修复顺序为：先 Candidate Recall/Evidence Allocation，再 Intent Coverage，最后 Evidence Precision；
- Citation Validation、Citation Normalization 和无证据拒答安全边界必须保留。

### 12.2 本次未冻结为最终结论的内容

以下内容仍然是待验证变量，不应被当作既定实现：

- Candidate Pool 生产默认值已经是 20，但它是否为质量、延迟和内存之间的最终最优值仍待 Benchmark；
- 是否移除或弱化论文标题索引信号；
- Query Decomposition 的实现方式；
- 是否采用 Section/Parent/Neighbor Expansion；
- BGE CrossEncoder 已接入 Follow-up；旧 DeepSeek Listwise 是否继续保留为实验对照、是否值得使用仍未形成新结论；
- Evidence Allocation 的最终 Token/块数策略；
- 任何质量、时延、成本或成功率提升数字。

### 12.3 解冻条件

当真正启动 RAG 修复时再解冻本文档，并且只按可核证证据追加：

1. 生产代码的实际改动与设计权衡；
2. 每组单变量实验的真实运行结果；
3. 最终采用/放弃哪些方案及原因；
4. 专项测试、canonical pytest、Streaming/Report/Citation 回归结果；
5. 修复完成后的 STAR `Result` 和可投递表达。

解冻时不覆盖本次基线；应保留 `v1.0-frozen` 事实作为 Before，并在修复章节追加 After 和实验对比。

### 12.4 版本记录

| 版本 | 日期 | 状态 | 内容 |
| --- | --- | --- | --- |
| `v1.0-frozen` | 2026-08-28 | 冻结 | 完成问题背景、证据基线、根因、分阶段修复路线、实验矩阵和面试表达骨架 |
| `v1.1-experiment-1` | 2026-08-29 | 追加实验 | 完成 3 篇真实论文、12 个问题的 Weighted 与 RRF 单变量对照；确认朴素 RRF 不适合直接替换当前融合方式 |
| `v1.2-experiment-2` | 2026-08-29 | 修正实验语义 | 零 TF-IDF 分数不再获得关键词排名；修正后 RRF 消除文档顺序噪声，但与 Weighted 的 Recall 完全持平 |
| `v1.3-experiment-3` | 2026-08-29 | 追加跨语言实验 | 冻结 12 条英文 Retrieval Query；Target 关键词命中由 3/22 增至 21/22，但直接替换中文 Query 的总体收益不稳定 |
| `v1.4-experiment-4` | 2026-08-29 | 追加 Query Routing 实验 | 中文 Query 保留给 Semantic、英文 Query 只路由到 TF-IDF；四档 Recall 与平均排名同时改善，且保住原有强语义案例 |
| `v1.5-interview-evidence` | 2026-08-29 | 面试证据强化 | 核验跨语言 Dense、Query Translation、CLIR Query Expansion 与自适应混合研究；更新可直接复述的 STAR、证据边界和反向追问 |
| `v1.6-query-routing-production` | 2026-08-29 | 生产接入 | 将中文 Semantic + 英文 TF-IDF 接入 Follow-up；增加开关、改写缓存、失败回退和诊断信息，保留 Original Paper Evidence 边界 |
| `v1.7-semantic-prf` | 2026-08-29 | 候选增强已实现 | 使用每篇论文 Semantic Top-3 正文提取 Top-10 TF-IDF 反馈词，以 0.5/0.5 向量插值增强词法检索；专项测试通过，尚未运行冻结 Benchmark |
| `v1.8-candidate-pool` | 2026-08-29 | 粗召回层已实现 | Parent Dedup 后保留 Hybrid Top-20 候选，将 Candidate Recall 与 Final Top-K 解耦；不开启后续精排时保持旧结果 |
| `v1.9-bge-crossencoder` | 2026-08-29 | 精排层已实现 | 使用 `BAAI/bge-reranker-v2-m3` 对完整 Candidate Pool 批量精排；延迟加载、进程单例、失败回退和真实 CPU 三块 Smoke 均通过 |
| `v2.0-evidence-selection-stack` | 2026-08-29 | 多样性层已实现 | 在完整 reranked Pool 上使用 λ=0.5 的 MMR 选择 Final Top-K；复用已有 dense vectors、不新增模型调用，专项与邻接回归通过 |

## 13. 第一轮真实修复实验：为什么没有直接采用 RRF

### 13.1 这轮实验要回答什么

第一轮没有立刻扩大 Candidate Pool、拆分 Query 或引入 Reranker，而是先验证一个更小的问题：当前 `0.75 × Semantic + 0.25 × TF-IDF` 的原始分数加权，是否可以被两个独立排行榜的 Reciprocal Rank Fusion（RRF）安全替换。

实验严格保持同一批 MySQL `paper_chunks`、同一 Seed Embedding、同一 TF-IDF 和同一问题，只改变最后的融合公式。RRF 使用从 1 开始的两张完整排名表，`k=60`：

```text
RRF(chunk) = 1 / (60 + semantic_rank)
           + 1 / (60 + tfidf_rank)
```

为避免“用当前检索结果定义正确答案”的循环论证，我先阅读实际 Chunk 正文，冻结了 3 篇论文、12 个问题、22 个目标 Chunk。11 个问题能与数据库中的原始用户消息逐字对应；数据库未保存的 1 个 ICT 细粒度问题只使用本轮需求中冻结的完整原文，并明确标注来源，没有用近似问题冒充历史原文。

### 13.2 关键结果

全部 12 个问题按问题宏平均后，结果如下：

| 指标 | Weighted | RRF |
| --- | ---: | ---: |
| Recall@3 | 0.3500 | 0.0000 |
| Recall@5 | 0.4083 | 0.0833 |
| Recall@10 | 0.5639 | 0.2500 |
| Recall@20 | 0.6944 | 0.6278 |
| 目标 Chunk 平均排名 | 17.00 | 23.77 |
| 目标 Chunk 中位排名 | 10.50 | 17.50 |

RRF 只在少数目标上改善排名，例如 HiDiff 推理过程 Chunk 692 从第 23 位升到第 15 位，Soil 数据 Chunk 775/776 从第 22/21 位升到第 17/18 位。但它同时造成更严重的退化：ICT Conclusion Chunk 596 从第 1 位跌到第 30 位；ICT 五个实验结果 Chunk 普遍后退 12 至 17 位；HiDiff 数据集和指标 Chunk 也分别从第 2/1 位跌到第 17/8 位。

因此这轮结果不是“RRF 小幅无效”，而是说明在当前跨语言检索条件下，朴素等权 RRF 会破坏原本有效的 Semantic 排名。

### 13.3 根因不是 RRF 公式写错，而是输入排行榜质量不对等

12 个问题是中文，论文 Chunk 主要是英文。22 个目标 Chunk 中，除包含英文词 `HiDiff` 的方法问题外，TF-IDF 目标分数几乎全部为 `0`。在这种情况下：

- Weighted 会把 `0.25 × 0` 当作“关键词通道没有提供证据”，最终主要服从 Semantic Score；
- 朴素 RRF 不知道 TF-IDF 分数为零，只看到每个 Chunk 都有一个名次；
- 大量并列零分 Chunk 的稳定顺序实际接近文档顺序，却被 RRF 当成与 Semantic 排名同等可信的信号；
- 越靠论文后部的实验结论和 Conclusion 越容易被这个伪排名惩罚。

这说明 Rank Fusion 的前提不是“有两张榜就能融合”，而是每张榜都必须包含可辨别、与 Query 有关的排序信号。把无信息的完整排名表等权加入，反而会引入确定性的噪声。

### 13.4 工程决策

本轮选择保留现有 Weighted 生产策略，不把 RRF 设为默认，也不因为局部样例提升就宣称 Recall 已改善。实验脚本与 Ground Truth 独立保存，用于后续继续做单变量对照。

下一步真正值得优先验证的是查询侧的跨语言与多意图召回能力，例如受控 Query Translation/Expansion 或 Intent Decomposition。只有 TF-IDF 通道能对英文正文形成有效区分后，才值得重新评估 RRF、加权 RRF或分数归一化融合。本轮没有实施这些方案。

### 13.5 面试复述版

> 我遇到过一个很典型的 RAG 问题：论文正文已经解析并入库，但模型只检索到“实验在第四节、结论在第五节”的导航句，所以按证据边界拒绝回答。我没有先调 Prompt，而是把问题拆成解析、索引、召回、上下文截断四层，并在确认正文真实存在后做了 3 篇论文、12 个问题、22 个目标 Chunk 的单变量排序实验。我测试用 RRF 替换 0.75/0.25 分数融合，但结果 Recall@5 从 0.4083 降到 0.0833。进一步检查发现，中文问题对英文 Chunk 的 TF-IDF 几乎全为零，RRF 却把零分并列产生的文档顺序当成有效排名，导致 Conclusion 从第 1 位跌到第 30 位。最终我没有为了“用了新算法”而上线 RRF，而是保留原策略，并把下一步收敛到跨语言 Query 和多意图召回。这件事让我形成了一个工程原则：融合算法是否有效，取决于各路信号的质量，不能只看算法名称。

完整目标 Chunk、分数、排名和只读数据库校验见本轮独立实验产物；该追加结论不覆盖第 12 节冻结的原始问题基线。

## 14. 第二轮实验：零分不应被解释为关键词排名

第一轮朴素 RRF 暴露了一个重要的实验语义问题：TF-IDF `score=0` 代表关键词检索没有命中，而不是“命中了，只是排得靠后”。如果仍把所有零分 Chunk 按文档顺序编号，就会人为制造一张没有检索意义的关键词榜。

第二轮完全复用同一批 3 篇论文、12 个问题和 22 个 Target Chunks，只修正这一条规则：

```text
tfidf_score > 0  → 进入关键词排行榜并贡献 RRF 分数
tfidf_score = 0  → tfidf_rank = null，关键词 RRF 贡献为 0
```

修正后，第一轮的严重退化全部消失：ICT Conclusion 恢复为第 1 位，Results、Dataset 和 Metric 等零关键词命中案例都回到 Semantic 主导的原排名。但新的总体结果也很明确：

| 指标 | Weighted | 修正后 RRF |
| --- | ---: | ---: |
| Recall@3 | 0.3500 | 0.3500 |
| Recall@5 | 0.4083 | 0.4083 |
| Recall@10 | 0.5639 | 0.5639 |
| Recall@20 | 0.6944 | 0.6944 |
| 目标 Chunk 平均排名 | 17.0000 | 17.0455 |
| 目标 Chunk 中位排名 | 10.5 | 10.5 |

只有包含英文词 `HiDiff` 的方法问题产生了非零 TF-IDF 信号，三个目标 Chunk 的排名分别为 `16→27`、`130→128`、`23→15`。局部有升有降，但没有改变任何 Recall@K；总体平均排名还略差 `0.0455`。

这轮实验把结论进一步收紧：正确语义下的 RRF 不再伤害大多数零关键词命中案例，但也没有提供可测量的整体收益。当前限制仍然是中文 Query 对英文正文的关键词召回几乎失效，以及部分细粒度正确正文在 Semantic 榜上也不够靠前。因此生产环境继续保留 Weighted，下一步优先研究 Query Recall，而不是继续调 RRF 常数。

## 15. 第三轮实验：英文 Retrieval Query 找到了什么，又丢掉了什么

第三轮不再改 Fusion，而是验证跨语言 Query Recall。用户原始中文问题保持不变，另外冻结一条只供检索使用的英文表达；Seed Embedding、TF-IDF、Weighted `0.75/0.25`、Chunks 和 22 个 Ground Truth 全部保持一致。

最强的正面证据是关键词通道从几乎失效变为可用：Target Chunk 的 `TF-IDF > 0` 从 `3/22` 提升到 `21/22`。ICT 一致性训练从第 23 位升到第 14 位；HiDiff 三个方法正文从 `16/130/23` 提升到 `1/48/5`；Soil 数据块之一从第 21 位升到第 3 位。这证明跨语言词面不一致确实是 Recall 根因之一。

但直接把中文 Query 替换为单条英文 Query 并不稳定：

| 指标 | 中文原 Query | 英文 Retrieval Query |
| --- | ---: | ---: |
| Recall@3 | 0.3500 | 0.3194 |
| Recall@5 | 0.4083 | 0.4306 |
| Recall@10 | 0.5639 | 0.5417 |
| Recall@20 | 0.6944 | 0.7722 |
| 目标 Chunk 平均排名 | 17.00 | 20.32 |
| 目标 Chunk 中位排名 | 10.5 | 13.5 |

HiDiff 和 Soil 整体改善，但 ICT Results 的多个目标从 `12/8/3/4` 退化到 `60/19/17/85`，Conclusion 从第 1 位跌到第 57 位。ICT 指标目标仍然 `TF-IDF=0`，排名从 40 变为 41，因为正文实际使用的是 `error rates`，而直译 Query 使用 `evaluation metrics`，两者仍没有词面重合。

因此实验支持两个同时成立的结论：

1. Query Language Alignment 能显著激活英文正文的关键词召回，属于真实根因；
2. 单条英文改写不能安全替代原始中文 Query，因为翻译表达可能过泛或丢失原问题在跨语言 Embedding 中已有的有效语义信号。

工程上不应直接上线“中文改英文后只检索一次”。更合理的下一步是保留原 Query，并对原始与英文检索结果做受控的多 Query 合并，或为 `evaluation metrics → error rates` 这类论文域表达增加受控扩展；本轮没有实施。

## 16. 第四轮实验：让不同 Query 服务于各自擅长的 Retriever

第三轮证明英文表达能激活 TF-IDF，却会破坏部分已经有效的中文跨语言 Semantic。第四轮因此不再做 Query 替换，而是做最小职责路由：中文原问题继续生成 Semantic Score，冻结英文表达只生成 TF-IDF Score，最终仍用原生产权重 `0.75/0.25` 相加。

实验额外强制 A/B 共享同一个中文 Query Vector，并逐 Target 校验 Semantic Score 和 Semantic Rank 完全相同。首次运行正是因为一个未缓存 Query 被 Seed 独立生成两次、Semantic 出现变化而被不变量校验主动终止；修正为实验内共享向量后才获得正式结果。

| 指标 | 当前生产路由 | 中文 Semantic + 英文 TF-IDF |
| --- | ---: | ---: |
| Recall@3 | 0.3500 | 0.3917 |
| Recall@5 | 0.4083 | 0.5361 |
| Recall@10 | 0.5639 | 0.6083 |
| Recall@20 | 0.6944 | 0.7361 |
| 目标 Chunk 平均排名 | 17.00 | 15.05 |
| 目标 Chunk 中位排名 | 10.5 | 7.5 |

22 个 Target 中有 18 个从中文 TF-IDF 零分变为英文非零，最终 11 个排名改善、7 个不变、4 个退化。ICT Conclusion 保持第 1，说明中文 Semantic 的强案例被保住；HiDiff 方法块从 `16/130/23` 变为 `5/109/22`，证明其中一部分收益确实来自英文 TF-IDF，而不仅是第三轮的英文 Semantic。

同时，Query Routing 不是万能修复：ICT 一致性训练从 23 退到 27，ICT 评价指标从 40 退到 49。后者再次说明 `evaluation metrics` 无法词面匹配 `error rates`，且其他英文词面命中的非目标块会挤压正确块。因此下一阶段仍需解决受控领域表达扩展，而不能把“TF-IDF 被激活”简单等同于“正确证据一定前移”。

这轮已经形成足够一致的离线证据进入带开关的生产实现验证：四档 Recall 全部提升、平均和中位排名同时改善、三篇论文等权指标也全部改善，且没有重现全英文 Query 对 Conclusion 的严重伤害。这里的“进入生产实现”不等于立即无条件发布；当时仍需定义英文 Query 的生成、失败降级、缓存和更大回归集。前三项随后已经在生产 Query Routing 中完成，更大回归集仍是当前边界。

## 17. 外部研究依据与面试边界

### 17.1 哪些外部资料真正有用

| 资料 | 可以支撑什么 | 不能据此声称什么 | 证据强度 |
| --- | --- | --- | --- |
| [mContriever 官方仓库](https://github.com/facebookresearch/contriever) | 多语言 Dense Retriever 可以用非英文 Query 检索英文 Wikipedia；其 MKQA 跨语言设置明确包含简体中文等语言 | 不能证明 Paper Hunter 的 Seed Embedding 必然同样有效；后者仍由本项目实验验证 | High |
| [Anserini 官方 NeuCLIR 回归](https://github.com/castorini/anserini) 与 [TREC 2022 NeuCLIR](https://arxiv.org/abs/2304.12367) | BM25 等稀疏检索需要通过 Query Translation 或 Document Translation 跨越语言边界，这两条路线都有可复现实验 | 不能证明“中文 Dense + 英文 Sparse”是 Anserini 已定义的标准算法 | High |
| [Alan Turing Institute CLIR-QE](https://github.com/alan-turing-institute/clir-qe) | 跨语言 Query Expansion 可加入相关词或伪文档，并用 BM25 检索；能解释为什么只翻译 `evaluation metrics` 仍匹配不到 `error rates` | 这是下一阶段候选方向，不是 Paper Hunter 当前已实现功能 | Medium |
| [QuDAR（ACL 2026）](https://aclanthology.org/2026.acl-long.1791/) | 原始/扩展 Query 与 Sparse/Dense Retriever 的贡献具有互补性，固定全局比例可能忽略 Query 差异 | Paper Hunter 没有实现 QuDAR 的 Query-wise 自适应权重，也不能借用其论文结果作为本项目指标 | High |
| [English-Spanish CLIR 示例](https://github.com/amiekong/cross-lingual-retrieval) | 直观展示 Query Translation 后用 TF-IDF/Lucene 检索，以及 Query Translation 相比翻译整个语料的工程成本优势 | 这是小型个人实现，只能帮助理解，不能作为“业界成熟方案”的核心依据 | Low |

### 17.2 最准确的技术定位

Paper Hunter 当前验证的方案不是某个仓库中同名的标准算法，而是把三条成熟原则按 Retriever 特性组合：

```text
Cross-lingual Dense Retrieval
→ 中文原 Query 保留给多语言 Semantic

Query Translation for Lexical Retrieval
→ 英文 Retrieval Query 路由给 TF-IDF

Hybrid Retrieval
→ 保留原 0.75 / 0.25 Weighted Fusion
```

外部研究提供的是设计先验，不是项目成果。真正决定工程选择的是 Paper Hunter 自己的四轮受控实验：朴素 RRF 失败、正确零命中语义下 RRF 持平、全英文 Query 有明显正负迁移、分路由方案才同时保住中文 Semantic 强项并激活英文词法通道。

### 17.3 Earned Secret

> **混合检索不只是把两个分数相加；Query 本身也要按 Retriever 的归纳偏置进行适配。** Dense Retriever 可以利用跨语言语义空间，Sparse Retriever 依赖词面重合；强迫两者共享同一种语言表达，会让其中一路天然失效。更重要的是，激活一条检索通道不代表正确证据一定前移，仍必须用冻结 Ground Truth 检查正负迁移。

证据是：中文 Query 下目标 TF-IDF 只有 3/22 非零；全英文 Query 虽达到 21/22，却让 ICT Conclusion 从第 1 跌到第 57；只有中文 Semantic + 英文 TF-IDF 的 Routing 才让四档 Recall 同时提高并保住 Conclusion。

### 17.4 面试时能说与不能说

可以说：

- “这是典型的 Cross-Lingual Information Retrieval 问题。”
- “我的设计分别借鉴了跨语言 Dense Retrieval 和 Lexical Query Translation 两类成熟路线。”
- “我没有照抄某个 RAG 模板，而是针对中文用户、英文论文的数据分布做 Retriever-specific Query Routing，并用 3 篇论文、12 个问题、22 个 Target Chunks 验证。”
- “ACL 2026 的 QuDAR 也说明 Query Format 与 Retriever Type 的贡献具有交互和 Query-specific 特征；这与我的实验观察一致，但我没有实现它的自适应算法。”

不能说：

- “这是 mContriever、Anserini 或 QuDAR 提出的 Paper Hunter 算法。”
- “GitHub 上已经有完全相同的 `Chinese Dense + English TF-IDF` 标准方案。”
- “Query Routing 的小规模离线收益已经证明能推广到大规模数据”或“PRF 已经提高了生产 Recall”。前者超出 3 篇/12 题证据规模，后者尚未运行冻结 Benchmark。
- “21/22 关键词非零就代表 21 个正确块排名都改善”；真实结果是 11 个改善、7 个不变、4 个下降。

### 17.5 Level-5 反向审查

- **假设审计：** 故事成立依赖当前 Seed Embedding 具备可用的中英跨语言能力，也依赖正式论文语料主要为英文；若语料变成多语言，路由策略必须先识别文档语言。
- **盲点：** 英文 Query 的生产生成、缓存和失败降级已经实现，但 PRF 目前只有算法契约与邻接回归，没有冻结 Benchmark 的增量指标；这是面试官最容易追问的新边界。
- **失败预演：** 如果一开口先讲 mContriever、NeuCLIR、QuDAR，面试官会觉得你在复述论文而不是解决项目问题；回答必须先讲真实故障和 A/B 数据，再用外部研究解释设计合理性。
- **质疑面：** 面试官可能问“12 题是否过拟合”“为什么不用 BM25”“翻译失败怎么办”“为何固定 0.75/0.25”。目前只能回答这是小规模离线证据，足以进入带开关实现，不足以宣布最终生产最优。
- **唯一最高价值补强：** 用完全相同的冻结问题、Chunks、Embedding 和 Query Routing Baseline，对 PRF 开关做单变量 A/B；先证明它是否改善 `evaluation metrics → error rates` 这类术语错配，再决定是否默认长期启用。

## 18. 第五阶段：语言对齐之后，如何解决作者术语错配

### 18.1 为什么 Query Routing 仍没有解决全部问题

Query Routing 解决的是“用户中文、论文英文”的语言不一致：中文原问题保留给跨语言 Semantic，英文检索表达交给 TF-IDF。但语言统一后仍存在第二层 vocabulary mismatch：用户和论文作者可能使用不同的英文术语。

```text
用户检索表达：evaluation metrics
论文作者表达：error rate / test error / classification error
```

这时 TF-IDF 仍然无法建立词面重合。手写 `evaluation → error rate` 领域词典容易过拟合，额外调用 LLM 做多 Query Expansion 又会增加延迟、成本和不可控词汇。第五阶段因此选择 Semantic-guided Pseudo Relevance Feedback：先利用已有 Semantic 找到可能相关的正文，再让当前论文自己的用词反哺词法检索。

### 18.2 最终方案

生产链路保持 Query Routing 不变，只增强 TF-IDF 分支：

```text
original_query（中文）
→ Semantic Retrieval
→ 当前论文纯 Semantic Top-3 Chunks
→ 仅从正文提取 TF-IDF 高权重术语
→ 聚合并选 Top-10 Feedback Terms

lexical_query（英文）
→ Q_original

Feedback Terms
→ Q_feedback

Q_enhanced
= 0.5 × normalize(Q_original)
+ 0.5 × normalize(Q_feedback)

Final Score
= 0.75 × Semantic Score
+ 0.25 × Enhanced TF-IDF Score
```

最终 Hybrid 的 `0.75/0.25` 没有改变；PRF 只改善词法 Query Vector，不更换 Semantic Query，也不改变用户原问题。

### 18.3 三个关键工程判断

第一，Feedback Chunk 必须使用纯 Semantic Top-3，不能用 Hybrid Top-3。否则 TF-IDF 会参与选择反馈文本，增强后的 TF-IDF 又反过来影响排名，形成循环依赖。Semantic 在这里承担“从语义相关正文中发现作者术语”的职责。

第二，必须逐篇论文独立反馈。Paper A 的 Top-3、词表、document frequency、反馈词和 Enhanced TF-IDF 只服务 Paper A；不能把 A 的作者术语扩展给 Paper B。现有检索本身按 `paper_id` 调用，因此无需为此重构全局检索器。

第三，只能从 Chunk 正文提词。实现只读取 `paper_chunks.content`，并移除 `[Section: ...]` 前缀；论文标题、章节名和 `content_type` 都不能成为反馈源，否则高频 metadata 会伪装成相关术语。

### 18.4 Feedback Terms 与向量插值

反馈词继续复用现有 TF-IDF tokenizer、当前论文 vocabulary 和 document frequency。每个候选词的反馈权重为它在 Semantic Top-3 正文中的 TF-IDF 权重之和，然后过滤停用词、标点、纯数字、过短 token 和词表外词，稳定选取 Top-10。

实现没有把 Top-10 词直接拼到 Query 字符串后重新 transform，而是分别构造稀疏向量。`Q_original` 和 `Q_feedback` 先各自做 L2 归一化，再固定按 `0.5/0.5` 插值，避免某一侧仅因为向量尺度较大而支配另一侧。这借鉴 RM3-style interpolation 思想，但不是完整 RM3 relevance model，也没有实现 Rocchio、SPLADE 或 Neural Sparse Retrieval。

### 18.5 为什么这套增强不会成为新的单点故障

新增 `RESEARCH_PRF_ENABLED` 开关，生产默认开启；关闭后完整保留 Query Routing + 普通 TF-IDF。以下情况统一回退到已经算好的原始 TF-IDF：

- 没有 Semantic Feedback Chunk；
- Feedback 正文为空；
- 没有有效反馈词或反馈向量为空；
- 插值失败；
- PRF 运行时异常；
- 当前走的是本阶段未修改的实验性 BM25 或多粒度路径。

回退不会让 Follow-up 失败。诊断中记录 `prf_status`、Feedback Chunk IDs、反馈词、分数、实际数量、插值权重和简短错误，但不保存完整稠密向量。

### 18.6 Evidence Safety

反馈词只是 Retrieval Hint：

```text
Feedback Terms
≠ Evidence
≠ Citation
≠ User Question
≠ Answer Fact
```

它们不进入最终回答 Prompt、Task Memory、Paper Summary 或 Citation。模型最终能陈述的论文事实仍然必须来自检索返回的 Original Paper Chunks。PRF 本身不调用 `ask_deepseek()` 或 `ask_deepseek_json()`，因此相对已有 Query Routing 新增 LLM 调用数为 `0`。

### 18.7 已完成验证与当前证据边界

专项验证覆盖 Feature Flag、纯 Semantic Top-3、逐 Paper 隔离、正文提词、Top-10、向量归一化与 `0.5/0.5` 插值、无反馈/异常回退、Diagnostics 和“无新增 LLM 调用”。结果为：

- PRF + Query Routing + Follow-up Context：`37 passed`；
- TF-IDF / BM25 / Hybrid 邻接测试：`8 passed`；
- Follow-up / Streaming 邻接测试：`11 passed`；
- MCP Retrieval 邻接测试：`7 passed`；
- `py_compile` 与 `git diff --check` 通过。

这证明的是实现契约、兼容性和安全边界，不是检索质量提升。本阶段按要求没有重跑 3 篇/12 问题冻结 Benchmark、canonical pytest、Phase 1–6 MySQL 或大型 E2E。因此面试中可以说“PRF 已经接入并通过专项回归”，不能说“PRF 已经把 Recall 提升了多少”。

### 18.8 可直接复述的升级版回答

> Query Routing 上线后，我又发现一个更细的问题：把中文问题改写成英文只能解决语言不一致，仍解决不了用户用词和论文作者用词不同。例如检索表达是 evaluation metrics，正文写的是 error rate，TF-IDF 还是零命中。我没有手写领域同义词，也没有再加一次 LLM 扩展，而是利用已有 Semantic 结果做按论文隔离的伪相关反馈：取当前论文纯 Semantic Top-3，只从正文中按现有 TF-IDF 统计提取 Top-10 作者术语，把原英文 Query Vector 和反馈向量分别归一化后按 0.5/0.5 插值，再保持原来的 0.75 Semantic + 0.25 TF-IDF。这样没有循环依赖，不会跨论文污染，也没有新增模型调用；任何提词或插值失败都会回退到原 TF-IDF。工程专项测试已经通过，但冻结检索集还没有做 PRF A/B，所以我会准确描述为“已实现、待质量验证”，而不会提前宣称 Recall 提升。

这一步延续了整个案例最重要的方法：先明确当前瓶颈属于语言错配还是术语错配，再选择只改变一个变量的方案；先保证降级和 Evidence 边界，最后才用冻结 Ground Truth 判断是否值得长期启用。

## 19. 第六阶段：Candidate Pool——先让正确正文拥有被判断的机会

### 19.1 为什么不能继续让 Top-K 同时承担候选和证据职责

原链路在 Hybrid 排序后直接截取 Final Top-K。这样做虽然简单，但排名第 8、39 的正文还没有经过更强的相关性判断，就已经永久退出管线。把 Final Top-K 机械增大又会直接增加 Prompt Token、重复内容和噪声。

Candidate Pool v1 因此只做一件事：把“机器内部继续判断的候选数量”和“最终交给 DeepSeek 的 Evidence 数量”分开。

```text
全部 paper chunks
→ Hybrid 全量排序
→ Parent Dedup
→ Candidate Pool Top-20
→ 后续选择
→ Final Top-K
```

生产默认 `RESEARCH_CANDIDATE_POOL_SIZE=20`。它不是新的最终 Top-K，也不会把 20 个块全部送入 Prompt；它只是让原先排名较后的正文有机会被 Reranker 和 MMR 重新判断。

### 19.2 为什么 Candidate Pool 放在 Parent Dedup 后

Parent Dedup 是既有语义：多个 child chunk 如果指向同一个 parent，只保留其中排名最高的代表。Candidate Pool 没有重构这一层，而是在去重后截取前 20，避免候选预算被同一 parent 的多个 child 重复占用。

这也形成了明确职责：

- Hybrid：粗排相关性；
- Parent Dedup：去除相同父证据的结构重复；
- Candidate Pool：保留后续可判断空间；
- Final Top-K：控制最终 Evidence 数量。

关闭 Reranker 和 MMR 时，Candidate Pool 会按原 Hybrid Rank 选出 Final Top-K，结果与旧生产路径一致。内部的 `hybrid_rank`、`hybrid_score`、`paper_id` 等候选字段在 Evidence 边界前统一移除。

### 19.3 这一步解决了什么，没解决什么

Candidate Pool 解决的是**机会问题**：正确正文不会因为过早 Top-K 截断而连精排资格都没有。它本身不改变 Hybrid Rank，因此不能单独声称把 chunk 154 从第 39 提升到了前列。只有后续选择器真正识别它时，较大候选池才转化为质量收益。

面试时最准确的说法是：

> 我没有直接把更多 Chunk 塞进 Prompt，而是新增内部 Candidate Pool，把召回宽度与 Evidence 数量解耦。这样既给精排留下空间，又继续由 Final Top-K 和 ContextBuilder 控制 Token。

## 20. 第七阶段：BGE CrossEncoder——区分“提到主题”和“能够回答”

### 20.1 为什么 Hybrid 后还需要精排

Bi-Encoder Semantic 与 TF-IDF 适合在大量 chunks 上快速计算，但 Query 和 Chunk 是分别编码的。CITISEN 的导航句同时提到 Experimental Setup 与 Conclusion，因此“主题很相关”，却不包含任何可回答细节。

CrossEncoder 将原始 Query 和每个 Candidate 放在同一个模型输入中联合判断，更适合区分：

```text
导航句：告诉你答案在哪
正文块：直接提供指标、设置或结论
```

生产实现使用 `BAAI/bge-reranker-v2-m3`，输入完整 Candidate Pool，而不是只重排 Hybrid Top-K。原始中文用户问题用于 Reranker；英文 Lexical Query 仍只服务 TF-IDF，不能替换用户意图。

### 20.2 工程实现与失败边界

- 使用 `FlagEmbedding` 的 `FlagReranker`，对 Query/Chunk pairs 批量打分；
- 模型按需延迟加载，避免应用启动必须依赖模型下载成功；
- 同一进程缓存单例，并用锁保护首次加载；
- `rerank_score` 降序，分数相同时以原 `hybrid_rank` 确定稳定顺序；
- 模型导入、加载、推理或分数校验失败时，回退到 Hybrid 顺序；
- Reranker 不调用 DeepSeek，不改变 Prompt、Citation 或 Original Paper Evidence 边界；
- `rerank_score`、`rerank_rank` 只进入诊断，不进入最终 Evidence。

### 20.3 真实最小 Smoke

使用真实 `bge-reranker-v2-m3` 和中文问题“这篇论文使用了什么评价指标？”测试三个英文候选：模型结构介绍、`classification error rates / test error` 正文、论文组织结构导航。结果为：

| 排名 | Chunk 含义 | Score |
| ---: | --- | ---: |
| 1 | classification error rates / test error 正文 | 0.624649 |
| 2 | 论文组织结构导航 | 0.000814 |
| 3 | 模型结构介绍 | 0.000019 |

CPU 首次模型下载/加载约 `217.011s`，同进程热推理约 `0.568s`。这证明模型能够加载、返回合法分数，并在这个最小真实语义对照中把可回答正文排到第 1；它不等于 12 问冻结 Benchmark 已经改善，也不能把冷启动数字当成稳定生产 P95。

### 20.4 面试复述

> Candidate Pool 解决“正文有没有机会进入后续判断”，CrossEncoder 解决“候选里谁真正回答问题”。我没有让 CrossEncoder 扫描整篇所有 chunks，而是先用便宜的 Hybrid 把范围缩到 20，再用本地 BGE 联合编码 Query 和正文。模型不可用时退回 Hybrid，因此精排能力不会成为聊天单点故障。

## 21. 第八阶段：MMR——精排之后还要控制证据冗余

### 21.1 为什么 Reranker Top-K 仍可能不够

Reranker 优化的是逐块 relevance。如果多个 chunks 都在重复同一个实验设置，它们可能一起排在前列，挤掉 Conclusion 或另一个实验维度。对于有限 Final Top-K，“五个都相关但内容重复”仍然可能无法完整回答复合问题。

MMR 放在完整 reranked Candidate Pool 之后：

```text
MMR Score(chunk)
= λ × normalized_rerank_relevance
  - (1 - λ) × max_cosine_similarity(chunk, selected)
```

默认 `RESEARCH_MMR_ENABLED=true`、`RESEARCH_MMR_LAMBDA=0.5`。它不是再次召回，也不是新 Reranker，而是在已确认相关的候选中平衡“相关”与“不重复”。

### 21.2 relevance、redundancy 与稳定性

- relevance 使用 Candidate Pool 内的 `rerank_score` Min-Max 归一化；
- 如果所有 rerank scores 相同，按稳定的 rerank rank 生成退化 relevance，避免除零和随机顺序；
- redundancy 使用数据库中已有 chunk dense embedding 的余弦相似度；
- 第一项始终选择 rerank rank 1，保证最强相关证据不会被多样性惩罚淘汰；
- 后续选择最大 MMR Score，并按 rerank rank、原始输入顺序稳定打破并列；
- MMR 输入是完整 reranked Candidate Pool，而不是已经截断的 Reranker Top-K，因此较后但信息互补的 chunk 仍有机会进入 Final Evidence。

专项构造用例中，rerank rank 1 被保留，一个与它近乎重复的 rank 2 被正交信息块替代；另一个用例证明 rerank rank 8 可以从完整候选池进入 Final Top-K。

### 21.3 为什么没有新增模型调用

MMR 只读取候选内部临时字段 `_mmr_embedding`，该向量来自已经存储在 `paper_chunks.dense_vector_json` 的 Chunk Embedding。它不会重新调用 Seed，不调用 DeepSeek，也不加载新的模型。

`_mmr_embedding`、`mmr_score`、`mmr_rank`、`mmr_relevance` 和 `mmr_max_similarity` 都会在 Evidence 边界前剥离；诊断只保留 Chunk ID、排名和标量分数，不保存正文或完整向量。

### 21.4 降级策略

以下情况统一退回 Reranker 顺序，不让 Follow-up 失败：

- MMR 开关关闭；
- Reranker 没有成功应用；
- Candidate 数量不大于 Final Top-K，没有必要做多样性选择；
- Dense Embedding 缺失、非法、零范数或维度不一致；
- rerank relevance 非法；
- MMR 运行时异常。

这一步体现的工程原则是：高级检索能力应该是可撤销的增益层，而不是新的单点故障。

## 22. 当前完整结论：这次“难题”真正解决到了哪一层

### 22.1 已经解决并有质量数据的部分

跨语言 Query Routing 已通过冻结 A/B 证明：中文 Semantic + 英文 TF-IDF 比“中文同时驱动两路”更适合当前中文用户、英文论文的分布。该层可以引用 Recall 和排名指标。

### 22.2 已经完成工程闭环、但仍待质量 A/B 的部分

Semantic-guided PRF、Candidate Pool、BGE CrossEncoder 和 MMR 已形成可运行的 Evidence Selection Stack：

```text
语言对齐
→ 作者术语补全
→ 扩大内部候选机会
→ 联合语义精排
→ 多样性选择
→ 安全 Evidence
```

最新验证结果为：

- MMR 专项：`14 passed`；
- MMR + Reranker + Candidate Pool + PRF + Query Routing + Hybrid：`63 passed`；
- Follow-up / Streaming / RAG / MCP 邻接：`13 passed`；
- 历史 RAG Reranker 兼容：`2 passed`；
- `py_compile`、`git diff --check` 与新增文件 whitespace 检查通过。

这些结果证明模块顺序、Feature Flag、fallback、稳定排序、无新增 MMR 模型调用和 Evidence Safety，没有证明冻结集 Recall 已继续上升。

### 22.3 仍未解决的产品级问题

- 复合问题还没有 Intent Decomposition，因此 MMR 只能鼓励多样性，不能证明“实验设置”和“Conclusion”各有一块；
- ContextBuilder 的固定每篇块数和 Dynamic Evidence Allocation 尚未重构；
- Candidate Pool 20、MMR λ=0.5 只是明确可回退的 v1 默认值，不是大规模最优参数；
- 原 CITISEN 问题仍需用相同 Source、Query 和 Target Evidence 做最终 E2E 复验。

### 22.4 面试中的一句话结论

> 我把一个表面上的“模型证据不足”问题定位成多层检索失配：先用冻结 A/B 解决跨语言 Query 路由，再把生产证据选择拆成 PRF 术语补全、Candidate Pool 粗召回、CrossEncoder 精排和 MMR 去冗余。整个链路都有开关、诊断和降级，并保持原论文块是唯一事实证据。Query Routing 已有 Recall 增益数据；后续层完成了工程闭环和真实模型 Smoke，但我不会在冻结 Benchmark 前声称它们已经彻底修复原案例。
