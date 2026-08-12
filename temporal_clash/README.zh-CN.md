# FinTemporalClash Mini

这是一个约 100 条的受控测试集，用来回答一个窄问题：

> 当金融搜索 Agent 面对未来来源、错期间、错单位或错版本证据时，结构化验证能否比普通提示词更稳定？

## 当前真实 pilot

2026-07-31 已完成 `claude-sonnet-5` 的 20 题 × 4 策略真实 Web Search pilot，
共 80 条严格验证记录。公开产物包含逐次 trace、聚合指标、逐题配对结果、
bootstrap 区间、排除说明和研究清单：

[查看真实 20×4 pilot](results/live_pilot_20q_claude_complete/README.md)

真实结果显示严格时间过滤会因开放网页缺少可验证日期而过度拒答；因此它是机制与局限性的
初步证据，不是策略优越性的最终结论。

## 数据设计

- 20 个真实金融问题，覆盖行情、宏观统计、央行公告与公司财报；
- 每题生成 5 个证据条件，共 100 条实例；
- 4 类冲突证据由人工规则模板构造，均用 `synthetic://` URL 和 `is_perturbed=true` 标识；
- 11 个问题来自本仓库原有审计案例，9 个问题改编自 FinanceBench，并保留来源归属；
- `base_cases.json` 是人工维护层，`data/controlled_cases.jsonl` 是可复现的生成层。

| 条件 | 候选证据 | 正确行为 |
|---|---|---|
| `clean` | 一条正确且截止日前可用的证据 | 回答 |
| `future_only` | 只有截止日之后发布的证据 | 拒答 |
| `period_conflict` | 错期间证据在前，正确证据在后 | 选择正确期间 |
| `unit_conflict` | 错单位证据在前，正确证据在后 | 选择正确单位 |
| `version_conflict` | 错版本证据在前，正确证据在后 | 选择要求的版本 |

## 四种方法

1. **普通 Agent**：直接采用第一条证据；
2. **时间约束 Prompt**：只过滤截止日之后的证据；
3. **元数据过滤器**：再检查目标期间和数据版本；
4. **TEG 验证器**：同时检查日期、期间、版本和单位。

`detector.py` 将这四种检查封装为统一的 `TemporalLeakageDetector`。它对每条候选证据输出：

- `accept` / `reject` 判定；
- 0–1 风险分数；
- 未通过的检查项；
- 每项检查的期望值、实际值和中文解释。

当前四种方法是可审计的确定性代理策略，并非真实 LLM API 运行。它们的用途是先固定实验协议、评价指标和预期消融关系。后续可把候选证据替换为真实搜索 Agent 的检索结果，同时保持检测器、数据结构和评价脚本不变。

## 复现

```bash
python -m temporal_clash.run_experiment --check --site-dir site
```

输出：

- `data/controlled_cases.jsonl`：100 条实例；
- `results/experiment_table.csv`：四种方法总表；
- `results/per_condition.csv`：按冲突类型拆分；
- `results/predictions.jsonl`：逐题决策；
- `results/detector_table.csv`：候选级 Precision、Recall、F1 和安全证据保留率；
- `results/detector_predictions.jsonl`：逐候选检查理由与风险分数；
- `results/summary.md`：可直接阅读的实验摘要。
- `site/temporal-audit.html`：GitHub Pages 展示页。

## 评价指标

- **决策准确率**：该答时答案正确，该拒答时确实拒答；
- **可回答题答案准确率**：只在非 `future_only` 条件上计算；
- **未来证据正确拒答率**；
- **时间违规率**：是否使用截止日之后发布的证据；
- **扰动采纳率**：是否选择人工扰动证据；
- **引用支持率**：已回答样本中，所选证据是否支持金标准；
- **覆盖率**：系统实际作答比例。
- **挑战条件准确率**：只在四类受控冲突条件上计算；
- **Temporal Robustness Gap（TRG）**：干净条件准确率减去挑战条件准确率，越接近 0 越稳定；
- **候选级检测 F1**：把人工扰动证据视为正类，衡量拦截能力；
- **安全证据保留率**：正确证据没有被误杀的比例。

## 研究边界

