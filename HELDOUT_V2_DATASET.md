# Paper Hunter M1 独立测试集预注册

> 数据集：`heldout_v2`
>
> 预注册日期：`2026-08-02`
>
> 验证完成日期：`2026-08-03`
>
> 适用版本：`v0.8.3`
>
> 当前状态：一次性基线/M1对照已完成，M1未通过主门槛，数据集已关闭

## 建立目的

`paper_multi_granularity` 在12篇论文、60道开发题上达到预设门槛，但配对bootstrap区间仍跨0。`heldout_v1`已经用于上一轮重排实验并正式关闭，不能继续拿来选择M1。

`heldout_v2`只回答一个问题：在从未参与策略设计的新论文上，M1能否相对固定边界基线提高前三名证据排序，同时保住前五名召回和证据覆盖率。

## 冻结对照

基线与候选除分块和候选融合方式外，其余配置完全相同：

```text
问题：冻结英文等价检索问题
Embedding：doubao-embedding-vision-251215
关键词检索器：TF-IDF
融合权重：Seed 0.75 / TF-IDF 0.25
最终输出：Top-5
DeepSeek重排：关闭
```

- 基线：`length_boundary`，父块`1200/150`，单路检索。
- 候选：`paper_multi_granularity`，父块`1200/150`，完整句子子块约`600/75`；父块Top-20与子块Top-20按共同父块ID执行RRF，常数固定为60，返回Top-5父块。
- 不允许根据`heldout_v2`结果修改块大小、重叠、候选池、RRF常数、查询、权重或证据匹配阈值。

## 论文清单

| ID | 论文 | 领域 | 开放来源 |
| --- | --- | --- | --- |
| V201 | Mamba: Linear-Time Sequence Modeling with Selective State Spaces | 序列建模与状态空间模型 | arXiv 2312.00752 |
| V202 | QLoRA: Efficient Finetuning of Quantized LLMs | 大模型高效微调 | arXiv 2305.14314 |
| V203 | Segment Anything | 计算机视觉与图像分割 | arXiv 2304.02643 |
| V204 | Mastering Diverse Domains through World Models | 强化学习与世界模型 | arXiv 2301.04104 |
| V205 | TabPFN: A Transformer That Solves Small Tabular Classification Problems in a Second | 表格数据与贝叶斯推断 | arXiv 2207.01848 |
| V206 | MatterGen: a generative model for inorganic materials design | 材料生成与扩散模型 | arXiv 2312.03687 |
| V207 | scGPT: Towards Building a Foundation Model for Single-Cell Multi-omics Using Generative AI | 单细胞生物信息学 | bioRxiv 2023.04.30.538439v2 |
| V208 | From Local to Global: A Graph RAG Approach to Query-Focused Summarization | 图检索增强生成 | arXiv 2404.16130 |

完整URL、预定本地文件名和进度见`benchmarks/heldout_v2_manifest.csv`。8篇论文与开发集及`heldout_v1`的标题重叠数必须为0。

选样校验结果：论文数`8`、与既有论文标题重叠`0`、重复标题`0`、重复ID`0`。用户授权后已下载并核验8篇PDF，共`308`个物理页、`81,962,255`字节；清单中的下载状态均为`downloaded`，标注状态均为`validated`。PDF与页文本只保存在被Git忽略的`downloads/`中。

## 标注设计

用户已于`2026-08-02`明确授权读取`downloads/heldout_v2/`中的PDF。每篇已制作5道中文问题，共40题：事实型、方法型、参数型、结果型和原因型各1道。

- 一个问题只考查一个明确事实或关系。
- 参考答案必须由一至两段连续原文完整支持。
- `evidence_page`使用PDF物理页码。
- `evidence_text`保存连续英文原文，不保存摘要改写或块ID。
- 问题不得根据任一策略的检索结果设计或修改。
- 构建脚本按论文ID、物理页码和原文起止标记截取证据；40段证据均通过指定页连续存在检查。
- 数据平衡检查为8篇论文各5题、五种题型各8题、40个问题无重复。
- 40条英文检索问题与中文问题一一对应、无缺失、无多余项且不含中文字符。

计划文件：

