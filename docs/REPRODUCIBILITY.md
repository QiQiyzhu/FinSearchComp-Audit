# Reproducibility Guide

## 1. 复现目标

本仓库要复现的不是某个 LLM 在未来仍然逐字输出同一答案，而是下面这条可审计链路：

```text
记录的 Agent 轨迹
→ 输入结构验证
→ 报告生成
→ 输出一致性验证
→ 可发布的 HTML / Markdown / JSON / CSV
```

LLM、搜索排序和网页内容会变化，因此“重新联网后得到完全相同文本”不是可靠的复现标准。本仓库将一次运行所依赖的关键词、工具参数、来源、计算与审计判断保存下来，使报告重建过程确定且可检查。

## 2. 环境

- Python 3.10 或更高版本；
- 核心审计与三消演示只使用 Python 标准库；平台 API 需要 `requirements-platform.txt`；
- 不需要网络、模型密钥或金融数据密钥；
- Windows、macOS 和 Linux 均可运行。

检查版本：

```bash
python --version
```

## 3. 标准复现

```bash
pip install -r requirements-platform.txt
python reproduce.py
```

预期结果：

```text
[1/8] Validated 12 recorded runs (6 success, 6 failure)
[2/8] Generated core audit artifacts in site
[3/8] Temporal detector benchmark and report generated
[4/8] ATLAS-RAG evaluated: MRR@10=0.929, selective_accuracy=100.0%
[5/8] Match-3 Agent QA verified: 5 legal moves, 3 cascades, deterministic replay
[6/8] FinAgent platform verified: 3 async cases, timeout recovery, idempotency, failure analytics, replay
[7/8] Agentic E4-E6 evaluated: planner_accuracy=100.0%, correct_abstention=100.0%
[8/8] All reproducibility and site-link checks passed
```

验证器会检查：

- 运行 ID 唯一；
- 严格演示包含 12 条轨迹、6 成功和 6 失败；
- 工具调用数与 `metrics.tool_call_count` 一致；
- 比率指标位于 0 到 1；
- 成功案例通过正确性、引用、时间和完整性四个门槛；
- 失败案例至少有一个门槛未通过；
- 审计 URL 能在 citations 或 tool calls 中找到；
- HTML、Markdown、JSON 和 CSV 均非空；
- `trace.json` 与输入的运行顺序一致；
- `metrics.csv` 的行数和运行 ID 一致；
- HTML 包含所有案例，报告包含要求的核心章节。
- HTML 包含面试展示所需的正式结果、E4–E6、工程平台、三消 QA 和结论边界。
- ATLAS-RAG 的检索逐题表、聚合表、决策表和状态 trace 均非空，并由网页入口引用。
- 三消 Agent QA 的场景预期、固定级联结果、Skill trace 与最终棋盘哈希可重放且一致。
- FinAgent Platform 的异步 Run、幂等、超时恢复、失败分类、Replay 和 OpenAPI 契约全部通过。
- E4–E6 的 8 个冻结合成案例、32 个充分性条件、预算扫描结果和 early-stop 成本平台期均通过。

## 4. ATLAS-RAG 离线评测

一键复现还会在 `site/advanced-rag/` 生成：

- `retrieval_per_query.csv`：BM25、Hybrid RRF、ATLAS temporal 的逐题 Recall、MRR、未来证据率；
- `retrieval_summary.csv`：检索层聚合结果；
- `system_per_query.csv`：每题回答、置信度、纠错与冲突数；
- `system_summary.csv`：覆盖率、选择性准确率与纠错比例；
- `traces.jsonl`：PLAN 到 ANSWER/ABSTAIN 的状态轨迹；
- `README.md`：人类可读报告和结果边界。

也可以单独运行：

```bash
python -m advanced_rag.evaluate
python -m unittest advanced_rag.test_advanced_rag -v
```

## 5. E4–E6 Agentic 评测

