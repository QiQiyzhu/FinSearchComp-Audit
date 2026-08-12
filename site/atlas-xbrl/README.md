# ATLAS-XBRL：20题真实模型 × SEC官方数据实验

这是项目当前的主实验。`claude-sonnet-5` 先把自然语言金融问题编译为受约束程序，
系统再通过 SEC Company Facts XBRL 官方接口取得未经四舍五入的10-K数值，最后用
Python `Decimal` 执行公式。普通搜索Agent使用同一模型和真实Web Search直接回答。

## 核心结果

| 方法 | 最终准确率 | 回答覆盖率 | 模型HTTP阶段 | 外部工具动作 |
|---|---:|---:|---:|---:|
| 普通搜索 Agent | 75.0% | 100.0% | 40 | 52 次Web Search |
| **ATLAS-XBRL** | **100.0%** | **100.0%** | 20 | 8 次SEC请求 + 80 次缓存命中 |

配对准确率差值为 **+25.0%**，逐题
5胜 / 15平 / 0负；
按题bootstrap 95% CI 为 +5.0% 到
+45.0%。

## 为什么它能超过普通搜索

普通搜索必须同时完成找报表、识别口径、抄取多个数、保持方向、计算和四舍五入，任一环节都可能出错。
ATLAS-XBRL把职责拆开：Claude只编译查询；label-free语法校准操作数顺序；SEC接口提供结构化原值；
Decimal程序只执行白名单公式。因此它不是“更强Prompt”，而是一个可审计的工具增强Agent。

## Gold重新审计

20个gold在运行前由独立 `reference_program` 重新调用SEC官方Company Facts验证，题集哈希为
`b42764184a6a7f1c5f26acf324f81b3cfef70bb82940a67985f84a194f094fa6`。运行时提示词明确排除 `gold_answer`、
`gold_calculation` 和 `reference_program`。评分采用题目要求的两位小数精确数值相等，不按任一策略输出改答案。

## 规模与异常

- 模型：`claude-sonnet-5`；有效运行：40/40；
- ATLAS-XBRL方法版本：`atlas-xbrl-1.0`；
- 正式运行传输/无效记录：0/0；
- 运行前开发实验曾暴露方向错误和二手来源拒答，本方法以SEC结构化工具解决，开发结果不计入本表；
- 本实验针对可映射到SEC XBRL的数值推理问题，不能外推到开放域所有问题。

## 文件

- `case_outcomes.csv`：20题逐题配对结果；
- `metrics.json`：聚合、胜平负和配对bootstrap；
- `trace.jsonl`：40条真实调用trace、模型计划、SEC字段、accession、响应哈希和公式；
- `gold_audit.json`：运行前20题gold的SEC复核；
- `study_manifest.json`：协议、提交、预算、题集哈希和排除记录；
- `../../xbrl_20q_cases.json`：冻结题集和不进入运行时的参考程序。
