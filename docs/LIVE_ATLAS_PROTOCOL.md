# ATLAS-RAG 真实 Web Search 对照实验（冻结协议）

> 协议冻结日期：2026-08-13；方法版本：`live-atlas-1.0`。本文件在完整运行前创建，
> 用来区分事先确定的方法与看到结果后的解释。

## 1. 研究问题

第一轮 Claude Sonnet 5 的 20 题 × 4 策略实验发现：严格过滤虽然没有采用模型声明的
未来证据，却把缺少网页发布日期视为违规，造成明显过度拒答。本轮检验：

> 在相同题目、相同模型、相同最多 3 次 Web Search 和相同两阶段结构化协议下，
> 加入来源路由、查询改写、证据充分性检查、冲突仲裁和校准 Gate 的 ATLAS-RAG，
> 能否提高最终决策准确率，同时不引入模型声明的时间泄漏？

## 2. 论文依据与工程映射

| 论文 | 正式会议 | 本轮采用的思想 | 代码映射 |
|---|---|---|---|
| [SPARKLE](https://aclanthology.org/2026.acl-long.1793/) | ACL 2026 Long | 用可插拔控制器规划检索，而不是固定一次搜索 | `atlas_route`、PLAN trace |
| [ReflectiveRAG](https://aclanthology.org/2026.eacl-industry.27/) | EACL 2026 Industry | 评估证据充分性，缺信息时改写查询 | ATLAS research prompt、最多 3 次搜索 |
| [SeCon-RAG](https://proceedings.neurips.cc/paper_files/paper/2025/hash/668563ef18fbfef0b66af491ea334d5f-Abstract-Conference.html) | NeurIPS 2025 | 区分有用证据和冲突证据，避免激进过滤 | hard conflict / metadata unknown 分离 |
| [Sufficient Context](https://openreview.net/pdf?id=Jjr2Odj8DJ) | ICLR 2025 | 在上下文足够时回答，不足时选择性拒答 | `context_sufficient` 与校准置信度 |

ATLAS-RAG 是这些论文启发的金融时点原型，不是对其中任何一篇的复现，也不作 SOTA 声明。

## 3. 固定处理条件

五种策略均使用 `temporal_clash/base_cases.json` 的固定前 20 题：

1. `plain_agent`：普通搜索 Agent；
2. `temporal_prompt`：仅增加截止日约束；
3. `metadata_filter`：严格日期、期间和版本过滤；
4. `teg_validator`：再加入单位验证；
5. `atlas_rag`：来源路由、纠错搜索、冲突仲裁和校准 Gate。

除策略提示与 ATLAS 的确定性首查询改写外，provider、模型、effort、最大搜索次数、
最大输出 token、两阶段结构化方式、题目顺序平衡规则和评价容差都保持一致。

ATLAS 只允许读取题目、截止日、目标期间、要求版本和标准单位。运行路径不得读取
`gold_answer`、记录的 `source_url` 或 `evidence_text_zh`；这些字段只供运行结束后的评测。

## 4. 校准 Gate 的冻结规则

- 明确晚于截止日：硬冲突，拒绝该证据；
- 明确目标期间、版本或单位不符：硬冲突，拒绝该证据；
- 日期或其他元数据未提供：记录为 `metadata_unknown`，不自动等同违规；
- provider 返回的 `page_age` 和 URL 中可确定解析的日期作为独立元数据记录；
- 最终回答必须有数值、标准单位、实际检索 URL、支持文本和至少一条无硬冲突证据；
- 所有 PLAN → RETRIEVE → AUDIT → RESOLVE → ANSWER/ABSTAIN 状态写入 trace。

## 5. 运行与停止规则

首先执行 1 题 × 5 策略门禁，最多 10 个 HTTP 阶段和 15 次 Web Search。门禁只检查：

- 5 条记录均为 `status=ok`；
- 搜索动作、完整来源、原生引用、两个 Claude 响应 ID、JSON Schema 和原始响应哈希齐全；
- 五种策略的实际模型一致；
- ATLAS trace 和独立来源日期字段能通过单元测试。

门禁题的正确与否不作为是否继续的停止条件。只有接口、trace 或实现错误才允许在完整实验前
修复；不得依据门禁答案修改规则。门禁通过后运行完整 20 题 × 5 策略，共 100 条记录，
最多 200 个 HTTP 阶段和 300 次 Web Search。任一 trace 无效时默认停止。

## 6. 主要指标与成功判定

主要指标：

1. 最终决策准确率；
2. 答案覆盖率；
3. 已回答样本上的模型声明时间泄漏率。

次要指标包括模型初稿准确率、引用覆盖率、完整来源覆盖率、Gate 触发率、搜索次数、
来源数、token 和延迟。按问题做配对 bootstrap 区间。

“取得进步”的最小诚实表述要求 ATLAS 在本轮同模型对照中：

- 最终决策准确率高于本轮最强旧策略；且
- 已回答样本的模型声明时间泄漏率不高于普通 Agent。

若不满足，就报告没有改善并分析失败，不改变结果或挑选题目。第一轮 Sonnet 数字只作历史
参照；主要结论来自本轮同一时间窗口的五策略比较。

## 7. 已知限制

- 这 20 题已经用于第一轮误差分析，因此新方法并非在盲测集上提出；
- 搜索结果随时间变化，不能把跨日期差异完全归因于策略；
- `page_age` 和 URL 日期只覆盖部分来源，不等于完整独立网页取证；
- 一次 20 题运行是 pilot，不足以证明普适优越性；后续需要新题、重复实验和人工盲审。
