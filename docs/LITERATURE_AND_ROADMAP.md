# 相关工作、论文定位与升级路线

> 检索日期：2026-07-31  
> 项目定位：Auditing Financial Research Agents: A Temporal Reliability Benchmark for Trustworthy Evaluation

> 2026-08-13 更新：最新主方法为 **ATLAS-XBRL**，将Claude语义编译、SEC官方Company Facts、
> label-free程序校准和Decimal确定性计算组合为可审计金融Agent。在20道运行前冻结的真实财报计算题上，
> 同模型普通Web Search Agent为75%（15/20），ATLAS-XBRL为100%（20/20），逐题5胜、15平、0负。
> 完整数据见[`20题正式研究卡`](../temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/README.md)。

> 早期ATLAS-RAG、ATLAS-Fusion和ATLAS-Compute负结果均保留为研究演进记录。它们共同表明：
> 仅增加网页轨迹、严格Gate或程序化计算仍受一手操作数缺失影响；最终升级必须同时改进事实获取与计算执行。
> 最新顶会论文与实现映射见[`TOP_CONFERENCE_RAG_2026.md`](TOP_CONFERENCE_RAG_2026.md)。

## 1. 这项工作位于什么研究空缺？

现有工作已经分别研究了金融搜索、知识时效、检索冲突、引用质量和拒答，但这几个问题很少在同一条
金融 Agent trace 中被联合评估。本项目的核心定位不是再做一个“最终数字准确率”榜单，而是评估：

1. 答案是否正确；
2. 引用是否真的支持答案；
3. 证据在指定截止日是否已经发布；
4. 证据是否属于正确期间、版本和单位；
5. 证据不充分时，Agent 是否能够适当拒答。

因此，本项目可以表述为 **point-in-time financial agent auditing**：把金融搜索任务中的
look-ahead bias 转化为可追踪、可检测、可拒绝的证据级评测问题。

## 2. 最相关论文及其对本项目的影响

