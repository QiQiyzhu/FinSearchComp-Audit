# 2024–2026 顶会 RAG 新技术调研与 FYP 落地选择

> 检索与核对日期：2026-08-13。优先使用 ACL Anthology、NeurIPS Proceedings 和 OpenReview
> 的正式会议页面。本文只把正式接收论文称为顶会论文，不把普通 arXiv 预印本包装成顶会成果。

## 一、先讲结论

“传统 RAG 太老”通常不是说检索增强本身没价值，而是说下面这个静态流水线研究空间已经很拥挤：

```text
切块 → 向量化 → Top-K 相似度 → 拼接 Prompt → LLM 回答
```

2024–2026 年值得关注的变化可以压缩为六条：

1. **从固定检索到自适应检索**：决定是否检索、用哪个检索器、检索几轮；
2. **从一次检索到推理—检索反馈循环**：答案生成过程中发现信息缺口再查；
3. **从单一相关性到多目标排序**：相关性之外加入新鲜度、来源质量、生成效用；
4. **从独立文本块到图结构**：显式表达实体、事件、多跳关系和冲突；
5. **从无条件信任上下文到冲突仲裁**：处理检索文档之间及文档与模型记忆之间的冲突；
6. **从最终准确率到模块化诊断与选择性回答**：分别评估检索、引用、生成、拒答和成本。
7. **从自由文本计算到结构化工具执行**：让LLM规划查询，由数据库/API提供原始事实，再由受限程序完成数值推理。

对本 FYP 最适合的不是训练一个大模型或照搬通用 GraphRAG，而是围绕已有的“金融时点可靠性”形成：

> **自适应来源路由 + 相关性/时间双通道检索 + 事实级冲突图 + 纠错检索 + 选择性回答。**

这条路线与当前开放网页实验暴露的“元数据缺失导致过度拒答”直接相连，也能做清晰消融。

## 二、重点论文

### A. 2026：直接影响本项目的新进展

