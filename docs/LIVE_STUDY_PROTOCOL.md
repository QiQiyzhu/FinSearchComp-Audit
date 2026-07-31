# 真实 LLM / Web Search Agent 正式实验协议

## 1. 目标与判定边界

真实 pilot 检验四种策略在开放网页上的外部有效性。它不替代 100 条人工控制实验，
也不把受控实验的 100% 解释为真实 LLM 性能。

一次运行只有同时满足以下条件，才会被标记为研究有效：

1. 服务端确实执行了 Web Search；
2. 保存模型、UTC 时间、完整提示词及 SHA-256、响应 ID、token 和搜索动作；
3. 保存最终 citations，以及模型实际参考的完整来源 URL 列表；
4. 每次最多 3 次内部 Web Search 工具调用；
5. 输出经过 JSON Schema 约束；
6. 原始 API 响应写入本地 `raw_responses/`，并在 trace 中保存路径和哈希；
7. 四种策略使用同一 provider、模型、reasoning effort、工具预算和问题批次；
8. 实际返回的模型版本在四种策略之间一致。

任一条件不满足时，runner 会把记录标为 `invalid`，默认立即停止，避免继续花费。

## 2. OpenAI 与 Anthropic 的实现差异

| 研究要求 | OpenAI Responses | Anthropic Messages |
|---|---|---|
| 强制搜索 | `tool_choice: "required"` | 强制选择 `web_search` server tool |
| 完整来源 | `include: ["web_search_call.action.sources"]` | Web Search tool `response_inclusion: "full"` |
| 内部搜索上限 | `max_tool_calls: 3` | Web Search tool `max_uses: 3` |
| Structured Output | 同一 Responses 请求的 `text.format` | 同一模型的第二阶段 `output_config.format` |
| 搜索上下文大小 | 固定 `medium` | Anthropic 无等价参数，记录为 provider-managed |

Anthropic 的 citations 与 `output_config.format` 不能在同一请求中启用。因此 Claude
实验固定为两阶段：

```text
同一 Claude 模型 + 同一 effort
  1. 强制 Web Search，保存 citations、完整 search results 与原始响应
  2. 不再搜索，只把第一阶段材料约束为 JSON Schema
```

这不是对某个策略额外增加工具；四种策略都执行完全相同的两阶段流程。

官方接口依据：

- [OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Anthropic Web Search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)

## 3. 密钥安全

曾经粘贴到聊天、截图、Issue 或提交记录中的密钥都应立即在供应商后台撤销并重新生成。
不要把真实密钥写入仓库内的 JSON、YAML、README 或 `settings.json`。

只在准备运行的 PowerShell 会话中设置轮换后的新密钥：

```powershell
$env:ANTHROPIC_BASE_URL = "https://ai.aiclick.cc"
$env:ANTHROPIC_AUTH_TOKEN = "<ROTATED_TOKEN>"
```

关闭该 PowerShell 会话后，进程级环境变量即失效。

## 4. 中转服务能力预检

“能够调用 Claude 文本”不等于“能够完成本实验”。中转服务必须原样支持：

- Anthropic Messages API；
- Anthropic server-side Web Search；
- 完整 search result blocks 和 citations；
- `output_config.effort`；
- `output_config.format` JSON Schema。

先做不产生模型输出和搜索费用的模型清单预检：

```powershell
$model = "从中转模型清单中选择的精确模型 ID"
python -m temporal_clash.probe_live_api `
  --provider anthropic `
  --base-url $env:ANTHROPIC_BASE_URL `
  --model $model `
  --output temporal_clash/results/provider_probe.json `
  --confirm-network
