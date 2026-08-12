# ATLAS-Compute 真实微型实验协议（运行前冻结）

协议版本：`atlas-compute-micro-1.0`

## 研究问题

在需要从真实10-K中取出多个原始数值再计算的金融问题上，把“自由文本直接作答”改成
“问题分解 → 操作数检索 → 受约束计算计划 → Decimal确定性执行”，能否超过相同模型和
相同请求预算下的普通搜索Agent？

## 方法依据

- EACL 2026《Query Decomposition for RAG》：复杂问题拆成子问题并动态选择有信息量的检索；
- ACL 2025《Question Decomposition for RAG》：分解、分别检索、合并与重排可改善多跳问答；
- ACL 2025《ChainRAG》：逐步补全缺失实体，避免推理链在检索阶段丢失；
- Findings of ACL 2026《FinMRAGBench》：真实财报问答需要跨页证据、多步金融分析和动态工具调用；
- Findings of EMNLP 2025《FinGEAR》：金融10-K的层级结构和术语需要领域化检索。

本项目只吸收上述设计原则，不声称复现论文模型或论文数值。

## 冻结题集

`temporal_clash/compute_micro_cases.json` 包含6道此前未运行的新题。它们全部要求使用
一手SEC 10-K中未经四舍五入的百万美元操作数，最终答案保留两位小数。题目覆盖：

1. 精确同比增长；
2. 同公司跨年度比率差；
3. GAAP营业利润率变化；
4. 跨公司研发强度差；
5. 跨公司、不同财年结束日的研发强度差；
6. 报表内SG&A强度变化。

这是观察旧pilot的数值推理失败后设计的 targeted challenge set，不是随机总体样本，也不是从旧20题中
挑选普通Agent失败的题。题集、gold计算和参考来源必须在真实调用前提交；运行时提示词不得读取
`gold_answer`、`gold_calculation` 或 `reference_sources`。

## 公平控制

| 条件 | 普通搜索Agent | ATLAS-Compute |
|---|---|---|
| 模型 | `claude-sonnet-5` | `claude-sonnet-5` |
| 推理强度 | medium | medium |
| 每题最大Web Search | 3 | 3 |
| HTTP阶段 | 搜索研究 + 结构化 | 搜索操作数 + 结构化计算计划 |
| 最大输出 | 每阶段4,800 tokens | 每阶段4,800 tokens |
| 题序 | 相同 | 相同 |
| 策略顺序 | 按题循环平衡 | 按题循环平衡 |

正式实验共 `6题 × 2策略 = 12` 条运行，对应24个成功HTTP阶段；不重试，批准上限24次请求。

## ATLAS-Compute执行约束

- `relative_change_percent` 仅执行 `(current / prior - 1) × 100`；
- `difference_of_ratios_pp` 仅执行 `(left_num / left_den - right_num / right_den) × 100`；
- 所有操作数URL必须来自本轮搜索完整来源或原生citation；
- 明确晚于cutoff的操作数被拒绝；
- 操作数数量、数值格式或引用验证失败即拒答；
- Python `Decimal` + `ROUND_HALF_UP` 统一保留两位小数；
- 模型不直接决定最终数值，最终值由受限程序执行。

## 成功判据与披露

主要指标是6题上的最终准确率；同时报告覆盖率、逐题胜/平/负、搜索次数、HTTP阶段、来源、原生引用和
配对bootstrap区间。只有当ATLAS-Compute准确率高于同批普通Agent时，首页才称为“微型实验中超过”；
无论结果如何都保留trace。由于样本只有6题，结果只能证明这个定向切片中的可行性，不能替代独立扩大实验。
