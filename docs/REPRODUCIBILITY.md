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
- 演示模式只使用 Python 标准库；
- 不需要网络、模型密钥或金融数据密钥；
- Windows、macOS 和 Linux 均可运行。

检查版本：

```bash
python --version
```

## 3. 标准复现

```bash
python reproduce.py
```

预期结果：

```text
[1/3] Validated 6 recorded runs (3 success, 3 failure)
[2/3] Generated 4 report artifacts in site
[3/3] Reproducibility checks passed
```

验证器会检查：

- 运行 ID 唯一；
- 严格演示包含 6 条轨迹、3 成功和 3 失败；
- 工具调用数与 `metrics.tool_call_count` 一致；
- 比率指标位于 0 到 1；
- 成功案例通过正确性、引用、时间和完整性四个门槛；
- 失败案例至少有一个门槛未通过；
- 审计 URL 能在 citations 或 tool calls 中找到；
- HTML、Markdown、JSON 和 CSV 均非空；
- `trace.json` 与输入的运行顺序一致；
- `metrics.csv` 的行数和运行 ID 一致；
- HTML 包含所有案例，报告包含要求的核心章节。

## 4. 可信成功的定义

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

## 5. 为什么保留失败案例

失败案例不是为了凑数量，而是验证审计框架能否识别不同故障层：

| 故障层 | 案例 | 可观察信号 |
|---|---|---|
| 检索 | 沪深指数问题只回答一半 | 无完整关键词、URL 和振幅计算 |
| 时间版本 | 经常账户初步值/最终值 | 同一机构的不同发布日期给出 4220 / 4239 |
| 引用关系 | NVIDIA 财年列错位 | 页面权威，但 3670 属于错误财年列 |

只保留成功案例会掩盖系统何时不可信。

## 6. 网页搜索与金融接口的可比实验

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

## 7. 添加自定义案例

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

## 8. 实时 Agent 模式

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

## 9. 数据来源与时间说明

- 指数价格案例使用 Yahoo Finance Chart JSON 进行可复算演示；
- 公司财报案例使用 SEC 文件；
- 中国经常账户案例使用国家外汇管理局公告；
- 记录的演示轨迹生成于 2026-07-17；
- `sample_runs.json` 中保留每次获取时间、来源 URL 和审计原因。

## 10. 已知局限

- 6 条案例不足以代表整个 FinSearchComp；
- 记录轨迹复现验证的是证据链和报告生成，不是实时工具稳定性；
- Yahoo Finance 不是监管级官方行情源；
- 来源支持的最终判断仍包含人工审计；
- 本项目不构成投资建议。
