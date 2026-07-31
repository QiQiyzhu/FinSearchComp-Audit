# Claude 真实 Web Search Agent：1题 × 4策略门禁

> 这是正式实验前的能力与 trace 门禁，不是模型性能结论。

## 运行结论

| 项目 | 结果 |
|---|---|
| 状态 | `completed`，4/4 记录通过严格校验 |
| 运行日期 | 2026-07-31 |
| Provider | Anthropic Messages-compatible relay |
| 中转地址 | `https://ai.aiclick.cc` |
| 请求与实际模型 | `claude-sonnet-5` |
| 代码提交 | `1d6d0aab00bb9dc3c64876d9a08e45911598e0fd` |
| Reasoning effort | `medium` |
| 搜索预算 | 每个策略最多 3 次 |
| HTTP | 8/8 阶段成功 |
| 实际 Web Search | 10 次 |
| 完整来源 / 原生 citations | 84 / 18 |
| Token | 18,347 input / 7,687 output |
| UTC 时间窗 | 07:01:37–07:05:07 |

四种策略均使用同一模型、同一 effort、同一工具预算、同一问题和同一两阶段
Claude 流程。只改变策略规则。

## 逐策略结果

| 策略 | API/trace | 最终动作 | 决策正确 | 搜索 | 来源 | Citations |
|---|---:|---|---:|---:|---:|---:|
| 普通搜索 Agent | 通过 | 回答 `12.7%` | 是（容差内） | 3 | 20 | 8 |
| 时间约束 Prompt | 通过 | 回答 `12.7%` | 是（容差内） | 2 | 20 | 5 |
| 元数据过滤器 | 通过 | 拒答 | 否 | 3 | 25 | 2 |
| 完整证据验证器 | 通过 | 拒答 | 否 | 2 | 19 | 3 |

元数据过滤器和完整验证器的模型初稿都给出了数值，但本地 gate 发现
`published_at` / `revision` 等必要字段缺失，因此改为拒答。这里的 0% 不是 API
失败，而是单题上严格 gate 的保守决策。样本量为 1，不能据此比较策略优劣。

## 可审计产物

- [`study_manifest.json`](study_manifest.json)：固定控制、问题/提示词哈希、代码指纹和时间窗；
- [`trace.jsonl`](trace.jsonl)：四条标准化完整 trace、两个阶段的无密钥请求、响应 ID、
  搜索动作、完整来源、citations、token 和证据检查；
- [`metrics.csv`](metrics.csv)：机器可读指标；
- [`summary.md`](summary.md)：逐策略摘要；
- `raw_responses/`：本地私有归档，保存完整 API 响应并由 trace 中的 SHA-256 校验，
  不提交到公开 Git。

## 下一步预算

按本次观测线性估算，20题 × 4策略 × 3重复约为：

- 480 次 HTTP 请求；
- 约 600 次 Web Search，硬上限 720；
- 约 110 万 input tokens、46 万 output tokens；
- 顺序运行约 3.5 小时，复杂题可能更久。

中转响应没有返回金额字段，因此正式扩展前仍需在中转后台确认余额和计费规则。
