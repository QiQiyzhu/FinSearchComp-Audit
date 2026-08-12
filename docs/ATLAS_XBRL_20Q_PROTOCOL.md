# ATLAS-XBRL 20题真实实验协议（运行前冻结）

版本：`atlas-xbrl-study-1.0` / `atlas-xbrl-1.0`

## 目标

验证在可映射到SEC XBRL的精确金融数值问题上，`大模型语义编译 + 官方结构化数据工具 +
白名单公式执行` 是否能稳定超过直接使用Web Search作答的普通Agent。

## 20题与gold

- 题集：`temporal_clash/xbrl_20q_cases.json`；
- 公司：Microsoft、Apple、NVIDIA、Tesla、Alphabet、Meta、Intel、AMD；
- 类型：4题研发费用增长、4题营业利润率变化、4题跨公司研发强度差、4题跨公司比率变化差、
  4题跨公司增长率差；
- 所有问题要求使用10-K中未经四舍五入的美元值，并保留两位小数；
- `reference_program` 只用于运行前gold审计，不进入任一运行时提示词；
- `python -m temporal_clash.audit_xbrl_cases` 必须在正式运行前对20个gold全部通过；
- gold只能因SEC一手值、公式方向或单位审计证据而修订，禁止按策略输出修订。

## 两种方法

普通搜索Agent：

1. `claude-sonnet-5` 强制真实Web Search；
2. 输出带原生citation的研究备忘录；
3. 同模型将备忘录归一化为结构化答案。

ATLAS-XBRL：

1. `claude-sonnet-5` 将自然语言编译为XBRL事实请求和公式类型；
2. label-free金融语法根据问题中的公司、期间和指标校准程序；
3. SEC Company Facts API按cutoff、10-K、年度持续期和taxonomy tag取原始值；
4. Python `Decimal`执行白名单公式并用`ROUND_HALF_UP`保留两位；
5. 保存模型计划、校准程序、taxonomy、accession、SEC响应哈希、操作数和完整公式。

## 控制与预算

| 条件 | 普通搜索Agent | ATLAS-XBRL |
|---|---|---|
| 模型 | Claude Sonnet 5 | Claude Sonnet 5 |
| 推理强度 | medium | medium |
| 模型HTTP阶段/题 | 2 | 1 |
| 外部工具 | 最多3次Web Search | SEC Company Facts，运行级缓存 |
| 最大输出 | 每阶段4,800 tokens | 4,800 tokens |
| 运行顺序 | 按题循环平衡 | 按题循环平衡 |

正式实验为20题×2方法=40条有效trace，计划60个成功模型HTTP阶段；不重试。SEC工具按8家公司缓存，
预计最多8次真实SEC JSON下载，其余事实读取为可审计缓存命中。两种方法工具能力不同，这是对完整系统的比较，
不是只比较Prompt。

## 评分和成功判据

- `action=answer`；
- unit与题目完全一致；
- 数值与重新审计的两位小数gold精确相等；
- 主指标：20题最终准确率；
- 同时报告覆盖率、逐题胜/平/负、配对bootstrap 95%区间、模型请求、工具动作、来源与citations；
- 只有ATLAS-XBRL准确率严格高于普通Agent，GitHub首页才写“超过普通搜索”。

## 边界

这是针对SEC XBRL可表达的结构化数值推理切片。它能验证工具增强与程序执行的价值，但不能外推为开放网页、
宏观数据、市场行情或所有RAG问题上的普遍领先。此前6题ATLAS-Compute实验属于开发诊断，不并入正式分数。
