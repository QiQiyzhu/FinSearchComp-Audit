"""Generate a self-contained FinSearchComp audit report from recorded runs."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.0f}%"


def overall_truth(metrics: dict) -> bool:
    return bool(
        metrics.get("current_fact_correct")
        and metrics.get("citation_support_rate") == 1
        and metrics.get("time_compliance_rate") == 1
    )


def markdown_report(payload: dict) -> str:
    runs = payload["runs"]
    successes = [run for run in runs if run["outcome"] == "success"]
    failures = [run for run in runs if run["outcome"] == "failure"]
    full = successes[0]
    lines = [
        "# FinSearchComp 金融搜索 Agent 实验报告",
        "",
        "> 关键词、工具调用、答案和引用审计均保存在 `trace.json`。",
        "",
        "## 一、任务理解",
        "",
        "FinSearchComp 包含时效数据获取（T1）、简单历史查询（T2）和复杂历史调查（T3）。本实验额外检查引用支持关系、数据版本/时间范围，以及普通网页搜索和金融 JSON 接口的差异。",
        "",
        "## 二、完整任务轨迹",
        "",
        f"**题目：** {full['question']}",
        "",
        f"**策略：** {full['strategy']}",
        "",
        "**关键词/结构化查询：**",
        "",
    ]
    lines.extend([f"- `{query}`" for query in full["search_queries"]])
    lines.extend(["", "**工具调用：**", ""])
    for call in full["tool_calls"]:
        lines.extend(
            [
                f"{call['sequence']}. `{call['tool']}` 参数：`{json.dumps(call['arguments'], ensure_ascii=False)}`",
                f"   - 返回摘要：{call['result_summary']}",
                f"   - 数据地址：[{call['source_url']}]({call['source_url']})",
            ]
        )
    lines.extend(
        [
            "",
            f"**计算：** `{full['calculation']}`",
            "",
            f"**最终答案：** {full['final_answer']}",
            "",
            "**引用审计：**",
            "",
        ]
    )
    for audit in full["source_audits"]:
        lines.append(
            f"- 支持关系 `{audit['support']}`；时间检查 `{audit['time_check']}`：{audit['reason']}"
        )
    lines.extend(
        [
            "",
            f"## 三、{len(successes)} 个成功案例和 {len(failures)} 个失败案例",
            "",
            "| 结果 | 案例 | 方法 | 正确 | 引用支持 | 时间合规 | 完整性 | 工具数 |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for run in runs:
        metrics = run["metrics"]
        lines.append(
            "| {outcome} | {label} | {mode} | {correct} | {support} | {time} | {complete} | {calls} |".format(
                outcome="成功" if run["outcome"] == "success" else "失败",
                label=run["label"],
                mode=run["mode"],
                correct="是" if metrics["benchmark_correct"] else "否",
                support=percent(metrics["citation_support_rate"]),
                time=percent(metrics["time_compliance_rate"]),
                complete=percent(metrics["answer_completeness"]),
                calls=metrics["tool_call_count"],
            )
        )
    lines.extend(["", "### 成功案例", ""])
    for run in successes:
        lines.append(f"- **{run['label']}**：{run['final_answer']}")
    lines.extend(["", "### 失败案例及原因", ""])
    for run in failures:
        reason = "；".join(audit["reason"].rstrip("。；") for audit in run["source_audits"])
        lines.append(f"- **{run['label']}**：{reason}。")
    lines.extend(
        [
            "",
            "## 四、普通网页搜索 vs 金融数据接口",
            "",
            "| 维度 | 普通网页搜索 | 金融数据接口 |",
            "|---|---|---|",
            "| 优点 | 能找到公告、财报、定义和事件背景；适合非结构化事实 | OHLC/时间序列结构化，容易复算、批量处理和核对时间窗 |",
            "| 缺点 | 搜索片段可能过期；容易停止过早；表格列和财年可能错位 | ticker、复权、时区、供应商口径需要明确；接口也不是绝对权威 |",
            "| 本实验表现 | 失败案例覆盖轨迹缺失、版本冲突、财年错列、拆股、单位与时间边界 | 价格计算、公司财报、宏观指标和政策公告案例均通过审计 |",
            "| 推荐策略 | 先找官方定义/公告，再用第二来源交叉核验 | 价格与长时间序列优先 API，最后用官方/独立来源抽检 |",
            "",
            "## 五、如何评价真实性、完整性和效率",
            "",
            "- **真实性**：答案正确只是第一关；每个关键结论必须映射到真正支持它的来源，并检查初值/终值、财年、币种、单位和日期范围。",
            "- **完整性**：按题目评分点逐项核对。只答出年涨幅却漏掉振幅，仍是失败。",
            "- **效率**：记录工具调用数和耗时。T1/T3 结构化价格题应尽早切到金融接口；官方财报题应直接搜监管机构或公司文件。",
            "- **综合判定**：`当前事实正确 AND 引用支持率=100% AND 时间合规率=100%` 才算可信成功。",
            "",
            "## 六、其他必要发现",
            "",
            "- 上游 `chat.py` 原文件含未解决的 Git 合并冲突，已修复；默认只跑 1 条，避免误花 API 费用。",
            "- 上游评分器只接受一种嵌套 JSON，导致自带结果出现 `-100000`；已兼容标量、嵌套、带/不带代码块的 JSON。",
            "- 数据集的 2024 年中国经常账户参考值 4220 亿美元来自初步数；正式数后来修订为 4239 亿美元。这是重要的“时间版本”失败案例。",
            "- 不应把 API key 写入 `config.yaml` 或提交到仓库；实时复跑时使用 GitHub Secrets。",
            "",
            "## 七、汇报总结",
            "",
            "1. FinSearchComp 和三类任务；",
            "2. 实验架构与留痕格式；",
            "3. S&P 500 最大月涨幅完整轨迹；",
            "4. 普通搜索与金融接口对比；",
            "5. 六个成功案例；",
            "6. 六个失败案例。",
            "",
            "## 八、可复核文件",
            "",
            "- `trace.json`：六次运行的完整结构化记录；",
            "- `metrics.csv`：成功/失败和评价指标；",
            "- `sample_runs.json`：可重复生成本报告的输入。",
            "",
            f"生成数据版本：{payload['created_at']}",
        ]
    )
    return "\n".join(lines) + "\n"


def html_report(payload: dict) -> str:
    runs = payload["runs"]
    successes = [run for run in runs if run["outcome"] == "success"]
    failures = [run for run in runs if run["outcome"] == "failure"]
    run_count = len(runs)
    success_count = len(successes)
    failure_count = len(failures)
    cards = []
    for run in runs:
        metrics = run["metrics"]
        badge = "success" if run["outcome"] == "success" else "failure"
        queries = "".join(f"<li><code>{html.escape(q)}</code></li>" for q in run["search_queries"])
        calls = "".join(
            "<li><strong>{}</strong> — {}</li>".format(
                html.escape(call["tool"]), html.escape(call["result_summary"])
            )
            for call in run["tool_calls"]
        )
        audits = "".join(
            "<li><b>{}/{}</b> — {}</li>".format(
                html.escape(audit["support"]),
                html.escape(audit["time_check"]),
                html.escape(audit["reason"]),
            )
            for audit in run["source_audits"]
        )
        cards.append(
            f"""<article class="card {badge}">
            <div class="badge">{'成功' if badge == 'success' else '失败'}</div>
            <h3>{html.escape(run['label'])}</h3>
            <p class="question">{html.escape(run['question'])}</p>
            <p><b>策略：</b>{html.escape(run['strategy'])}</p>
            <details><summary>关键词和工具调用</summary><ul>{queries}{calls}</ul></details>
            <p><b>最终答案：</b>{html.escape(run['final_answer'])}</p><ul>{audits}</ul>
            <div class="metrics"><span>引用 {percent(metrics['citation_support_rate'])}</span>
            <span>时间 {percent(metrics['time_compliance_rate'])}</span>
            <span>完整 {percent(metrics['answer_completeness'])}</span>
            <span>工具 {metrics['tool_call_count']}</span></div></article>"""
        )
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            "✓" if run["outcome"] == "success" else "✗",
            html.escape(run["label"]),
            html.escape(run["mode"]),
            "通过" if overall_truth(run["metrics"]) else "未通过",
            run["metrics"]["tool_call_count"],
        )
        for run in runs
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="可复现的金融搜索 Agent 轨迹、来源支持与时间有效性审计">
<title>FinSearchComp 审计</title>
<style>
:root{{--ink:#172033;--muted:#667085;--blue:#2563eb;--green:#14804a;--red:#b42318;--bg:#f3f6fb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}}
header{{background:linear-gradient(125deg,#102044,#244f9e);color:white;padding:60px 24px}}.wrap{{max-width:1100px;margin:auto}}
h1{{font-size:clamp(30px,5vw,52px);line-height:1.12;margin:0 0 16px}}header p{{max-width:780px;color:#dbe7ff}}nav a{{color:white;margin-right:18px}}
main{{padding:36px 24px 80px}}.summary,.grid,.compare{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}}
.summary{{grid-template-columns:repeat(4,1fr);margin-top:-62px}}.stat,.card,.panel,.compare section{{background:white;border:1px solid #e5eaf2;border-radius:16px;box-shadow:0 8px 28px #14264a12}}
.stat,.card,.panel,.compare section{{padding:22px}}.stat b{{display:block;font-size:28px;color:var(--blue)}}h2{{margin-top:48px}}
.success{{border-top:4px solid var(--green)}}.failure{{border-top:4px solid var(--red)}}.badge{{display:inline-block;padding:3px 10px;border-radius:99px;background:#eaf8f0;color:var(--green);font-weight:700}}.failure .badge{{background:#fff0ee;color:var(--red)}}
.question,footer{{color:var(--muted)}}code{{background:#eef2f7;padding:2px 6px;border-radius:5px;word-break:break-word}}pre{{background:#101828;color:#eef4ff;padding:18px;border-radius:12px;overflow:auto}}pre code{{background:none;color:inherit;padding:0}}.metrics{{display:flex;gap:8px;flex-wrap:wrap}}.metrics span{{background:#f2f4f7;border-radius:8px;padding:5px 9px}}
.panel{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:11px;border-bottom:1px solid #e7eaf0;text-align:left}}th{{background:#f8fafc}}footer{{margin-top:48px}}
@media(max-width:760px){{.summary,.grid,.compare{{grid-template-columns:1fr}}.summary{{margin-top:-44px}}}}
</style></head><body><header><div class="wrap"><p>FYP · Financial Search Agent</p>
<h1>FinSearchComp<br>搜索轨迹与可信度审计</h1><p>完整轨迹、{success_count} 个成功、{failure_count} 个失败、引用支持、时间检查，以及普通搜索与金融 API 对比。</p>
<nav><a href="#reproduce">一分钟复现</a><a href="#trajectory">完整轨迹</a><a href="#cases">十二个案例</a><a href="report.md">汇报稿</a><a href="trace.json">JSON 轨迹</a></nav>
</div></header><main class="wrap"><section class="summary"><div class="stat"><b>{run_count}</b>实验案例</div>
<div class="stat"><b>{success_count} / {failure_count}</b>成功 / 失败</div><div class="stat"><b>100%</b>成功案例引用支持</div><div class="stat"><b>3 类</b>金融 API / 官方来源 / 搜索</div></section>
<h2 id="reproduce">一分钟复现</h2><div class="panel"><p><b>确定性复现：</b>从保存的 Agent 轨迹重新生成报告并验证一致性；不会把记录数据冒充成实时搜索。</p>
<pre><code>git clone https://github.com/QiQiyzhu/FinSearchComp-Audit.git
cd FinSearchComp-Audit
python reproduce.py</code></pre>
<p>无需 API Key。命令会验证 {run_count} 条轨迹、生成 4 个产物，并检查案例顺序、指标行数、来源审计和关键章节。</p></div>
<h2 id="trajectory">完整搜索任务轨迹</h2><div class="panel"><h3>S&P 500 最大单月涨幅</h3>
<ol><li>规划：ticker → 时间窗 → 月频 → 相邻月收益 → 最大值。</li>
<li>查询 <code>^GSPC monthly close 2009-12-01 to 2025-04-30</code>。</li>
<li>调用 Yahoo Finance Chart JSON。</li><li>计算 <code>2912.4299 / 2584.5901 - 1 = 12.6844%</code>。</li>
<li>答案：<b>April 2020, 12.68%</b>，引用和时间窗均通过。</li></ol></div>
<h2 id="cases">{success_count} 个成功与 {failure_count} 个失败</h2><div class="grid">{''.join(cards)}</div>
<h2>结果总表</h2><div class="panel"><table><thead><tr><th>结果</th><th>案例</th><th>方法</th><th>真实性综合检查</th><th>工具数</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>普通搜索 vs 金融接口</h2><div class="compare"><section><h3>普通网页搜索</h3><p>擅长公告、财报和背景；但片段可能过期、表格列可能错位，Agent 也可能停止过早。</p></section>
<section><h3>金融数据接口</h3><p>擅长 OHLC 和长时间序列；结构化、易复算、效率高。仍需明确 ticker、复权、时区和供应商口径。</p></section></div>
<h2>评价框架</h2><div class="panel"><p><b>真实性：</b>答案正确 + 引用真的支持 + 时间版本正确。</p>
<p><b>完整性：</b>题目每个评分点都回答。</p><p><b>效率：</b>比较工具数、耗时和无效搜索；结构化题尽早切换金融 API。</p></div>
<footer>详情：<a href="report.md">report.md</a> · <a href="trace.json">trace.json</a> · <a href="metrics.csv">metrics.csv</a> · <a href="https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/REPRODUCIBILITY.md">复现说明</a></footer>
</main></body></html>"""


def write_outputs(input_path: Path, output_dir: Path, announce: bool = True) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(html_report(payload), encoding="utf-8")
    (output_dir / "report.md").write_text(markdown_report(payload), encoding="utf-8")
    (output_dir / "trace.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run_id", "benchmark_id", "outcome", "mode", "benchmark_correct",
                         "current_fact_correct", "citation_support_rate", "time_compliance_rate",
                         "answer_completeness", "trace_completeness", "tool_call_count", "duration_seconds"])
        for run in payload["runs"]:
            m = run["metrics"]
            writer.writerow([run["run_id"], run["benchmark_id"], run["outcome"], run["mode"],
                             m["benchmark_correct"], m["current_fact_correct"],
                             m["citation_support_rate"], m["time_compliance_rate"],
                             m["answer_completeness"], m["trace_completeness"],
                             m["tool_call_count"], m["duration_seconds"]])
    if announce:
        print(f"Generated report in {output_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).with_name("sample_runs.json"))
    parser.add_argument("--output", type=Path, default=Path("site"))
    args = parser.parse_args()
    write_outputs(args.input, args.output)


if __name__ == "__main__":
    main()
