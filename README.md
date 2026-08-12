# FinSearchComp-Audit · ATLAS-PIT-XBRL

> **让答案正确，也让每条证据在历史截止日之前真实可用。**

[![Live Result](https://img.shields.io/badge/Live-ATLAS--PIT--XBRL-2563eb)](https://qiqiyzhu.github.io/FinSearchComp-Audit/)
[![Real LLM](https://img.shields.io/badge/Claude_Sonnet_5-40_valid_traces-7c3aed)](temporal_clash/results/atlas_pit_xbrl_20q_sonnet5_20260813/README.md)
[![Accuracy](https://img.shields.io/badge/Accuracy-65%25_to_100%25-15803d)](temporal_clash/results/atlas_pit_xbrl_20q_sonnet5_20260813/metrics.json)
[![PIT](https://img.shields.io/badge/Final_future_evidence-7_to_0-0f766e)](temporal_clash/results/atlas_pit_xbrl_20q_sonnet5_20260813/metrics.json)
[![Tests](https://img.shields.io/badge/Tests-47_passing-0f766e)](https://github.com/QiQiyzhu/FinSearchComp-Audit/actions/workflows/finsearch-audit.yml)

## 一句话成果

在20道全新的历史截止日SEC财报计算题上，使用同一个 `claude-sonnet-5`：

| 完整系统 | 数值准确率 | 回答覆盖率 | 候选未来来源 | 最终未来证据 | 联合可靠回答率 |
|---|---:|---:|---:|---:|---:|
| 普通 Web Search Agent | 65%（13/20） | 90% | 80/392 | 7/55 | 0% |
| **ATLAS-PIT-XBRL** | **100%（20/20）** | **100%** | **0/32** | **0/84** | **100%** |

数值逐题为 **7胜 / 13平 / 0负**，提升 **35个百分点**，配对bootstrap 95% CI为
**+15至+55个百分点**。普通Agent在19/20题的候选搜索结果中暴露确认的未来来源，并在6/20题
最终采用未来证据；ATLAS-PIT-XBRL两项均为0。

“联合可靠回答”要求答案正确，并且最终证据全部有日期、不晚于题目截止日、来源字段完整。
未知日期不算确认未来，但也不再被包装成确认安全。

- [在线结果首页](https://qiqiyzhu.github.io/FinSearchComp-Audit/)
- [20题答案与时间审计页](https://qiqiyzhu.github.io/FinSearchComp-Audit/xbrl-study.html)
- [正式研究卡](temporal_clash/results/atlas_pit_xbrl_20q_sonnet5_20260813/README.md)
- [逐题结果](temporal_clash/results/atlas_pit_xbrl_20q_sonnet5_20260813/case_outcomes.csv)
- [40条有效记录 + 1条排除记录](temporal_clash/results/atlas_pit_xbrl_20q_sonnet5_20260813/trace.jsonl)
- [Gold独立审计](temporal_clash/results/atlas_pit_xbrl_20q_sonnet5_20260813/gold_audit.json)
- [运行前协议与传输修订](docs/ATLAS_PIT_XBRL_20Q_PROTOCOL.md)

## 为什么旧的“泄漏为0”不够严格？

旧实验只把最终证据中“已经明确标注日期且晚于截止日”的记录计为泄漏。开放网页经常没有日期，
因此 `published_at=null` 会落在指标之外，看起来仍像“泄漏为0”。

新的独立 `pit-audit-1.0` 同时检查三层：

1. 搜索引擎返回的全部候选来源；
2. 模型原生引用；
3. 最终答案实际采用的证据。

它使用供应商返回的 `page_age` 与证据URL匹配日期，并把每条记录分为：截止日前、截止日后、日期未知。
普通Agent的候选来源时间元数据覆盖率为30.6%，ATLAS为100%；最终证据时间元数据覆盖率分别为
36.4%和100%。

其中Web来源日期是搜索供应商观察到的页面时间元数据，不等同于对所有网页完成独立取证；因此项目把它表述为
“供应商元数据确认的未来来源”。SEC filing date则来自官方Company Facts记录。

## ATLAS-PIT-XBRL 如何工作？

```mermaid
flowchart LR
    Q["自然语言问题 + 历史截止日"] --> L["Claude：语义编译"]
    L --> P["Label-free程序校准"]
    P --> X["SEC Company Facts"]
    X --> V{"filed ≤ cutoff?"}
    V -->|否| R["拒绝未来申报值"]
    V -->|是| C["Decimal白名单公式"]
    C --> A["答案 + Filing date + Accession + Citation"]
    A --> T["候选 / 引用 / 最终证据三层PIT审计"]
```

- Claude只识别公司、财务指标、财年与公式类型，不直接决定最终数字；
- 程序只选择截止日前已经提交的10-K年度事实；
- 每个事实保存US-GAAP taxonomy、filing date、accession和SEC响应SHA-256；
- Python `Decimal`执行固定公式，只在最后一步统一舍入；
- 日期缺失单独计为unknown，不算作确认安全。

## 公平性与防止事后改答案

20题、Gold、日期解析器、联合评分和六项成功条件在正式运行前冻结于提交 `7a138a3`。
Gold由独立程序重新调用SEC Company Facts复算，20/20通过，整批题集哈希为：

```text
a11e93b6974a2dd5d41c0e5de590e4dedaafc317827f2de9c34dd0f6c467172a
```

正式提示词不含 `gold_answer`、`gold_calculation` 或 `reference_program`，没有依据任一方法输出修改Gold。
第21个单元遇到一次HTTP 524，协议在 `ffda118` 记录“仅替代一次、累计尝试上限60→61”；原错误永久保留，
最终得到40/40条有效记录和1条公开排除记录，0条invalid。

这是完整Agent系统比较：两种方法使用同一模型和推理强度，但普通Agent使用通用Web Search，
ATLAS使用任务专用SEC工具。它不是“只换Prompt”的消融。

## 实验规模

| 项目 | 普通 Agent | ATLAS-PIT-XBRL |
|---|---:|---:|
| 有效运行 | 20 | 20 |
| 模型HTTP阶段 | 40 | 20 |
| 外部工具动作 | 50次Web Search | 16次SEC下载 + 68次缓存命中 |
| 候选来源 | 392 | 32个公司级SEC来源记录 |
| Citation | 83条原生Web citations | 84条SEC事实citations |
| 输入 / 输出tokens | 103,332 / 56,560 | 13,006 / 5,412 |
| 最终来源字段完整率 | 14.5% | 100% |

## 项目从失败到综合改进

| 阶段 | 真实结果 | 研究结论 |
|---|---|---|
| 2026-07 时间Gate | 严格方法20%，普通搜索50% | 网页缺日期导致过度拒答 |
| 校准ATLAS-RAG | ATLAS 45%，普通搜索55% | unknown与violation分开能改善Gate，但仍未超过普通搜索 |
| ATLAS-Fusion | 最终55%持平，草稿65% | 多轨迹找到互补证据，但Gate会丢掉正确答案 |
| ATLAS-Compute开发实验 | 50%，普通搜索66.7% | 程序化公式有效，Web仍缺一手操作数 |
| ATLAS-XBRL正式实验 | 100%，普通搜索75% | 官方结构化事实 + 程序计算解决数值误差 |
| **ATLAS-PIT-XBRL综合实验** | **准确率100% vs 65%；最终未来证据0 vs 7** | **准确率、覆盖率、搜索时间安全与最终证据安全同时改善** |

早期负结果没有删除，见[历史真实实验归档](#历史真实实验归档)。

## 顶会研究如何落地

| 顶会工作 | 本项目吸收的思想 | 工程实现 |
|---|---|---|
| [Query Decomposition for RAG, EACL 2026](https://aclanthology.org/2026.eacl-long.322/) | 复杂问题分解 | 2–8个有序财务事实请求 |
| [FinMRAGBench, Findings ACL 2026](https://aclanthology.org/2026.findings-acl.187/) | 财报多步工具推理 | SEC事实、accession、公式trace |
| [ChainRAG, ACL 2025](https://aclanthology.org/2025.acl-long.1089/) | 保持多跳实体链 | 显式公司、指标、期间和操作数顺序 |
| [FinGEAR, Findings EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.382/) | 金融结构和术语映射 | US-GAAP taxonomy白名单路由 |
| [Sufficient Context, ICLR 2025](https://openreview.net/pdf?id=Jjr2Odj8DJ) | 区分上下文不足与使用失败 | unknown日期不计为安全，切换官方工具 |
| [FreshQA / FreshLLMs, Findings ACL 2024](https://aclanthology.org/2024.findings-acl.813/) | 动态知识新鲜度 | 从“现在正确”推进到“历史时点可用” |

本项目吸收设计原则，不声称复现这些论文的训练过程或公开指标。

## 代码与复现

```text
temporal_clash/
├── atlas_xbrl.py                 # LLM编译、SEC截止日取数、Decimal执行
├── pit_audit.py                  # 候选/引用/最终证据独立时间审计
├── run_xbrl_study.py             # 20×2真实实验、联合指标与bootstrap
├── audit_xbrl_cases.py           # 独立Gold复核
├── audit_pit_traces.py           # 已有trace的PIT重审CLI
├── pit_xbrl_20q_cases.json       # 冻结的新20题
└── results/atlas_pit_xbrl_20q_sonnet5_20260813/
```

本地验证不消耗模型额度：

```bash
python -m unittest temporal_clash.test_detector temporal_clash.test_live_agent \
  temporal_clash.test_atlas_compute temporal_clash.test_atlas_xbrl \
  temporal_clash.test_pit_audit advanced_rag.test_advanced_rag -v
python -m temporal_clash.audit_xbrl_cases \
  --case-file temporal_clash/pit_xbrl_20q_cases.json
python reproduce.py
```

## 结论边界

可以声称：

- 在这20道冻结的历史SEC数值题上，ATLAS同时提高准确率与覆盖率；
- 它把候选未来来源从80降到0、最终未来证据从7降到0；
- 每个最终事实都能追溯到截止日前10-K、taxonomy、accession、操作数和公式。

不能声称：

- 已在开放域所有RAG任务或完整FinSearchComp上达到100%；
- 已复现或超过某篇顶会论文的公开benchmark；
- 20题、单模型、单次运行等价于通用SOTA。
- 搜索供应商的 `page_age` 等价于对网页首次发布时间的完整第三方取证。

下一步需要第二模型、至少三次独立重复、更多公司与taxonomy，以及完全未见的问题表达和任务类型。

## 历史真实实验归档

- [第一轮Sonnet 20×4](temporal_clash/results/live_pilot_20q_claude_complete/README.md)
- [校准ATLAS-RAG 20×5](temporal_clash/results/live_pilot_20q_atlas_sonnet5_20260813/README.md)
- [ATLAS-Fusion 20题](temporal_clash/results/atlas_fusion_20q_sonnet5_20260813/README.md)
- [ATLAS-Compute 6题开发实验](temporal_clash/results/atlas_compute_micro_6q_sonnet5_20260813/README.md)
- [ATLAS-XBRL 20题数值实验](temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/README.md)

API密钥只从环境变量读取，未进入仓库、trace或GitHub Pages。本项目不构成投资建议。
