# FinSearchComp-Audit：3分钟项目演示稿

## 演示目标

三分钟讲清楚四件事：

1. 普通搜索Agent在真实财报数值推理中会累积取数、方向、公式与舍入错误；
2. ATLAS-XBRL把语言理解、官方事实和确定性计算拆开；
3. 同一个Claude Sonnet 5、同一批20道冻结题上，准确率从75%提升到100%；
4. 题目、Gold、逐题输出、SEC来源和完整trace均可审计，结论有明确边界。

## 第1页：GitHub首页（约45秒）

打开：<https://github.com/QiQiyzhu/FinSearchComp-Audit>

可以直接说：

> 我的FYP研究的是可信金融研究Agent。早期方法依赖开放网页和严格证据Gate，但真实网页经常缺少日期与口径，
> 会导致过度拒答。新的ATLAS-XBRL让Claude只负责把问题编译成财务程序，从SEC官方XBRL取得事实，
> 再由Decimal程序完成计算。在20道正式题上，普通搜索Agent答对15题，ATLAS-XBRL答对20题，
> 配对结果是5胜、15平、0负。

马上说明边界：

> 这里的100%只属于这20道可映射到SEC XBRL的结构化数值题，不代表开放域RAG或完整FinSearchComp的通用SOTA。

## 第2页：方法（约55秒）

指向README中的流程图：

> 第一步，Claude识别公司、指标、财年和计算类型；第二步，label-free语法校准器固定操作数顺序和正负方向；
> 第三步，系统用截止日、10-K、财年和US-GAAP taxonomy从SEC Company Facts选值；第四步，
> Decimal按白名单公式计算，并且只在最后一步舍入；第五步，保存accession、响应哈希、操作数、公式和citation。

强调系统设计：

> 关键不是换一个更长Prompt，而是不让同一个生成模型同时承担搜索、抄数、公式和舍入。

## 第3页：20题逐题结果（约55秒）

打开：<https://qiqiyzhu.github.io/FinSearchComp-Audit/xbrl-study.html>

可以直接说：

> 普通Agent的5个错误很有代表性：一题把“下降多少”输出成负数，两题发生0.01个百分点的提前舍入误差，
> 一题跨公司利润率变化公式错误，另一题增长率差提前舍入。ATLAS-XBRL保留原始操作数和运算顺序，
> 因此这5题全部修复。它还把模型HTTP阶段从40降到20。

## 第4页：公平性与研究依据（约45秒）

打开Gold审计和研究卡：

- [Gold审计](../temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/gold_audit.json)
- [正式研究卡](../temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/README.md)
- [40条trace](../temporal_clash/results/atlas_xbrl_20q_sonnet5_20260813/trace.jsonl)

可以直接说：

> 20题和参考程序在正式运行前提交；独立脚本重新调用SEC复算Gold，20/20通过。
> 正式提示词不包含Gold、参考计算或参考程序，也没有根据两种方法的输出修改答案。
> 设计受到Query Decomposition、FinMRAGBench、ChainRAG、FinGEAR和Sufficient Context等顶会工作的启发，
> 但我不声称复现它们的训练方法或超过它们的公开benchmark。

## 老师可能追问

### 为什么这次能超过普通搜索？

因为任务本身有权威结构化数据。开放网页搜索仍让模型负责定位口径、抄数和多步计算；ATLAS-XBRL把事实层固定到SEC，
把计算层固定到可测试程序，模型只做最适合它的语义映射。改进来自系统分工，不是事后挑题或改Gold。

### 这是不是对普通Agent不公平？

这是“完整Agent系统”比较，不是只换Prompt的消融。两者使用相同模型和推理强度，但ATLAS-XBRL拥有任务专用工具。
研究问题正是：给金融Agent加入结构化官方工具和确定性执行后，能否比通用Web Search更可靠。README明确披露了这个差异。

### 20题够吗？

足以作为预注册的小规模工程验证，但不足以支持通用SOTA。下一步需要未见模板的新题、更多公司与taxonomy、
第二个模型以及至少三次独立重复。

### 为什么Gold可信？

每道题的Gold来自冻结参考程序，独立审计脚本通过SEC Company Facts重新取数和计算；结果保存taxonomy、10-K accession、
操作数、响应SHA-256和整批题集哈希。评分采用两位小数精确相等和单位一致。

## 收尾句

> 这个项目的核心成果不是“让模型更会猜数字”，而是把金融Agent从不可控的搜索生成链，升级成一个语义可编译、
> 事实可追溯、计算可复现、结果可审计的工具化系统。当前20题中，它在不牺牲覆盖率的情况下把准确率从75%提升到100%。