```

`requested_model_listed=true` 只证明认证和模型清单可用。是否支持 Web Search 与
Structured Outputs，必须由实际的多策略运行证明。

## 5. 1题 × 4策略正式门禁

**当前状态：已于 2026-07-31 使用 `claude-sonnet-5` 真实通过，并扩展为 10×4 pilot。**
公开结果包含 40/40 条严格有效记录、80 个成功 HTTP 阶段、70 次搜索、547 个完整来源
和 155 条原生 citations。见
[`temporal_clash/results/live_pilot_10q_claude_r1/README.md`](../temporal_clash/results/live_pilot_10q_claude_r1/README.md)。

Claude 每个策略固定使用 2 个 HTTP 阶段，所以 4 个策略最多需要 8 次请求；
每个策略最多 3 次搜索，所以最多 12 次付费 Web Search。

```powershell
python -m temporal_clash.run_live_pilot `
  --provider anthropic `
  --base-url $env:ANTHROPIC_BASE_URL `
  --limit 1 `
  --model $model `
  --reasoning-effort medium `
  --search-context-size medium `
  --max-tool-calls 3 `
  --max-api-calls 8 `
  --output-dir temporal_clash/results/live_pilot_1q_gate `
  --confirm-live
```

只有以下结果才算通过：

- `study_manifest.json` 的 `status` 为 `completed`；
- `manifest.json` 显示 `valid_runs=4`；
- `protocol_validation_errors=[]`；
- 四条记录都有 search action、完整来源、citation、结构化结果和原始响应哈希；
- `requested_model` 和实际 `model` 没有跨策略变化。

如果中转不支持任一原生能力，程序会停止。不要通过关闭强制搜索或退回 Prompt JSON
来“跑通”，因为那会改变研究对象。

## 6. 20题 × 4策略 × 3重复

当前 10×4 是一次真实开放网页 pilot，但仍只有一个模型、一个运行窗口和一次重复。
它发现严格过滤器会因网页时间元数据缺失而过度拒答，因此不应把下面的完整实验描述成
已经完成；应先改进元数据获取和校准，再在同一代码提交、同一模型和尽可能集中的时间窗口运行：

```powershell
python -m temporal_clash.run_live_pilot `
  --provider anthropic `
  --base-url $env:ANTHROPIC_BASE_URL `
  --limit 20 `
  --model $model `
  --reasoning-effort medium `
  --search-context-size medium `
  --max-tool-calls 3 `
  --repeats 3 `
  --max-api-calls 480 `
  --output-dir temporal_clash/results/live_pilot_20q_claude_3x `
  --confirm-live
```

预算上界是：

```text
20题 × 4策略 × 3重复 × 2个Claude阶段 = 480 次 HTTP 请求
20题 × 4策略 × 3重复 × 3次搜索上限 = 720 次 Web Search
```

runner 按问题和重复次数循环轮换四种策略的执行顺序，降低固定先后顺序与网页变化的混杂。
每个 repeat 独立生成 `trace.jsonl`、`metrics.csv`、`summary.md` 和 `manifest.json`；
根目录额外生成均值、样本标准差、最小值和最大值的 aggregate 报告。

## 7. 结果保存与 GitHub 发布

完整原始响应保存在每次实验的 `raw_responses/`。该目录默认被 Git 忽略：

- 本地/私有研究归档：保存完整 raw response，并根据 trace 中的 SHA-256 验证；
- GitHub 展示：提交无密钥的 manifest、标准化 trace、metrics 和 summary；
- 不要未经检查就公开大段网页正文、加密搜索内容或供应商内部字段。

正式结果提交前至少检查：

```powershell
python -m unittest temporal_clash.test_live_agent temporal_clash.test_detector -v
python reproduce.py
git status --short
```

## 8. 仍需在论文中声明的局限

- 搜索排名和网页内容会随时间改变；
- Anthropic 没有可固定的 `search_context_size`，因此不能把该参数与 OpenAI
  做严格等值比较；
- 当前 `published_at` 主要来自模型的结构化报告，下一步仍需独立抓取网页或官方文件
  元数据进行二次验证；
- 三次重复只能作为 pilot 的不稳定性描述，不能替代更大样本、人工复核和统计功效分析。