一键复现会在 `site/agentic-eval/` 生成在线报告、预算曲线、聚合 CSV、逐题 CSV 和 `summary.json`。
同时在 `advanced_rag/results/agentic/` 保存仓库内的原始结果。单独运行：

```bash
python -m advanced_rag.evaluate_agentic
python -m unittest advanced_rag.test_agentic_eval -v
```

协议固定 8 个合成问题；E4 比较 plain top-3、query rewrite 与逐槽 Planner；E5 每题派生 complete、
partial、empty、conflict；E6 扫描 1 / 2 / 3 / 5 / 8 次最大检索调用。验证器锁定以下关键结论：

- Structured Gap Planner 准确率 100%，plain one-shot 为 75%；
- Sufficiency Gate 正确拒答率 100%，无依据回答率 0%；
- 预算 5 与预算 8 都为 100%，且平均成本代理相同，证明充分即停生效。

这些是确定性机制测试，不使用外部 LLM；成本代理不等于 API token 或线上延迟。

## 6. FinAgent Audit Platform 离线复现

一键复现会在 `site/platform/` 生成：

- `index.html`：Run 状态、超时恢复、失败分析、Replay 和 API 的可视化平台报告；
- `platform_demo.json`：3 个异步 Case 与五项平台 invariant；
- `openapi.json`：FastAPI 自动生成的接口契约；
- `README.md`：人类可读的平台复现结果。

也可以单独运行：

```bash
python -m unittest finagent_platform.test_platform -v
python -m finagent_platform --output site/platform
uvicorn finagent_platform.api:create_default_app --factory --port 8080
```

平台测试包含真实 ATLAS-RAG 集成，以及并发幂等、SQLite 状态迁移、超时故障注入、Batch 子 Run、
失败分类、Replay diff 和 FastAPI schema。静态 Demo 使用注入的确定性 Answer Service，从而保证产物可重复生成。

## 7. Match-3 Agent QA 离线复现

一键复现还会在 `site/game-qa/` 生成：

- `index.html`：初始/最终棋盘、跨岗位能力和 Skill trace 的可视化报告；
- `report.json`：初始棋盘分析、全部合法动作模拟、固定回归动作和四项复现检查；
- `trace.json`：Skill 参数、成本预算、调用状态及每步前后棋盘哈希；
- `README.md`：人类可读的复现结果与指标边界。

也可以单独运行：

```bash
python -m unittest game_qa_agent.test_game_qa_agent -v
python -m game_qa_agent --output site/game-qa
```

该模块只使用标准库，不把 Agent 输出作为规则 oracle。模型只能规划白名单 Skill，交换合法性、
级联、计分和回放是否一致都由确定性程序判定。

## 8. 可信成功的定义

```text
current_fact_correct = true
citation_support_rate = 1.0
time_compliance_rate = 1.0
answer_completeness = 1.0
```

此外，发布演示中的成功案例还必须：

- 匹配 FinSearchComp 参考答案；
- `trace_completeness = 1.0`；
- 保存非空查询词；
- 保存可访问的引用 URL。

## 9. 为什么保留失败案例

失败案例不是为了凑数量，而是验证审计框架能否识别不同故障层：

| 故障层 | 案例 | 可观察信号 |
|---|---|---|
| 检索 | 沪深指数问题只回答一半 | 无完整关键词、URL 和振幅计算 |
| 时间版本 | 经常账户初步值/最终值 | 同一机构的不同发布日期给出 4220 / 4239 |
| 引用关系 | NVIDIA 财年列错位 | 页面权威，但 3670 属于错误财年列 |
| 公司行为 | NVIDIA 10-for-1 拆股 | 未复权比较两个名义价格，制造虚假暴跌 |
| 单位缩放 | Tesla 研发费用 | 忽略 Dollars in millions，结果缩小千倍 |
| 时间边界 | S&P 500 年度收益率 | 用当年第一个交易日代替上年最后一个交易日 |