- `benchmarks/heldout_v2_manifest.csv`：论文来源与进度，8篇均为`downloaded/validated`。
- `benchmarks/heldout_v2.csv`：40道中文问题、参考答案、物理页码和连续英文证据，已冻结。
- `benchmarks/heldout_v2_queries_en.csv`：40条等价英文检索问题，已冻结。
- `downloads/heldout_v2/`：8篇本地PDF，已下载并由Git忽略。
- `downloads/heldout_v2_runtime/`：独立SQLite、缓存与配对分析，已创建并由Git忽略。

冻结文件指纹：

```text
heldout_v2.csv             0A1A29758B332F53D9F898CA08A94E83CC29DA071F2C7FA066FF746534A27FF5
heldout_v2_queries_en.csv  4F3E83266476E008F1F9DE81F5E2D28CAA63985EA218D5702D667CE5CF2A2AA5
heldout_v2_manifest.csv    4F76E5850A0E72C8AFBD5892586B8F1242B1617A607AB4988C734C1DC3FE2C85
```

从记录这些指纹起，不得根据后续基线或M1结果修改问题、答案、页码、证据或英文查询。

## 预注册验收规则

主指标为`Hit@3`，辅助指标为`Hit@1`、`Hit@5`、`MRR@5`和证据覆盖率。固定随机种子42执行10,000次逐题配对bootstrap。

M1只有同时满足以下条件，才允许从“候选策略”升级为正式推荐策略：

1. `Hit@3`差值为正，且配对bootstrap 95%区间下界大于0。
2. `Hit@5`相对基线最多下降1题，即不超过2.5个百分点。
3. 证据覆盖率相对基线下降不超过1个百分点。
4. `MRR@5`点估计不低于基线。

若未通过，保留M1代码和失败记录，但普通产品默认继续使用`length_boundary`；不得回到这40题上调参后重复宣称独立验证。

## 一次性验证结果

基线运行`#1`与M1运行`#2`均完成40题且错误数为0。运行期间M1两次遇到Seed SSL EOF；严格索引一致性门禁阻止TF-IDF静默降级进入正式结果，在不修改任何冻结配置的前提下恢复同一运行后完成。

| 指标 | 固定边界 | M1 | 差值 | 95%配对区间 |
| --- | ---: | ---: | ---: | ---: |
| Hit@1 | 50.00% | 55.00% | +5.00个百分点 | `[-5.00,+15.00]`个百分点 |
| Hit@3 | 82.50% | 77.50% | -5.00个百分点 | `[-12.50,0.00]`个百分点 |
| Hit@5 | 90.00% | 90.00% | 0.00个百分点 | `[-10.00,+10.00]`个百分点 |
| MRR@5 | 0.6675 | 0.6925 | +0.0250 | `[-0.0333,+0.0896]` |
| 证据覆盖率 | 87.04% | 88.66% | +1.62个百分点 | `[-6.91,+10.37]`个百分点 |

逐题排名改善6题、变差5题、不变29题；Top-5新增2题、丢失2题。M1通过Hit@5、覆盖率和MRR三条保护门槛，但`Hit@3`差值不是正数，主门槛失败，因此最终判定`adopt_m1=false`。

结论：M1提高了首位排序、MRR点估计与平均证据覆盖，但没有把正确证据更稳定地保留在前三名。它继续作为实验策略，普通产品默认仍为`length_boundary`。`heldout_v2`已经关闭，不得根据本结果调参后重新使用。

## 后续顺序

1. 已完成：取得PDF读取授权。
2. 已完成：下载并检查8篇PDF文件头、标题和页数。
3. 已完成：制作40题并完成页码级连续证据校验。
4. 已完成：冻结中文案例与英文查询并记录SHA-256。
5. 已完成：在隔离数据库中运行基线`#1`与M1`#2`。
6. 已完成：输出逐题配对结果与10,000次bootstrap区间，判定M1不采用。
7. 已完成：关闭`heldout_v2`，后续不得用它继续选择M1参数。

数据集构建阶段没有调用Seed或DeepSeek；正式对照调用Seed完成索引与检索，没有调用DeepSeek。版本收尾的完整离线测试为`78 passed`。