- 满分只说明验证规则正确执行，不代表能处理开放网页中的隐含冲突；
- 人工扰动的措辞和位置可能低估真实检索噪声；
- 受控表格不是付费 LLM 排名；真实 pilot 也只有单模型、单次运行，不能外推为总体排名；
- 真实 Agent 实验必须额外记录搜索日期、模型版本、提示词、费用和完整工具轨迹；
- 本测试集不构成投资建议。

## 与 Look-Ahead-Bench 的关系

Look-Ahead-Bench 通过比较两个市场时期的 Alpha Decay 诊断交易模型的前视偏差。本项目保留“干净条件 vs 时间外条件”的核心思想，但把研究对象改为金融搜索 Agent 的证据链：

- Look-Ahead-Bench 问：收益从可能记忆过的时期到未知时期衰减多少？
- 本项目问：证据从干净状态变为未来/错期间/错版本/错单位时，决策稳定性下降多少？

因此 TRG 是面向搜索与引用任务的审计指标，不应被解释为交易 Alpha。

## 相关论文

- Zhang, Chen, Stadie. [All Leaks Count, Some Count More](https://arxiv.org/abs/2602.17234), 2026.
- Yang et al. [Search-Time Data Contamination](https://arxiv.org/abs/2508.13180), 2025.
- Wu et al. [ClashEval](https://arxiv.org/abs/2404.10198), NeurIPS 2024.
- Islam et al. [FinanceBench](https://arxiv.org/abs/2311.11944), 2023.
- Benhenda. [Look-Ahead-Bench](https://arxiv.org/abs/2601.13770), 2026.

## 真实 Web Search Agent pilot

仓库提供了 OpenAI Responses 和 Anthropic Messages 两种真实搜索接入。它使用
20 个基础金融问题，分别运行普通 Agent、时间 Prompt、元数据过滤器、完整证据
验证器和校准版 ATLAS-RAG。ATLAS 进一步加入来源路由、查询改写、证据充分性、
明确冲突/元数据未知分离和状态 trace。runner
强制搜索、保存完整来源、限制内部搜索次数、使用 JSON Schema，
并把原始响应、模型、日期、提示词哈希、响应 ID、token、引用和结果写入 trace。

先查看运行计划（不会访问 API，也不会产生费用）：

```powershell
python -m temporal_clash.run_live_pilot
```

首次接入新的 Claude 中转时，先运行模型清单预检，再跑 1 题、5 个策略门禁；
当前 Sonnet 5 的门禁与 20×5 pilot 均已完成：

```powershell
$env:ANTHROPIC_BASE_URL = "https://ai.aiclick.cc"
$env:ANTHROPIC_AUTH_TOKEN = "<轮换后的本地密钥>"
$model = "中转实际列出的 Claude 模型 ID"
python -m temporal_clash.probe_live_api `
  --provider anthropic --model $model --confirm-network
python -m temporal_clash.run_live_pilot `
  --provider anthropic --limit 1 --model $model `
  --max-tool-calls 3 --max-output-tokens 4800 --max-api-calls 10 `
  --output-dir temporal_clash/results/live_pilot_1q_gate `
  --confirm-live
```

确认后运行 20 题 × 5 策略 × 3 重复：

```powershell
python -m temporal_clash.run_live_pilot `
  --provider anthropic --limit 20 --model $model `
  --reasoning-effort medium --search-context-size medium `
  --max-tool-calls 3 --max-output-tokens 4800 --repeats 3 --max-api-calls 600 `
  --output-dir temporal_clash/results/live_pilot_20q_claude_3x `
  --confirm-live
```

安全边界：

- API Key 只从进程环境变量读取，不写入仓库；
- 没有 `--confirm-live` 时只打印调用计划；
- `--max-api-calls` 限制 HTTP 请求，`--max-tool-calls` 限制响应内部搜索；
- 默认断点续跑，只跳过已经成功的 `case × strategy`；
- 任一成功记录缺少搜索、完整来源、结构化输出或原始响应时会立即判为 invalid；
- 真实 pilot 是外部有效性实验，不能替代人工扰动的因果受控实验；
- 当前来源日期是 Agent 报告的元数据，后续需增加独立网页抓取验证。

完整协议、两种 provider 的差异和结果发布边界见
[`docs/LIVE_STUDY_PROTOCOL.md`](../docs/LIVE_STUDY_PROTOCOL.md)。