只保留成功案例会掩盖系统何时不可信。

## 10. 网页搜索与金融接口的可比实验

更严格的后续比较应固定：

1. 同一问题和参考答案；
2. 同一 as-of date；
3. 同一模型和提示词；
4. 相同工具预算；
5. 相同输出与审计格式。

只改变工具路线：

```text
Web-only
vs.
Financial-API-only
vs.
Hybrid
```

建议报告正确率、引用支持率、时间合规率、答案完整性、工具调用数和耗时，而不是只比较最终答案。

## 11. 添加自定义案例

复制一条 `audit/sample_runs.json` 记录并修改以下内容：

1. 使用新的 `run_id`；
2. 保存原始问题和参考答案；
3. 记录 Agent 的策略与关键词；
4. 按发生顺序记录工具调用；
5. 保存可复算的计算过程；
6. 为每个关键结论添加 `source_audits`；
7. 如实设置指标，不要把未知项自动视为通过。

然后运行：

```bash
python reproduce.py \
  --input path/to/custom_runs.json \
  --output build/site \
  --no-strict-demo
```

## 12. 实时 Agent 模式

正式的 OpenAI/Claude Web Search Agent 实验不再通过上游聊天脚本运行。当前已经公开
[20题×4策略真实 pilot](../temporal_clash/results/live_pilot_20q_claude_complete/README.md)，
共 80 条严格验证 trace。`trace.jsonl` 保存代码与 prompt 哈希、实际模型、UTC 时间、
搜索动作、引用和完整来源；`case_outcomes.csv` 保存逐题配对结果；
`case_analysis.md` 提供中文逐题解释，`confidence_intervals.csv` 提供按题目配对的
bootstrap 区间，`study_manifest.json` 保存预算和排除统计。原始响应只在本地保留，
由 trace 中的 SHA-256 校验。

本地源运行最终有 80 条成功记录；公开结果保留预定的全部 20 道题和四种策略，
没有按结果排除成功单元。早期 4 次中转连接中断记录在 `exclusions.json`，续跑成功后
不进入策略指标。下一步应先改善独立时间元数据获取，再按
[`LIVE_STUDY_PROTOCOL.md`](LIVE_STUDY_PROTOCOL.md)进行预注册的重复实验。

下面的上游命令仅保留为原 FinSearchComp 模型调用参考，不满足本项目正式 trace 门禁：

如需重新调用上游模型：

```bash
pip install -r finsearchcomp/requirements.txt
python finsearchcomp/chat/chat.py \
  --model_name gemini-2.5-flash \
  --input_file data/finsearchcomp_akshare_version.json \
  --output_path finsearchcomp/result/chat-result/chat.json \
  --limit 1
```

注意：

- 实时运行需要模型/API 配置；
- 不要把密钥写入 Git 或公开仓库；
- 搜索结果和网页内容会随时间改变；
- 对实时结果仍应转换成与 `sample_runs.json` 类似的审计记录。

## 13. 数据来源与时间说明

- 指数价格案例使用 Yahoo Finance Chart JSON 进行可复算演示；
- 公司财报案例使用 Apple、Tesla 和 NVIDIA 的 SEC 文件；
- 宏观与政策案例使用 BLS 和 Federal Reserve 官方公告；
- 公司行为案例使用 NVIDIA 官方拆股文件；
- 中国经常账户案例使用国家外汇管理局公告；
- 记录的演示轨迹生成于 2026-07-17；
- `sample_runs.json` 中保留每次获取时间、来源 URL 和审计原因。

## 13. 已知局限

- 12 条案例仍不足以代表整个 FinSearchComp；
- 记录轨迹复现验证的是证据链和报告生成，不是实时工具稳定性；
- Yahoo Finance 不是监管级官方行情源；
- 来源支持的最终判断仍包含人工审计；
- 本项目不构成投资建议。
