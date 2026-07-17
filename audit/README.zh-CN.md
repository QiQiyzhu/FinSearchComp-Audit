# FinSearchComp 可审计复现实验

本目录提供一个确定性复现（deterministic replay）：使用已经保存的 6 条 Agent 运行轨迹，重新生成报告并检查轨迹、指标与输出是否一致。

它不是伪装成“实时搜索”的静态答案。两种模式的边界如下：

| 模式 | 是否联网 | 是否需要 API Key | 目的 |
|---|---:|---:|---|
| 记录轨迹复现 | 否 | 否 | 验证报告能否由保存的证据链稳定重建 |
| 上游 Agent 实时运行 | 是 | 是 | 重新调用模型和搜索/金融工具 |

## 一条命令

在仓库根目录运行：

```bash
python reproduce.py
```

该命令会依次：

1. 验证 `sample_runs.json` 的字段、案例数和成功/失败条件；
2. 生成 HTML、Markdown、JSON 和 CSV；
3. 验证输出案例顺序、指标行数、标题和关键章节；
4. 在任一步不一致时返回非零退出码。

自定义输入和输出：

```bash
python reproduce.py \
  --input audit/sample_runs.json \
  --output site
```

如果你正在开发自己的案例集，不要求固定的 3 成功/3 失败：

```bash
python reproduce.py \
  --input path/to/custom_runs.json \
  --output build/site \
  --no-strict-demo
```

## 输出

| 文件 | 用途 |
|---|---|
| `site/index.html` | 可发布的交互式汇报页面 |
| `site/report.md` | 中文实验报告 |
| `site/trace.json` | 原始问题、关键词、工具调用、答案、引用与时间审计 |
| `site/metrics.csv` | 真实性、完整性、轨迹完整度、工具数和耗时 |

## 记录一条新轨迹

每条运行至少包含：

- `question`、`reference_answer`、`final_answer`；
- `strategy` 和 `search_queries`；
- 按顺序保存的 `tool_calls`；
- 可复算的 `calculation`；
- `citations` 和逐结论的 `source_audits`；
- `benchmark_correct`、`citation_support_rate`、`time_compliance_rate` 等指标。

失败案例允许关键词或 URL 缺失，但必须如实反映在 `trace_completeness` 中，不能用虚构信息补齐。

详细口径见 [`../docs/REPRODUCIBILITY.md`](../docs/REPRODUCIBILITY.md)。

> 本项目用于研究与课程汇报，不构成投资建议。