| 论文 | 会议信息 | 核心思想 | 对本项目的启发 |
|---|---|---|---|
| [Re³: Relevance & Recency Retrieval for Mitigating Temporal Hallucination](https://aclanthology.org/2026.acl-long.1180/) | ACL 2026 Long | 分离相关性与时间信号，并过滤过时事实版本 | 不要等生成后才检查日期；在检索排序阶段就纳入截止日与版本 |
| [R³AG: Retriever Routing for Retrieval-Augmented Generation](https://aclanthology.org/2026.acl-long.939/) | ACL 2026 Long | 路由时同时考虑检索质量与对生成的实际效用 | 公司财报、行情、宏观数据不应都走 Web Search |
| [Retrieval as Generation / GRIP](https://aclanthology.org/2026.acl-long.196/) | ACL 2026 Long | 在生成轨迹中决定何时检索、如何改写查询、何时停止 | 把一次性 Gate 改为低置信度触发的纠错检索循环 |
| [E²RAG: Respecting Temporal-Causal Consistency](https://aclanthology.org/2026.eacl-long.90/) | EACL 2026 Long | 用实体图和事件图保留时间、因果和演化上下文 | 财务数值不能只挂在“公司”节点上，还要绑定财年、发布日期和版本 |
| [When Facts Change](https://aclanthology.org/2026.findings-acl.103/) | ACL 2026 Findings | 模型识别到事实会变化，不代表能正确处理新旧冲突 | 不能只靠 Prompt 提醒；需要可执行的时间验证和冲突处理 |
| [SPARKLE](https://aclanthology.org/2026.acl-long.1793/) | ACL 2026 Long | 用结构化、可插拔控制器规划图推理与检索 | 把来源路由和检索状态独立于底层 LLM，保留 PLAN trace |
| [ReflectiveRAG](https://aclanthology.org/2026.eacl-industry.27/) | EACL 2026 Industry | 反思证据充分性并迭代改写查询，同时去除冗余噪声 | 缺少操作数或一手来源时继续搜索，而不是立刻拒答 |
| [RouteRAG](https://aclanthology.org/2026.findings-acl.1502/) | ACL 2026 Findings | 在文本、图检索、继续推理和最终回答之间自适应路由 | 金融问题按结构化行情、申报文件和官方网页选择不同路径 |
| [Query Decomposition for RAG](https://aclanthology.org/2026.eacl-long.322/) | EACL 2026 Long | 将复杂问题分解为子查询并动态平衡探索与利用 | 将财务公式拆成2–8个有序操作数请求，逐项获取可验证事实 |
| [FinMRAGBench](https://aclanthology.org/2026.findings-acl.187/) | ACL 2026 Findings | 真实财报需要跨页/跨文档证据和多步金融分析工具 | 把财报问答落到SEC XBRL、accession与程序化计算trace |

### B. 2025：冲突与不完美检索

| 论文 | 会议信息 | 核心思想 | 对本项目的启发 |
|---|---|---|---|
| [Astute RAG](https://aclanthology.org/2025.acl-long.1476/) | ACL 2025 Long | 迭代整合模型内部知识和外部来源，按可靠性解决冲突 | 不把所有检索结果等价拼接；进行来源感知的 listwise 仲裁 |
| [FaithfulRAG](https://aclanthology.org/2025.acl-long.1062/) | ACL 2025 Long | 在事实粒度显式建模模型记忆与上下文冲突 | 把“整段答案”拆成可审计事实和对应证据 |
| [SeCon-RAG](https://proceedings.neurips.cc/paper_files/paper/2025/hash/668563ef18fbfef0b66af491ea334d5f-Abstract-Conference.html) | NeurIPS 2025 Main | 两阶段语义过滤与冲突过滤，避免过度删除有用证据 | 先相关性/来源过滤，再做字段与数值冲突过滤 |
| [GFM-RAG](https://proceedings.neurips.cc/paper_files/paper/2025/hash/33ca0b1102b54c191a9a45a05adafaf4-Abstract-Conference.html) | NeurIPS 2025 Main | 图基础模型在未见数据集上做图检索 | 后续可把手工图升级为可学习图检索，但当前 FYP 不必承担大规模训练成本 |
| [HyperGraphRAG](https://proceedings.neurips.cc/paper_files/paper/2025/hash/df55ee6e59f8ac4a625219e11fe9ddba-Abstract-Conference.html) | NeurIPS 2025 Main | 用超边表达多元关系 | 金融事实天然是“公司—指标—期间—版本—单位—发布日期”的多元关系 |
| [Question Decomposition for RAG](https://aclanthology.org/2025.acl-srw.32/) | ACL 2025 Student Research Workshop | 分解复杂问题、分别检索并合并重排，多跳问答优于标准RAG | 每个公司/指标/年度独立取数，避免操作数只在不同文档中出现时漏检 |
| [ChainRAG](https://aclanthology.org/2025.acl-long.1089/) | ACL 2025 Long | 渐进检索与查询改写，减少多跳链条中的实体丢失 | 程序显式保存公司、财务指标、期间和操作数顺序 |
| [FinGEAR](https://aclanthology.org/2025.findings-emnlp.382/) | EMNLP 2025 Findings | 用金融术语映射和财报层级结构改善10-K检索 | 以US-GAAP taxonomy白名单替代扁平网页文本检索 |

### C. 2024：高级 RAG 的基础路线

| 论文 | 会议信息 | 核心思想 | 对本项目的启发 |
|---|---|---|---|
| [Self-RAG](https://openreview.net/forum?id=hSyW5go0v8) | ICLR 2024 | 按需检索并用 reflection token 自我评价 | 检索不应无条件发生，生成也应接受证据质量反馈 |
| [Adaptive-RAG](https://aclanthology.org/2024.naacl-long.389/) | NAACL 2024 Long | 根据问题复杂度选择不检索、单步或多步检索 | 简单单事实与跨期计算题使用不同检索预算 |
| [DRAGIN](https://aclanthology.org/2024.acl-long.702/) | ACL 2024 Long | 根据生成过程中的信息需求决定何时和检索什么 | 失败原因应转成新的查询，而不是直接结束 |
| [HippoRAG](https://proceedings.neurips.cc/paper_files/paper/2024/hash/6ddc001d07ca4f319af96a3024f6dbd1-Abstract-Conference.html) | NeurIPS 2024 Main | 知识图谱 + Personalized PageRank 支持高效多跳检索 | 图结构适合跨报表、跨期间和多来源推理 |
| [RAGChecker](https://proceedings.neurips.cc/paper_files/paper/2024/hash/27245589131d17368cccdfa990cbf16e-Abstract-Datasets_and_Benchmarks_Track.html) | NeurIPS 2024 D&B | 分别诊断检索和生成模块 | 不能只报答案准确率；需报告 Recall、MRR、冲突、引用和覆盖率 |
| [CRAG Benchmark](https://proceedings.neurips.cc/paper_files/paper/2024/hash/1435d2d0fca85a84d83ddcb754f58c29-Abstract-Datasets_and_Benchmarks_Track.html) | NeurIPS 2024 D&B | 评测检索、总结、Web/KG 与端到端 RAG | 金融 Agent 应保留模块级 trace 和不同来源通道对照 |

### D. 选择性回答与证据充分性

| 论文 | 会议信息 | 核心思想 | 对本项目的启发 |
|---|---|---|---|
| [Sufficient Context](https://openreview.net/pdf?id=Jjr2Odj8DJ) | ICLR 2025 | 区分“检索上下文不足”和“模型没有正确使用足够上下文”，并据此选择性回答 | `metadata_unknown` 不等于违规；先判断证据是否足以支撑答案，再决定回答或拒答 |
| [Abstention in LLMs: A Survey](https://aclanthology.org/2025.tacl-1.26/) | TACL 2025 | 系统整理拒答动机、方法与评价 | 同时报告准确率、覆盖率和 risk–coverage，不能只追求拒答后的选择性准确率 |

## 三、为什么没有直接选择“大模型 GraphRAG 训练”？

1. FYP 的独特问题是金融 point-in-time 可靠性，不是通用多跳 QA；
2. 当前只有 20 个基础问题，训练 GNN 或大规模 dense retriever 容易变成数据不足的演示；
3. 老师更容易认可“问题—方法—消融—结果”闭环，而不是堆叠复杂框架；
4. 可解释规则可以作为以后学习模型的强基线，并让错误分析更可信；
5. 服务端面试更看重清晰边界、接口、缓存、并发、容错和评测意识。

## 四、项目落地优先级

### 已实现：P0

- ATLAS-RAG 自适应来源路由；
- BM25、确定性向量特征和时间/来源效用融合；
- 事实级冲突图与 listwise 仲裁；
- 低置信度纠错检索和选择性回答；
- 分层离线评测、完整 trace、HTTP 服务、缓存、健康检查和 CI。
- ATLAS-XBRL：LLM语义编译、label-free程序校准、SEC Company Facts取数、Decimal白名单公式执行；
- 20道真实SEC计算题的运行前gold审计、40条真实Claude trace和逐题配对bootstrap。

### 下一阶段：P1

- 独立解析网页 `datePublished`、`dateModified`、JSON-LD、OpenGraph 和 HTTP Header；
- SEC、BLS、BEA、FRED、公司财报等来源适配器；
- 区分 `metadata_unknown` 与真实 `violation`，减少过度拒答；
- 在独立开发集校准置信度阈值并绘制 risk-coverage curve。

### 2026-08-13 真实验证更新

- 完成 `claude-sonnet-5` 的 20 题 × 5 策略同轮实验，共 100 条严格有效 trace；
- 单轨迹 ATLAS 最终准确率 45%，高于同轮完整验证器 35%，但低于普通 Agent 55%；
- 完成 ATLAS-Fusion：复用每题五条真实搜索轨迹做 listwise 仲裁，不新增搜索；
- Fusion 最终准确率 55%，与普通 Agent 持平；仲裁初稿 65%，达到五轨迹 oracle 上限；
- 该负结果把下一步从泛泛的“继续优化 RAG”收敛到两个可检验问题：派生答案的证据单位兼容，
  以及在新开发集上校准拒答风险；不能继续用同一 20 题调参后再当作独立测试。

### 2026-08-13 ATLAS-XBRL 正式更新

- 先运行6题ATLAS-Compute开发实验：普通搜索66.7%，Compute 50%；公开保留该负结果；
- 失败诊断显示，程序化公式能修复NVIDIA利润率题，但自由Web检索仍缺一手操作数，且“下降多少”方向规则不完整；
- 随后冻结20道新的SEC XBRL计算题，并用独立`reference_program`在运行前审计20/20个gold；
- 正式使用`claude-sonnet-5`运行20题×2方法，共40/40条有效trace；
- 普通Web Search Agent达到75%（15/20），ATLAS-XBRL达到100%（20/20）；
- 配对提升+25个百分点，逐题5胜/15平/0负，配对bootstrap 95% CI为+5至+45个百分点；
- ATLAS-XBRL不是继续堆Prompt：Claude只负责编译，事实来自SEC Company Facts，数值由Decimal程序执行；
- 结果适用于可映射到SEC XBRL的结构化金融数值推理，不外推为开放域RAG SOTA。

### 投稿级：P2

- BGE/E5 dense encoder 与学习型路由器；
- 至少三次独立实网重复、第二模型和第二检索后端；
- 更大问题集、人工双人盲审、标注一致性；
- 预注册主要指标、停止条件和排除规则。

## 五、老师面前应如何描述

推荐表述：

> 我没有再做静态 Top-K RAG，而是把最新论文中的自适应路由、时间感知检索、事实级冲突建模、
> 纠错检索和选择性回答组合到金融 point-in-time 场景。当前实现是论文启发的可解释原型，
> 不是对某一篇论文的复现。它先用受控数据验证机制，再用真实 Web Search 检查外部有效性。

不要说：

- “我复现了 Re³ / R³AG”（没有使用其训练和权重）；
- “ATLAS-RAG 已经超过 SOTA”（没有同一公开基准上的公平对比）；
- “100% 证明真实系统可靠”（已回答样本的离线受控结果不能外推）；
- “用了向量就一定理解语义”（当前默认是确定性 HashVector 基线）。
