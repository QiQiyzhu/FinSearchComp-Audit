# 给老师与面试官：FinAgent Terminal 的研究与产品迭代

**一句话介绍：这是一个可以切换历史日期、查询财务指标、追溯原始证据并重放模型实验的金融研究工作台。** 我把 Point-in-Time 问题从提示词要求，推进到数据版本、证据完整性和最终输出校验三个层面。

[直接体验工作台](https://qiqiyzhu.github.io/FinSearchComp-Audit/terminal/) · [方法与论文依据](TERMINAL_METHODS.md) · [冻结题集](../evals/terminal/manifest.json)

## 新增：一分钟联网研究演示

裸工作台入口现在优先展示“联网研究”。确认服务状态为已连接，输入“分析 Microsoft 最新披露的云业务需求与主要风险，并核对年度营业利润率”。任务提交后，可以展开实际执行轨迹：DeepSeek 拆解问题 → SEC 申报目录与全文搜索 → 读取官方原文 → 从本次取得的 CompanyFacts 计算 → 引用与时间检查 → 模型语义复核 → 生成报告。

点击报告中的文档引用查看逐字摘录与披露日期；点击财务引用查看公式及原始 XBRL 操作数；展开调用记录查看规划、归纳和核验凭据。报告可导出 Markdown / JSON，也可复制只读任务链接。服务断开、模型失败或额度耗尽时会明确提示，不用预制答案替代联网结果。

这条链路目前聚焦八家美股公司的 SEC 申报，配置补充搜索服务后可读取允许范围内的公司官网材料；不是全网新闻或实时行情终端。计算表支持已实现的年度口径，季度问题不会自动换成年值。正文语义复核仍可能出错，旧 60 题结构化成绩不等于新联网报告准确率。首次联网实跑的来源、调用次数和结果应以单独运行凭据为准。

## 三分钟演示

1. 在研究台选择公司、历史日期与财年，查看金额、比率及公式。比较 MSFT 与 AAPL 时留意实际财年结束日期，避免把同一个 FY 标签当作同一期间。
2. 将日期移到年报申报日前后，观察 `unavailable_as_of` 与 `available` 的变化。打开证据面板，核对 SEC 标签、申报日、期间、数值和来源链接。
3. 查询 NVIDIA 的纯 PP&E 自由现金流，展示“资料缺口”的处理：缺少规定口径时保留 `unknown`，不能为了给出答案而替换成含无形资产的支出。
4. 打开质量页，分别查看结构化查询与自然语言意图的首评、失败样例及修复后回归。进入时点评测，查看模型原始回答与系统门禁是否接受的区别。

## 已经测到了什么

首次代码冻结在 `9cfd3aa7ff8f1c196e2476f2e50053c3d78ef850`，随后才揭示保留题结果。原始首次代码已通过 Git archive 重放，76 题逐项输出与评分一致，记录见[重放凭据](verification/terminal-first-code-replay.json)。

| 评价对象 | 首次开发题 | 首次保留题 | 首次全部题 |
|---|---:|---:|---:|
| 结构化财务/PIT 查询，数值、期间、证据及状态全部通过 | 24/24 | 34/36 | **58/60，96.67%** |
| 自然语言意图解析，指标、年份、运算和支持范围正确 | 8/8 | 6/8 | **14/16，87.5%** |

结构化查询另有 47/48 个可回答数值正确、11/12 个拒答类型正确。每一道缺输出或拒答错误都保留在分母中。[结构化首次逐题记录](verification/terminal-structured-first.json)与[意图首次逐题记录](verification/terminal-nlq-first.json)包含请求、实际输出及评分项。

这两项成绩不能合并为“自然语言金融准确率 96.67%”。前者测结构化系统，后者只是一个很小的意图测试。它们都是项目自建题，覆盖固定公司与财报口径；没有第三方盲评，也不代表开放网页研究、行情预测或投资收益。

首评暴露了四个可解释的缺陷：缺少现金/资产比率、未来财年状态分错、现金转换比例被解析成两项原值、“上一年”被默默替换为默认年份。代码 `1c2a0cd` 修复后，同题[结构化回归为 60/60](verification/terminal-structured-regression.json)，[意图回归为 16/16](verification/terminal-nlq-regression.json)。这是看到失败后修复的成绩，不是新的保留题成绩，也不替换上述首评。

旧 36 次模型输出另做了[确定性门禁回放](verification/pit-gate-replay-v3.json)：申报前无证据支持的输出从 13/18 降为 0/18，但申报后的回答覆盖从 18/18 降为 12/18，因为 6 条纯记忆回答也被拦截。它展示了时间合规与覆盖率的取舍；模型没有重跑，不能称作模型准确率提升。

## 论文方法如何落到了代码

- 从 [FinFIRST](https://arxiv.org/abs/2609.25192) 借鉴原子评分，将“数字正确”和“证据链完整”分开；最终严格通过要求所有相关条件成立。
- 从 [ExAnte](https://aclanthology.org/2026.eacl-long.72/) 和 [HindsightBench](https://arxiv.org/abs/2607.18867) 借鉴时间合规与模型记忆分离的实验思路，保留原始模型输出，不把系统拒绝等同于模型学会推理。
- 从 [EvidenceLoop](https://openreview.net/pdf?id=x4zQDewgHr) 借鉴证据 ID 与主张验证；本项目先把它落实为可以独立复算的结构化财务证据校验。

这些是方法借鉴与工程实现，尚不是对论文的完整复现，也不是新的算法领先性结论。不同论文的方法、完整实现与当前边界见[方法表](TERMINAL_METHODS.md)。

## 希望进一步研究的问题

**在固定模型、固定历史证据和固定记忆使用许可下，显式可用性类型与完整证据门禁，能否降低时间不合规输出，同时保持可回答覆盖率？**

我希望下一阶段补真实重述和修订事件，按公司或披露事件分组划分测试集，请老师帮助审阅财务定义与题目。随后将同样的时间版本协议扩展到新闻和研报，形成按月/周组织的题集。对模型知识时间截面的判断，需要更多时间点、日期线索对照和记忆探针，不能从当前小试点推断训练截止日。

## 复现入口

```bash
python -m unittest discover -s evals/terminal -p 'test_*.py' -v
python scripts/evaluate_terminal.py --split all --label local-regression --output build/terminal-local-regression.json
python scripts/evaluate_terminal_nlq.py --split all --label local-regression --output build/terminal-local-nlq.json
python scripts/replay_terminal_first.py --output build/terminal-first-replay.json
python scripts/verify_terminal_release.py
```

评测输出采用新文件名，不覆盖历史成绩。上述主评测与原码重放不调用付费模型；网页静态体验也无需访问者提供 API 密钥。连接后端的真实 DeepSeek 调用单独记录，不纳入这 60 题系统准确率。