| 研究方向 | 代表工作 | 已经解决了什么 | 本项目可以补充什么 |
|---|---|---|---|
| 金融搜索 Agent | [FinSearchComp](https://arxiv.org/abs/2509.13160) | 635 个专家问题、三类金融搜索任务和端到端答案评测 | 在最终答案之外审计每条证据的 point-in-time 合规性 |
| 金融时间穿越 | [Look-Ahead-Bench](https://arxiv.org/abs/2601.13770) | 直接评估金融工作流中的 look-ahead bias 和跨市场阶段表现 | 加入发布日期、期间、版本、单位四类可解释证据检查和四策略消融 |
| 金融检索路径 | [FinRetrieval](https://arxiv.org/abs/2603.04403) | 比较 Web、浏览器与结构化数据检索配置，并保存检索 trace | 研究不同检索通道是否会产生不同的时间泄漏风险 |
| 动态知识评测 | [FreshQA / FreshLLMs](https://aclanthology.org/2024.findings-acl.813/) | 区分永恒、缓慢变化、快速变化和错误前提问题 | 将“知识是否新鲜”推进到“证据在目标历史时点是否允许使用” |
| 时间冲突 | [When Facts Change](https://aclanthology.org/2026.findings-acl.103/) | 发现模型识别到事实可变，不代表最终答案能正确处理新旧事实冲突 | 在模型答案之后加入可执行的 evidence gate，而不只依靠提示词 |
| 时间感知检索 | [MRAG / TEMPRAGEVAL](https://openreview.net/attachment?id=GXxIWaNkoN&name=pdf) | 将语义相关性与时间匹配分开建模，并构造时间扰动评测 | 把时间评分扩展为日期、期间、版本和单位的金融专用约束 |
| 引用质量 | [ALCE](https://aclanthology.org/2023.emnlp-main.398/) | 将答案质量、引用正确性和引用完整性分开评估 | 再增加“引用当时是否可用”和“引用是否为正确数据版本” |
| 检索冲突 | [ClashEval](https://proceedings.neurips.cc/paper_files/paper/2024/hash/3aa291abc426d7a29fb08418c1244177-Abstract-Datasets_and_Benchmarks_Track.html) | 评估模型内部知识与外部检索证据之间的冲突 | 进一步区分未来证据、修订值、错期间和错单位四种冲突 |
| 大规模冲突数据 | [ConflictBank](https://proceedings.neurips.cc/paper_files/paper/2024/hash/baf4b960d118f838ad0b2c08247a9ebe-Abstract-Datasets_and_Benchmarks_Track.html) | 构造包含时间差异在内的大规模 claim–evidence 冲突数据 | 提供金融任务中的小规模、强标注、可解释受控扰动 |
| 不完美检索鲁棒性 | [Toward Robust Retrieval-Augmented Language Models](https://aclanthology.org/2024.tacl-1.91/) | 系统评估不可回答、对抗和互相冲突的检索集合 | 把“检索是否有害”具体化为可执行的金融时间合规规则 |
| 事实级冲突处理 | [FaithfulRAG](https://aclanthology.org/2025.acl-long.1062/) | 在事实粒度处理模型记忆与检索内容的冲突 | 将当前证据级 gate 升级为 claim–evidence 图和事实级裁决 |
| 事实性与忠实性 | [FRANQ](https://aclanthology.org/2026.findings-acl.338/) | 区分“答案事实上正确”和“答案受当前证据支持” | 分开报告数字正确率、证据支持率和时间合规率，避免把碰巧答对算成可靠 |
| 选择性回答 | [Selective QA under Domain Shift](https://aclanthology.org/2020.acl-main.503/) | 用 risk–coverage 衡量在准确率约束下的回答覆盖率 | 将二元 gate 改为可校准的风险分数，报告选择性准确率和覆盖率 |
| 拒答训练 | [R-Tuning](https://aclanthology.org/2024.naacl-long.394/) | 训练模型区分已知和未知并适当拒答 | 为时间元数据缺失设计 refusal-aware 校准或轻量训练数据 |

## 3. 真实实验如何改变了技术路线

受控实验中，完整验证器能够利用人工完整标注的元数据稳定拒绝污染证据；真实 Web Search 中，
网页的发布日期、版本或单位经常缺失。于是同一套严格规则可能把“无法验证”直接等同为“错误”，
产生过度拒答。

第一轮 20题×4策略结果显示了这个落差：普通 Agent 的最终正确率/覆盖率为 50%/65%，
完整验证器为 20%/25%。最新 20×5 同模型实验加入校准 ATLAS 后，普通 Agent 为
55%/75%，完整验证器为 35%/40%，ATLAS 为 45%/50%。因此 unknown/violation 分离确实
减少了严格 Gate 的损失，但没有使单轨迹 ATLAS 超过普通搜索。所有运行都保存了搜索来源，
但“有来源”不等于“来源元数据足以通过 point-in-time 验证”。

ATLAS-Compute进一步证明：即使公式改为程序执行，只要操作数仍来自开放网页，跨公司题就可能因为缺少
可核验的一手事实而拒答。于是ATLAS-XBRL把“独立元数据获取”推进为“官方结构化事实工具”，并将
LLM限制在语义编译阶段。正式20题结果为100%对75%，但仅适用于可映射到SEC XBRL的任务。

这意味着后续论文阅读不应只集中在prompt engineering，而应转向以下四条主线：

1. **Temporal retrieval / point-in-time data**：怎样在检索阶段找到“当时可用”的页面或数据快照；
2. **Evidence grounding and conflict resolution**：怎样在 claim 粒度判断来源是否支持答案及各来源是否冲突；
3. **Selective prediction and abstention calibration**：怎样在可靠性与覆盖率之间做可量化的权衡。
4. **Structured tool execution**：怎样把LLM规划、权威事实获取和确定性数值程序组合为可审计系统。

换言之，真实 pilot 把论文问题从“更严格的规则会不会更好”推进为：

> 在开放网页元数据不完整时，如何同时降低时间泄漏和不必要拒答？

这是比简单比较四个 prompt 准确率更有研究价值、也更容易形成论文主线的问题。

## 4. 建议升级后的研究问题与假设

### RQ1：Prompt、过滤器和完整 gate 分别减少了什么风险？

- **H1a**：时间 Prompt 会降低候选证据中的未来来源暴露，但不能完全消除；
- **H1b**：元数据过滤和完整 gate 会把最终采用证据的时间泄漏降得更低；
- **H1c**：如果只依赖模型自报元数据，严格 gate 会显著降低回答覆盖率。

### RQ2：独立元数据解析能否修复过度拒答？

- **H2a**：独立解析 HTTP 日期、结构化网页字段和官方发布日期，会提高可验证证据覆盖率；
- **H2b**：在不增加最终证据泄漏的条件下，它会提高选择性准确率和回答覆盖率。

### RQ3：不同检索通道的时间风险是否不同？

- **H3a**：官方 API、监管申报和官方统计表比通用 Web Search 更容易提供确定的期间、版本和单位；
- **H3b**：检索路由器可以同时降低成本、延迟和元数据缺失率。

### RQ4：跨轨迹融合能否把互补搜索转成可靠最终答案？

- **H4a**：多条独立搜索轨迹的 listwise 仲裁会提高模型初稿准确率；
- **H4b**：若不区分“最终派生单位”和“底层操作数单位”，严格 Gate 仍会误拒正确计算；
- **H4c**：单位兼容和拒答阈值必须在新开发集定义，再在独立测试集检验，不能继续用当前 20 题调参。

## 5. 优先级明确的升级路线

### P0：把本轮 20×4 固化为可审计基线

必须保存模型、日期、完整提示词和哈希、API response ID、搜索动作、全部搜索来源、最终引用、
token、延迟、raw response 哈希、错误和排除规则。按题目聚类做 bootstrap 置信区间；
当前只有单次 20 题时，应明确标为 pilot，不做模型总体优越性的显著性结论。

### P1：独立 Temporal Metadata Resolver

这是当前最重要的技术升级。不要只相信模型生成的 `published_at`：

1. 解析 `datePublished`、`dateModified`、OpenGraph、JSON-LD、RSS 和 HTTP headers；
2. 为 SEC filing accession/filing date、BLS/BEA/FRED release、公司年报等建立来源适配器；
3. 保存页面抓取时间、原始字段、解析规则和证据片段；
4. 将 `unknown` 与 `violation` 分开，记录元数据来源和置信度；
5. 必要时使用网页归档快照验证某 URL 在截止日前是否已存在。

建议输出：

```text
published_at = 2025-02-14
metadata_origin = "JSON-LD.datePublished"
metadata_confidence = "high"
verified_independently = true
```

### P2：从二元拒答升级为可校准的选择性 gate

当前 gate 的“任一字段缺失即拒答”适合安全上限测试，但不一定是最佳生产策略。建议构造连续风险分数：

```text
risk = w1·future_risk + w2·period_mismatch + w3·revision_risk
     + w4·unit_risk + w5·source_quality + w6·metadata_uncertainty
```

在独立开发集上校准阈值，并报告：

- risk–coverage curve；
- selective accuracy；
- false-abstention rate；
- accepted-evidence temporal leakage；
- 在不同错误成本下的 expected utility。

### P3：事实级 claim–evidence graph 与纠错循环

把“一条答案 + 一个 evidence 列表”改成：

```text
claim -> supporting passages -> source metadata -> temporal checks
```

如果某个关键 claim 未通过：

1. 针对缺失字段重新检索；
2. 优先寻找官方、第一方或结构化来源；
3. 比较多来源是否数值、期间和版本一致；
4. 仍无法验证才拒答。

这样可区分“答案错”“证据不支持”“元数据未知”“来源相互冲突”，并减少一次性 gate 的过度拒答。

### P4：金融检索路由

根据问题类型选择检索通道：

- 公司财报：SEC/issuer filing；
- 宏观数据：BLS、BEA、FRED、国家统计机构；
- 市场价格/回报：带 point-in-time 保证的 market-data API；
- 新闻和解释性问题：Web Search；
- Web 仅作为官方来源找不到时的 fallback。

实验中应保留同一批问题，并比较 Web-only、structured-only 和 routed retrieval 三种设置的准确率、
时间泄漏、元数据缺失、成本和延迟。

### P5：扩大实验与统计设计

在本轮 pilot 之后：

1. 固定题集与协议，至少独立重复 3 次；
2. 以“题目”为聚类单位计算 bootstrap 95% CI，避免把同题四策略当作独立样本；
3. 记录并报告 transport failure，不把失败静默删除；
4. 对策略顺序进行随机化或 counterbalancing，降低缓存和运行时段影响；
5. 引入第二模型和第二检索后端，检验结论是否只适用于一个中转或模型；
6. 抽样进行双人盲审，报告日期、版本和支持关系标注的一致性；
7. 在实验前固定主要指标、排除规则和停止条件。

## 6. 建议论文结构

1. **Introduction**：金融回测中的 look-ahead bias；最终答案正确并不等于证据可靠；
2. **Related Work**：financial search、temporal QA、RAG conflicts、citation evaluation、selective QA；
3. **Benchmark**：20 个问题、四类受控扰动、真实 Web pilot、point-in-time 标签；
4. **Methods**：普通 Agent、时间 Prompt、元数据过滤、Temporal Evidence Gate；
5. **Metrics**：答案、证据、时间泄漏、覆盖率、risk–coverage、成本和延迟；
6. **Results**：受控实验与真实 pilot 分开呈现，解释两者为何不同；
7. **Error Analysis**：未来来源、错期间、错版本、错单位、元数据缺失和过度拒答；
8. **Limitations**：样本量、单模型/中转、模型自报日期、gold answer 与人工标注；
9. **Future Work**：独立 metadata resolver、校准 gate、结构化检索和多模型重复实验。

## 7. 结果陈述边界

本项目当前可以声称：

- 受控证据冲突中，显式元数据验证比只在 prompt 中提醒更稳健；
- 真实 Web Search 中，完整来源和引用 trace 能揭示最终准确率无法显示的失败；
- 严格 gate 面临可验证元数据不足与过度拒答之间的真实权衡。

本项目当前不能声称：

- 某个 Claude/OpenAI 模型在总体金融搜索任务上优于其他模型；
- 20 题单次 pilot 足以支持普遍统计结论；
- 模型自报的发布日期已经等同于独立验证的 point-in-time 事实；
- 受控实验中的 100% 能直接外推到开放网页。

把这些边界写清楚不会削弱论文，反而能将工程演示升级为研究设计清晰、结论可信的 FYP 初版。
