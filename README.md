# FinSearchComp-Audit · ATLAS-XBRL

> **让大模型理解问题，让官方结构化数据提供事实，让程序负责计算。**

[![Live Result](https://img.shields.io/badge/Live_Result-ATLAS--XBRL-2563eb)](https://qiqiyzhu.github.io/FinSearchComp-Audit/)
[![Real LLM](https://img.shields.io/badge/Claude_Sonnet_5-40_valid_traces-7c3aed)](temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/README.md)
[![Accuracy](https://img.shields.io/badge/ATLAS--XBRL-100%25-15803d)](temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/metrics.json)
[![Tests](https://img.shields.io/badge/Tests-43_passing-0f766e)](https://github.com/QiQiyzhu/FinSearchComp-Audit/actions/workflows/finsearch-audit.yml)

## 一句话成果

在20道新的真实SEC财报计算题上，同一个 `claude-sonnet-5`：

| 完整系统 | 最终准确率 | 回答覆盖率 | 逐题胜/平/负 |
|---|---:|---:|---:|
| 普通 Web Search Agent | 75%（15/20） | 100% | — |
| **ATLAS-XBRL** | **100%（20/20）** | **100%** | **5 / 15 / 0** |

ATLAS-XBRL 相对普通搜索提升 **25个百分点**；配对bootstrap 95% CI为
**+5至+45个百分点**。正式批次共40/40条有效真实trace，0个transport error，0个invalid record。

- [在线结果首页](https://qiqiyzhu.github.io/FinSearchComp-Audit/)
- [20题正式研究卡](temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/README.md)
- [20题逐题结果](temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/case_outcomes.csv)
- [40条完整trace](temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/trace.jsonl)
- [运行前gold审计](temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/gold_audit.json)
- [冻结协议](docs/ATLAS_XBRL_20Q_PROTOCOL.md)

## 为什么普通搜索会错？

普通Agent必须在一次生成中同时完成：搜索10-K、识别正确财年和口径、抄取2–8个数、保持计算方向、
执行公式和四舍五入。正式实验中的5个错误分别来自：

1. “下降多少”返回了负号；
2. 两题先把中间比率四舍五入，再相减，产生0.01个百分点误差；
3. 一题跨公司利润率变化公式错误，偏差5.62个百分点；
4. 一题跨公司同比增长差提前舍入，产生0.01个百分点误差。

这些不是“模型完全不知道答案”，而是非结构化检索、数值抄取和多步计算被塞进同一条生成轨迹后，
错误会累积。

## ATLAS-XBRL 如何工作？

```mermaid
flowchart LR
    Q["自然语言财务问题"] --> L["Claude：语义编译"]
    L --> P["Label-free 金融程序校准"]
    P --> X["SEC Company Facts XBRL"]
    X --> V["截止日 / 10-K / 财年 / Taxonomy 校验"]
    V --> C["Decimal 白名单公式执行"]
    C --> A["答案 + Citation + Accession + 完整 Trace"]
```

系统支持五类程序：

- 单指标同比增长；
- 同公司利润率或研发强度变化；
- 跨公司比率差；
- 两家公司比率变化幅度之差；
- 两家公司同比增长率之差。

Claude不直接决定最终数值。它把问题编译成事实请求；系统再从SEC官方接口取得未经四舍五入的
10-K值，保存taxonomy tag、filing date、accession和响应SHA-256，最后通过Python `Decimal`
执行固定公式并统一保留两位小数。

## 公平性与防止“为了赢而改答案”

20题和参考程序在正式运行前提交于 `8aafe59`。Gold由独立脚本重新调用SEC Company Facts验证，
20/20全部通过，题集哈希为：

```text
b42764184a6a7f1c5f26acf324f81b3cfef70bb82940a67985f84a194f094fa6
```

运行时提示词不包含 `gold_answer`、`gold_calculation` 或 `reference_program`。评分采用题目要求的
两位小数精确相等和单位完全一致；没有依据普通Agent或ATLAS-XBRL的输出修改gold。

两种方法使用相同模型和medium推理强度，但工具能力不同：普通Agent使用真实Web Search；
ATLAS-XBRL使用SEC官方结构化接口。这是完整Agent系统比较，不是“只换Prompt”的消融。

## 实验规模

| 项目 | 普通 Agent | ATLAS-XBRL |
|---|---:|---:|
| 有效运行 | 20 | 20 |
| 模型HTTP阶段 | 40 | 20 |
| 外部工具动作 | 52次Web Search | 8次SEC下载 + 80次缓存命中 |
| 捕获来源 | 383 | 32个去重公司级来源记录 |
| Citation | 73条原生Web citations | 88条SEC事实citations |
| 输入 / 输出tokens | 100,296 / 56,821 | 12,838 / 5,388 |

ATLAS-XBRL不仅更准确，还把模型HTTP阶段从40降至20；不过SEC结构化接口是更强的任务专用工具，
因此不能把节省量解释为所有RAG任务上的通用成本优势。

## 从失败到成功的项目演进

| 阶段 | 真实结果 | 学到什么 |
|---|---|---|
| 第一轮时间Gate | 严格方法20%，普通搜索50% | 开放网页缺日期，硬拒答导致覆盖率崩溃 |
| 校准ATLAS-RAG | ATLAS 45%，普通搜索55% | 区分metadata unknown和明确冲突能减少过度拒答，但搜索仍不稳定 |
| ATLAS-Fusion | 最终55%持平，草稿65% | 多轨迹能找到互补证据，派生单位和Gate仍会丢掉正确答案 |
| 6题ATLAS-Compute开发实验 | Compute 50%，普通搜索66.7% | 公式程序化有效，但Web检索仍缺一手操作数，且方向规则不完整 |
| **20题ATLAS-XBRL正式实验** | **100% vs 75%** | 将语义、事实和计算拆开后，结构化工具消除了关键误差链 |

6题开发实验没有被删除或包装为成功结果，见
[ATLAS-Compute开发研究卡](temporal_clash/results/atlas_compute_micro_6q_sonnet5_20260813/README.md)。

## 顶会研究如何落到项目里

| 顶会工作 | 核心启发 | 本项目实现 |
|---|---|---|
| [Query Decomposition for RAG, EACL 2026](https://aclanthology.org/2026.eacl-long.322/) | 将复杂问题拆成互补子查询 | 把2–8个财务操作数编译成有序事实请求 |
| [FinMRAGBench, Findings of ACL 2026](https://aclanthology.org/2026.findings-acl.187/) | 真实财报需要跨页证据和多步金融分析 | SEC XBRL工具 + 多操作数计算trace |
| [Question Decomposition for RAG, ACL 2025](https://aclanthology.org/2025.acl-srw.32/) | 分解、分别检索、合并能改善多跳QA | 每个事实独立解析taxonomy与财年，再统一执行公式 |
| [ChainRAG, ACL 2025](https://aclanthology.org/2025.acl-long.1089/) | 渐进检索避免推理链丢失实体 | 程序显式保存公司、指标、年度和顺序 |
| [FinGEAR, Findings of EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.382/) | 金融披露需要领域结构和术语映射 | 财务概念到US-GAAP taxonomy的白名单路由 |
| [Sufficient Context, ICLR 2025](https://openreview.net/pdf?id=Jjr2Odj8DJ) | 区分上下文不足与模型使用失败 | Web缺数转向官方结构化工具，不用过度拒答掩盖失败 |

完整调研见[2024–2026顶会RAG路线](docs/TOP_CONFERENCE_RAG_2026.md)。本项目吸收设计原则，
不声称复现这些论文的训练过程或论文指标。

## 代码与审计产物

```text
temporal_clash/
├── atlas_xbrl.py                 # LLM编译、label-free校准、SEC取数、Decimal执行
├── run_xbrl_study.py             # 20×2真实实验、预算、trace与配对统计
├── audit_xbrl_cases.py           # 独立gold复核
├── xbrl_20q_cases.json           # 冻结20题与参考程序
├── test_atlas_xbrl.py            # 防泄漏、程序和SEC选择测试
└── results/
    ├── atlas_xbrl_20q_sonnet5_20260813/
    └── atlas_compute_micro_6q_sonnet5_20260813/
```

本地验证不消耗模型额度：

```bash
python -m unittest temporal_clash.test_detector temporal_clash.test_live_agent \
  temporal_clash.test_atlas_compute temporal_clash.test_atlas_xbrl \
  advanced_rag.test_advanced_rag -v
python -m temporal_clash.audit_xbrl_cases
python reproduce.py
```

正式runner默认只打印预算，不发送请求：

```bash
python -m temporal_clash.run_xbrl_study \
  --output-dir outputs/atlas_xbrl_plan
```

API密钥只从环境变量读取，未进入仓库、trace或GitHub Pages。

## 结论边界

可以声称：

- ATLAS-XBRL在这20道冻结的真实SEC数值推理题上达到100%，比同模型普通搜索高25个百分点；
- 结构化官方事实和确定性计算解决了方向、舍入、跨公司操作数和公式错误；
- 每个答案可以追溯到SEC taxonomy、10-K accession、操作数和公式。

不能声称：

- 已在所有开放域RAG或完整FinSearchComp上达到100%；
- 已复现或超过某篇顶会论文的公开基准结果；
- 20题单模型单次实验等价于通用SOTA。

下一步应增加第二模型、至少三次独立重复、更多公司和taxonomy，并在完全独立的新测试集验证
语义编译器对未见问题模板的泛化。

<details>
<summary><b>历史真实实验归档</b></summary>

- [第一轮Sonnet 20×4](temporal_clash/results/live_pilot_20q_claude_complete/README.md)
- [Haiku 20×4](temporal_clash/results/live_pilot_20q_claude_haiku_complete_20260813/README.md)
- [校准ATLAS-RAG 20×5](temporal_clash/results/live_pilot_20q_atlas_sonnet5_20260813/README.md)
- [ATLAS-Fusion 20题](temporal_clash/results/atlas_fusion_20q_sonnet5_20260813/README.md)

</details>

本仓库基于上游 [randomtutu/FinSearchComp](https://github.com/randomtutu/FinSearchComp)。
本项目不构成投资建议。
