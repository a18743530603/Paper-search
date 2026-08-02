# Paper Hunter 独立测试集记录

> 数据集：`heldout_v1`
>
> 建立日期：`2026-07-31`
>
> 冻结日期：`2026-08-01`
>
> H2 首次运行完成日期：`2026-08-02`
>
> 修正版生成复核：`2026-08-02` 已完成
>
> 当前状态：H2-R 检索与完整证据生成复核完成，`heldout_v1` 已关闭

## 建立目的

现有 12 篇论文、60 道题已反复用于分块、查询、检索器和重排实验，因此定位为开发集，不能继续承担无偏泛化测试。

`heldout_v1` 使用此前从未参与开发的论文。在清单冻结后，不得根据这批论文修改分块参数、检索权重、重排 Prompt、生成 Prompt 或评分阈值。最终只运行一次基线与候选方案对照。

## 冻结方案

- 基线：检索运行 `#21` 对应的固定边界 `1200/150`、固定英文查询、Seed + TF-IDF、无重排。
- 候选：检索运行 `#35` 对应的相同初召回 + DeepSeek Top-20 列表重排到 Top-5。
- 生成：`paper-answer-v1`。
- 裁判：`paper-quality-judge-v1`。
- 不得根据 `heldout_v1` 的结果回头调整上述配置；若修改方案，必须建立新的未见测试集。

## 论文清单

| ID | 论文 | 领域 | 来源 |
| --- | --- | --- | --- |
| H01 | OpenVLA: An Open-Source Vision-Language-Action Model | 机器人与多模态 | arXiv 2406.09246 |
| H02 | A Foundation Model for the Earth System | 地球系统与气象 | arXiv 2405.13063 |
| H03 | TimeMixer: Decomposable Multiscale Mixing for Time Series Forecasting | 时间序列预测 | arXiv 2405.14616 |
| H04 | De novo design of high-affinity protein binders with AlphaProteo | 蛋白质设计 | arXiv 2409.08022 |
| H05 | A Sober Look at LLMs for Material Discovery | 化学与材料发现 | arXiv 2402.05015 |
| H06 | GMAI-MMBench: A Comprehensive Multimodal Evaluation Benchmark Towards General Medical AI | 医疗多模态评测 | arXiv 2408.03361 |
| H07 | Can Language Models Solve Olympiad Programming? | 代码推理与软件工程 | arXiv 2404.10952 |
| H08 | Better than classical? The subtle art of benchmarking quantum machine learning models | 量子机器学习 | arXiv 2403.07059 |

完整地址、文件名和进度见 `benchmarks/heldout_v1_manifest.csv`。

H06 原计划使用 PMC11519755，但 PMC 开放资源包与期刊 PDF 在当前网络环境下均无法稳定复现下载。由于尚未开始标注或运行测试，清单冻结前将其替换为同属医疗 AI 领域、可从 arXiv 稳定获得的 GMAI-MMBench；本次替换不依据任何检索得分。

## 标注规则

每篇论文固定制作 5 道中文问题：事实型、方法型、参数型、结果型和原因型各 1 道，共 40 道。

- 一个问题只考查一个明确事实或关系。
- `reference_answer` 必须能被一至两段连续原文完整支持。
- `evidence_page` 使用 `pypdf` 读取到的 PDF 物理页码，不使用网页页码。
- `evidence_text` 保存连续英文原文，不保存摘要改写或文本块 ID。
- 结果题必须明确比较对象；原因题优先使用作者明确的原因或优势表述。
- 标注完成后逐条在对应物理页执行 token 覆盖校验。
- 不允许根据检索结果修改问题、答案或证据，以免测试集泄漏。

## 文件说明

- `benchmarks/heldout_v1_manifest.csv`：论文来源、PDF 地址、本地文件名和标注进度。
- `benchmarks/heldout_v1.csv`：最终 40 道问答与页码级证据。
- `benchmarks/heldout_v1_queries_en.csv`：H2 冻结的 40 条等价英文检索问题。
- `downloads/heldout_v1/`：本地 PDF，仅用于运行，不进入 Git。

## 冻结校验

- 共 `40` 道唯一问题、`8` 篇唯一论文，每篇恰好 `5` 题。
- 事实型、方法型、参数型、结果型、原因型各 `8` 题。
- 与开发集 `benchmarks/chunking_baseline.csv` 的论文标题重叠数为 `0`。
- `40/40` 条证据均连续存在于指定的 PDF 物理页。
- 冻结 CSV 的 SHA-256：`B9724E71F74966B03B15E396597BF70B49C097D07A2A5AF83CF3FD0FCE5B60BB`。
- 英文查询 CSV 的 SHA-256：`B60042FE7E9578CCD4C67711ED50F25DF0B617F4CA631FEE482A09E2E46B9819`。

正式测试前必须重新计算指纹；若与上述值不一致，应停止运行并调查数据为何发生变化。

## H2 预注册配置

- 独立运行目录：`downloads/heldout_h2_runtime/`，不得使用主数据库中的开发集案例。
- 基线：固定边界 `1200/150`、固定英文查询、Seed + TF-IDF、权重 `0.75/0.25`、Top-5、无重排。
- 候选：与基线相同的 Top-20 初召回，使用冻结的 DeepSeek 列表重排 Prompt 输出 Top-5。
- 生成器：`paper-answer-v1`；裁判：`paper-quality-judge-v1`。
- 唯一算法变量：是否启用 DeepSeek Top-20 到 Top-5 列表重排。
- API 或网络中断允许对同一冻结输入做失败恢复，但不得修改题目、英文查询、配置或 Prompt。
- H2 隔离库基线检索运行 `#1` 与候选重排运行 `#2` 已完成；两组生成运行 `#1/#2` 均完成40题且错误为0。
- 基线：`Hit@1/3/5=50.00%/67.50%/85.00%`、`MRR=0.6204`、覆盖率 `85.01%`。
- 重排：`Hit@1/3/5=62.50%/90.00%/92.50%`、`MRR=0.7675`、覆盖率 `90.17%`。
- 重排相对基线使 Hit@1/3/5 提高 `12.50/22.50/7.50` 个百分点；两组均为 `0/40` 错误。
- 逐题比较：15题排名改善、4题变差、21题不变；Top-5新增5题、丢失2题。
- 10,000次配对bootstrap（seed=42）：Hit@3差值95%区间为`[+5.00,+37.56]`个百分点，MRR差值区间为`[+0.0296,+0.2567]`。
- 基线生成曾在17题后因DeepSeek余额不足中断；`2026-08-02`按原配置恢复23题，最终40题无错误。
- 旧截断基线生成指标：Faithfulness `1.0000`、Answer relevance `0.9500`、Reference correctness `0.8863`、Citation correctness `0.9688`。
- 旧截断重排生成指标：Faithfulness `0.9808`、Answer relevance `0.9400`、Reference correctness `0.8900`、Citation correctness `0.9662`。
- 生成配对bootstrap的四项95%区间均包含0；Reference correctness仅提高`0.00375`，区间为`[-0.0275,+0.0300]`。
- 最终检索结论：未见集支持重排改善Hit@3和MRR。G3a随后发现生成评测读取的是截断为800字符的落库证据，两组各有7题的标准证据在截断后完全消失，因此原始生成质量对照只代表截断证据管线，不能作为完整块RAG结论。
- 修复只允许恢复检索与生成证据一致性，并按原冻结配置建立新运行；不得调整问题、查询、分块、权重、候选池、重排Prompt、生成Prompt或裁判Prompt。
- `v0.8.1` 已取消新检索运行中 `retrieved_json` 的 800 字符截断并通过 `70` 项测试；旧运行不会自动补全，只保留审计。
- 开发集 G2 修正版已通过完整证据 `0 mismatch` 门禁并完成60题生成复核；H2-R继续使用本文件冻结的原配置，没有调整任何参数。
- H2-R 新检索运行 `#3/#4` 与旧检索指标完全一致，完整证据门禁两组均为 `0 mismatch`；修正版生成与全缓存复跑 `#3/#4/#5/#6` 已全部完成且错误为0。
- 修正版基线/重排的 Answer relevance 为 `99.00%/100.00%`，配对95%区间为 `[+0.25,+2.00]` 个百分点；Reference correctness 为 `96.75%/97.62%`，差值区间跨0。

## 当前进度

```text
论文选择：8 / 8
PDF 下载：8 / 8（均通过 `%PDF-` 文件头校验）
题目标注：40 / 40（H01-H08完成）
证据校验：40 / 40（连续原文校验通过）
正式测试：H2-R 检索与完整证据生成复核完成；旧截断运行仅保留审计
```

`heldout_v1` 至此停止使用。后续不得根据这40题继续调整算法、Prompt或阈值；需要验证新方案时必须建立 `heldout_v2` 或采用交叉验证。
