"""Generate a self-contained FinSearchComp audit report from recorded runs."""

from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from audit.xbrl_site import (
    RESULT_FILES as XBRL_FILES,
    build_homepage,
    build_study_page,
    load_xbrl_result,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_PILOT_DIR = (
    REPO_ROOT
    / "temporal_clash"
    / "results"
    / "live_pilot_20q_atlas_sonnet5_20260813"
)
BASELINE_LIVE_PILOT_DIR = (
    REPO_ROOT
    / "temporal_clash"
    / "results"
    / "live_pilot_20q_claude_complete"
)
FUSION_RESULT_DIR = (
    REPO_ROOT
    / "temporal_clash"
    / "results"
    / "atlas_fusion_20q_sonnet5_20260813"
)
LIVE_PILOT_FILES = (
    "README.md",
    "metrics.csv",
    "case_outcomes.csv",
    "case_analysis.md",
    "confidence_intervals.csv",
    "exclusions.json",
    "study_manifest.json",
    "trace.jsonl",
)
FUSION_FILES = (
    "README.md",
    "metrics.json",
    "case_outcomes.csv",
    "study_manifest.json",
    "trace.jsonl",
)
STRATEGY_LABELS = {
    "plain_agent": "普通搜索 Agent",
    "temporal_prompt": "时间约束 Prompt",
    "metadata_filter": "元数据过滤器",
    "teg_validator": "完整证据验证器",
    "atlas_rag": "ATLAS-RAG（校准版）",
}
UNIT_LABELS = {
    "percent": "%",
    "percentage_point": "个百分点",
    "basis_point": "个基点",
    "USD_million": "百万美元",
    "USD_100million": "亿美元",
    "shares_per_share": "股/股",
}
SHOWCASE_CASES = (
    "apple_2024_sales",
    "us_cpi_dec_2024",
    "nasdaq_vs_sp500_2024",
)


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.0f}%"


def live_percent(value: str | float) -> str:
    return f"{float(value) * 100:.0f}%"


def display_number(value: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:g}"


def display_value_with_unit(value: str, unit: str) -> str:
    unit_label = UNIT_LABELS.get(unit, unit)
    separator = "" if unit_label == "%" else " "
    return f"{display_number(value)}{separator}{unit_label}".strip()


def load_live_pilot(result_dir: Path = LIVE_PILOT_DIR) -> dict:
    missing = [
        filename for filename in LIVE_PILOT_FILES if not (result_dir / filename).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Live pilot artifacts are incomplete in {result_dir}: {missing}"
        )
    with (result_dir / "metrics.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        metrics = list(csv.DictReader(handle))
    with (result_dir / "case_outcomes.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        outcomes = list(csv.DictReader(handle))
    manifest = json.loads(
        (result_dir / "study_manifest.json").read_text(encoding="utf-8")
    )
    exclusions = json.loads(
        (result_dir / "exclusions.json").read_text(encoding="utf-8")
    )
    expected_runs = int(manifest["scope"]["valid_runs"])
    if len(metrics) != len(manifest["metrics"]) or len(outcomes) != expected_runs:
        raise ValueError(
            "Published live pilot must contain one metric row per strategy and "
            f"{expected_runs} outcomes"
        )
    return {
        "result_dir": result_dir,
        "metrics": metrics,
        "outcomes": outcomes,
        "manifest": manifest,
        "exclusions": exclusions,
    }


def metric_for(live_pilot: dict, strategy: str) -> dict:
    label = STRATEGY_LABELS[strategy]
    return next(
        row for row in live_pilot["metrics"] if row["strategy"] == label
    )


def live_finding(live_pilot: dict) -> dict[str, str | int]:
    plain = metric_for(live_pilot, "plain_agent")
    teg = metric_for(live_pilot, "teg_validator")
    atlas = metric_for(live_pilot, "atlas_rag")
    plain_accuracy = float(plain["decision_accuracy"])
    teg_accuracy = float(teg["decision_accuracy"])
    atlas_accuracy = float(atlas["decision_accuracy"])
    over_rejections = sum(
        row["strategy"] in {"metadata_filter", "teg_validator", "atlas_rag"}
        and row["model_draft_correct"] == "1"
        and row["final_decision_correct"] == "0"
        and row["final_action"] == "abstain"
        for row in live_pilot["outcomes"]
    )
    title = "ATLAS 缓解严格 Gate 过度拒答，但未超过普通搜索"
    conclusion = (
        "同轮 ATLAS 比完整验证器提高 10 个百分点，但仍低于普通 Agent 10 个百分点；"
        "因此只能声称改进了严格审计基线，不能声称全面超过普通搜索。"
    )
    if over_rejections:
        detail = (
            f"严格策略共有 {over_rejections} 条正确初稿最终被 Gate 拒答，"
            "说明元数据缺失会直接损失覆盖率。"
        )
    else:
        detail = (
            "本轮没有出现正确初稿被 Gate 拒答；准确率下降主要来自模型更早拒答"
            "或初稿本身错误，仍需与第一轮共同解读。"
        )
    return {
        "title": title,
        "big": f"{teg_accuracy:.0%} → {atlas_accuracy:.0%}",
        "plain_accuracy": f"{plain_accuracy:.0%}",
        "teg_accuracy": f"{teg_accuracy:.0%}",
        "teg_draft_accuracy": f"{float(teg['model_decision_accuracy']):.0%}",
        "teg_coverage": f"{float(teg['answer_coverage']):.0%}",
        "atlas_accuracy": f"{atlas_accuracy:.0%}",
        "atlas_coverage": f"{float(atlas['answer_coverage']):.0%}",
        "over_rejections": over_rejections,
        "detail": detail,
        "conclusion": conclusion,
    }


def live_metrics_rows(live_pilot: dict) -> str:
    rows = []
    for row in live_pilot["metrics"]:
        rows.append(
            "<tr><td>{}</td><td><b>{}</b></td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td></tr>".format(
                html.escape(row["strategy"]),
                live_percent(row["decision_accuracy"]),
                live_percent(row["model_decision_accuracy"]),
                live_percent(row["answer_coverage"]),
                live_percent(row["citation_coverage"]),
                live_percent(row["source_capture_coverage"]),
            )
        )
    return "".join(rows)


def pilot_comparison_rows(baseline: dict, latest: dict) -> str:
    rows = []
    for strategy, label in STRATEGY_LABELS.items():
        if strategy == "atlas_rag":
            continue
        first = metric_for(baseline, strategy)
        second = metric_for(latest, strategy)
        rows.append(
            "<tr><td>{}</td><td><b>{}</b></td><td>{}</td><td><b>{}</b></td>"
            "<td>{}</td></tr>".format(
                html.escape(label),
                live_percent(first["decision_accuracy"]),
                live_percent(first["answer_coverage"]),
                live_percent(second["decision_accuracy"]),
                live_percent(second["answer_coverage"]),
            )
        )
    return "".join(rows)


def showcase_story(case_rows: list[dict]) -> tuple[str, str]:
    correct = [
        STRATEGY_LABELS[row["strategy"]]
        for row in case_rows
        if row["final_decision_correct"] == "1"
    ]
    abstained = sum(row["final_action"] == "abstain" for row in case_rows)
    over_rejected = sum(
        row["model_draft_correct"] == "1"
        and row["final_decision_correct"] == "0"
        and row["final_action"] == "abstain"
        for row in case_rows
    )
    if len(correct) == len(case_rows):
        headline = f"{len(case_rows)} 策略一致正确"
    elif not correct:
        headline = f"{len(case_rows)} 策略均未形成正确答案"
    elif over_rejected:
        headline = "正确初稿被严格规则拒答"
    elif correct == ["普通搜索 Agent"]:
        headline = "只有普通搜索回答正确"
    else:
        headline = "策略结果出现分歧"
    correct_text = "、".join(correct) if correct else "无"
    interpretation = (
        f"本轮最终答对的策略：{correct_text}；{len(case_rows)} 种策略中有 {abstained} 个拒答。"
    )
    if over_rejected:
        interpretation += f"其中 {over_rejected} 个属于模型初稿正确但被本地 Gate 拒绝。"
    return headline, interpretation


def live_case_cards(live_pilot: dict) -> str:
    outcomes = live_pilot["outcomes"]
    cards = []
    for case_id in SHOWCASE_CASES:
        case_rows = [row for row in outcomes if row["case_id"] == case_id]
        if len(case_rows) != len(STRATEGY_LABELS):
            raise ValueError(
                f"Expected {len(STRATEGY_LABELS)} strategies for showcase case {case_id}"
            )
        case_rows.sort(
            key=lambda row: list(STRATEGY_LABELS).index(row["strategy"])
        )
        headline, interpretation = showcase_story(case_rows)
        first = case_rows[0]
        gold = display_value_with_unit(
            first["gold_answer"], first["canonical_unit"]
        )
        result_items = []
        for row in case_rows:
            strategy = STRATEGY_LABELS[row["strategy"]]
            if row["final_action"] == "abstain":
                if row["model_draft_correct"] == "1":
                    result = "正确初稿 → Gate 拒答"
                    state = "warn"
                else:
                    result = "拒答"
                    state = "neutral"
            else:
                answer = display_value_with_unit(
                    row["answer_value"], row["unit"]
                )
                correct = row["final_decision_correct"] == "1"
                result = f"{answer} · {'正确' if correct else '错误'}"
                state = "correct" if correct else "wrong"
            result_items.append(
                f'<li><span>{html.escape(strategy)}</span>'
                f'<b class="{state}">{html.escape(result)}</b></li>'
            )
        searches = sum(int(row["web_search_calls"]) for row in case_rows)
        sources = sum(int(row["source_count"]) for row in case_rows)
        citations = sum(int(row["citation_count"]) for row in case_rows)
        cards.append(
            f"""<article class="live-case">
            <div class="case-kicker">{html.escape(headline)}</div>
            <h3>{html.escape(first['question_zh'])}</h3>
            <p class="gold">参考答案：<b>{html.escape(gold)}</b></p>
            <ul class="strategy-results">{''.join(result_items)}</ul>
            <p>{html.escape(interpretation)}</p>
            <div class="case-meta"><span>{searches} 次搜索</span>
            <span>{sources} 个来源</span><span>{citations} 条引用</span></div>
            </article>"""
        )
    return "".join(cards)


def live_all_case_rows(live_pilot: dict) -> str:
    grouped: dict[str, list[dict]] = {}
    for row in live_pilot["outcomes"]:
        grouped.setdefault(row["case_id"], []).append(row)
    strategy_order = {
        strategy: index for index, strategy in enumerate(STRATEGY_LABELS)
    }
    rendered = []
    for case_rows in grouped.values():
        case_rows.sort(key=lambda row: strategy_order[row["strategy"]])
        by_strategy = {row["strategy"]: row for row in case_rows}
        correct = {
            strategy: row["final_decision_correct"] == "1"
            for strategy, row in by_strategy.items()
        }
        over_rejected = any(
            row["strategy"] in {"metadata_filter", "teg_validator", "atlas_rag"}
            and row["model_draft_correct"] == "1"
            and row["final_decision_correct"] == "0"
            and row["final_action"] == "abstain"
            for row in case_rows
        )
        if all(correct.values()):
            category = f"{len(case_rows)} 策略一致正确"
        elif over_rejected:
            category = "过度拒答候选"
        elif correct.get("temporal_prompt") and not correct.get("plain_agent"):
            category = "Prompt 修正基线"
        elif (
            correct.get("metadata_filter")
            or correct.get("teg_validator")
            or correct.get("atlas_rag")
        ) and not correct.get("plain_agent"):
            category = "验证策略修正基线"
        elif correct.get("plain_agent") and not (
            correct.get("metadata_filter")
            or correct.get("teg_validator")
            or correct.get("atlas_rag")
        ):
            category = "严格策略降低覆盖"
        elif not any(correct.values()):
            category = f"{len(case_rows)} 策略均未解决"
        else:
            category = "策略结果分化"

        cells = []
        for row in case_rows:
            if row["final_action"] == "abstain":
                value = (
                    "正确初稿→拒答"
                    if row["model_draft_correct"] == "1"
                    else "拒答"
                )
                css_class = "warn" if row["model_draft_correct"] == "1" else "neutral"
            else:
                value = display_value_with_unit(
                    row["answer_value"], row["unit"]
                )
                is_correct = row["final_decision_correct"] == "1"
                value = f"{value} · {'正确' if is_correct else '错误'}"
                css_class = "correct" if is_correct else "wrong"
            cells.append(
                f'<td class="{css_class}">{html.escape(value)}</td>'
            )
        first = case_rows[0]
        gold = display_value_with_unit(
            first["gold_answer"], first["canonical_unit"]
        )
        rendered.append(
            "<tr><td>{}</td><td><b>{}</b></td><td>{}</td>{}</tr>".format(
                html.escape(first["question_zh"]),
                html.escape(category),
                html.escape(gold),
                "".join(cells),
            )
        )
    return "".join(rendered)


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


def html_report(
    payload: dict,
    live_pilot: dict | None = None,
    baseline_pilot: dict | None = None,
) -> str:
    live_pilot = live_pilot or load_live_pilot()
    baseline_pilot = baseline_pilot or load_live_pilot(BASELINE_LIVE_PILOT_DIR)
    live_manifest = live_pilot["manifest"]
    baseline_manifest = baseline_pilot["manifest"]
    live_scope = live_manifest["scope"]
    live_usage = live_manifest["selected_usage"]
    live_model = live_manifest["source_protocol"]["requested_model"]
    live_date = (
        datetime.fromisoformat(
            live_manifest["selected_run_window"]["first_record_at"]
        )
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .date()
        .isoformat()
    )
    metric_rows = live_metrics_rows(live_pilot)
    comparison_rows = pilot_comparison_rows(baseline_pilot, live_pilot)
    case_cards = live_case_cards(live_pilot)
    finding = live_finding(live_pilot)
    fusion_metrics = json.loads(
        (FUSION_RESULT_DIR / "metrics.json").read_text(encoding="utf-8")
    )
    live_questions = int(live_scope["questions"])
    live_runs = int(live_scope["valid_runs"])
    live_strategies = int(live_scope["strategies"])
    baseline_runs = int(baseline_manifest["scope"]["valid_runs"])
    total_live_runs = live_runs + baseline_runs
    total_searches = int(live_usage["web_search_calls"]) + int(
        baseline_manifest["selected_usage"]["web_search_calls"]
    )
    total_sources = int(live_usage["search_sources"]) + int(
        baseline_manifest["selected_usage"]["search_sources"]
    )
    baseline_model = baseline_manifest["source_protocol"]["requested_model"]
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
<meta name="description" content="金融研究 Agent 的时间可靠性 Benchmark、证据审计与真实搜索 pilot">
<title>FinSearchComp-Audit · 金融 Agent 时间可靠性</title>
<style>
:root{{--ink:#111827;--muted:#667085;--navy:#07111f;--blue:#2563eb;--cyan:#31c7d5;--green:#14804a;--red:#b42318;--amber:#b54708;--bg:#f4f7fb;--line:#e2e8f0}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 Inter,ui-sans-serif,system-ui,"Microsoft YaHei",sans-serif}}
header{{background:radial-gradient(circle at 86% 12%,#164e63 0,transparent 28%),linear-gradient(130deg,#07111f,#10244b 68%,#173d59);color:white;padding:26px 24px 92px;overflow:hidden}}.wrap{{max-width:1160px;margin:auto}}
.hero-top{{display:flex;align-items:center;justify-content:space-between;gap:16px}}.eyebrow{{font-size:13px;font-weight:800;letter-spacing:.13em;text-transform:uppercase;color:#93c5fd}}
.live-pill{{border:1px solid #5eead466;background:#0f766e44;color:#99f6e4;border-radius:999px;padding:6px 12px;font-size:12px;font-weight:800;letter-spacing:.06em}}
h1{{font-size:clamp(38px,6vw,68px);line-height:1.04;letter-spacing:-.045em;margin:45px 0 20px;max-width:940px}}h1 span{{color:#67e8f9}}
.hero-copy{{max-width:820px;color:#d8e6fb;font-size:19px}}.hero-actions{{display:flex;gap:12px;flex-wrap:wrap;margin:30px 0}}
.button{{display:inline-block;padding:11px 17px;border-radius:10px;text-decoration:none;font-weight:800}}.button.primary{{background:#67e8f9;color:#082f49}}.button.secondary{{border:1px solid #ffffff55;color:white}}
nav{{margin-top:34px;display:flex;gap:22px;flex-wrap:wrap}}nav a{{color:#c7d7ee;text-decoration:none;font-size:14px}}nav a:hover{{color:white}}
main{{padding:36px 24px 80px}}.summary,.grid,.compare,.research-grid,.live-cases,.pilot-grid,.timeline,.atlas-flow{{display:grid;gap:18px}}
.grid,.compare,.research-grid{{grid-template-columns:repeat(2,1fr)}}.summary{{grid-template-columns:repeat(4,1fr);margin-top:-74px}}
.stat,.card,.panel,.compare section,.live-case,.finding{{background:white;border:1px solid var(--line);border-radius:16px;box-shadow:0 10px 32px #14264a12}}
.stat,.card,.panel,.compare section{{padding:22px}}.stat b{{display:block;font-size:30px;line-height:1.2;color:#164e63}}.stat small{{display:block;margin-top:5px;color:var(--muted)}}
h2{{font-size:clamp(27px,4vw,38px);line-height:1.2;letter-spacing:-.025em;margin:60px 0 18px}}h3{{line-height:1.35}}
.section-label{{display:inline-block;color:#0e7490;font-size:12px;font-weight:900;letter-spacing:.14em;margin-top:58px}}.section-label+h2{{margin-top:8px}}
.success{{border-top:4px solid var(--green)}}.failure{{border-top:4px solid var(--red)}}.badge,.case-kicker{{display:inline-block;padding:3px 10px;border-radius:99px;background:#eaf8f0;color:var(--green);font-weight:800;font-size:13px}}.failure .badge{{background:#fff0ee;color:var(--red)}}
.question,footer,.section-copy{{color:var(--muted)}}code{{background:#eef2f7;padding:2px 6px;border-radius:5px;word-break:break-word}}pre{{background:#101828;color:#eef4ff;padding:18px;border-radius:12px;overflow:auto}}pre code{{background:none;color:inherit;padding:0}}.metrics,.case-meta,.artifact-links{{display:flex;gap:8px;flex-wrap:wrap}}.metrics span,.case-meta span{{background:#f2f4f7;border-radius:8px;padding:5px 9px}}
.panel{{overflow:auto}}.research-grid section{{background:white;border-top:4px solid #164e63;border-radius:14px;padding:20px;box-shadow:0 8px 28px #14264a12}}.research-grid h3{{margin-top:0}}
.research-grid a{{color:#1d4ed8;font-weight:750;text-decoration:none}}.timeline{{grid-template-columns:repeat(4,1fr);margin:24px 0}}.step{{position:relative;background:#0f1f38;color:#e6f2ff;border-radius:15px;padding:20px;min-height:174px}}.step b{{display:block;color:#67e8f9;font-size:12px;letter-spacing:.12em;margin-bottom:12px}}.step h3{{margin:0 0 8px}}.step p{{margin:0;color:#bdd0e8;font-size:14px}}.atlas-flow{{grid-template-columns:repeat(5,1fr);margin:22px 0}}.atlas-flow div{{background:#e9f7fa;border:1px solid #b6e3e8;border-radius:12px;padding:15px;text-align:center;font-weight:800;color:#164e63}}.archive{{margin-top:48px;background:white;border:1px solid var(--line);border-radius:16px;padding:20px}}.archive summary{{cursor:pointer;font-size:20px;font-weight:850;color:#164e63}}.archive[open] summary{{margin-bottom:24px}}
.lead{{font-size:18px;background:#e9f7fa;border-radius:14px;padding:20px;border:1px solid #b6e3e8}}.boundary{{background:#fff8e8;border:1px solid #efd99d;border-radius:12px;padding:15px}}
.pilot-grid{{grid-template-columns:minmax(0,1.65fr) minmax(260px,.75fr);align-items:stretch}}.finding{{padding:25px;background:linear-gradient(145deg,#fff7ed,#fff);border-color:#fed7aa}}.finding h3{{color:#9a3412;margin-top:0}}.finding .big{{font-size:44px;line-height:1;font-weight:900;color:#c2410c;margin:18px 0 8px}}.finding p:last-child{{margin-bottom:0}}
.live-cases{{grid-template-columns:repeat(3,1fr)}}.live-case{{padding:22px;display:flex;flex-direction:column}}.live-case h3{{min-height:76px}}.live-case p{{color:#475467}}.gold{{background:#f0f9ff;border-radius:9px;padding:9px 11px}}
.strategy-results{{list-style:none;margin:4px 0 15px;padding:0;border-top:1px solid var(--line)}}.strategy-results li{{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid var(--line);padding:9px 0;font-size:14px}}.strategy-results b{{text-align:right}}.correct{{color:var(--green)}}.wrong{{color:var(--red)}}.warn{{color:var(--amber)}}.neutral{{color:var(--muted)}}.case-meta{{margin-top:auto;font-size:12px}}
.artifact-links{{margin:18px 0 0}}.artifact-links a{{background:#e8f1ff;color:#1d4ed8;text-decoration:none;border-radius:9px;padding:8px 12px;font-weight:700}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:11px;border-bottom:1px solid #e7eaf0;text-align:left;white-space:nowrap}}th{{background:#f8fafc;color:#344054;font-size:13px}}
footer{{margin-top:48px;padding-top:24px;border-top:1px solid var(--line)}}
@media(max-width:900px){{.summary{{grid-template-columns:repeat(2,1fr)}}.live-cases,.pilot-grid,.timeline{{grid-template-columns:1fr}}.atlas-flow{{grid-template-columns:repeat(2,1fr)}}.live-case h3{{min-height:0}}}}
@media(max-width:650px){{header{{padding-bottom:70px}}.hero-top{{align-items:flex-start;flex-direction:column}}.summary,.grid,.compare,.research-grid,.atlas-flow{{grid-template-columns:1fr}}.summary{{margin-top:-54px}}.strategy-results li{{align-items:flex-start;flex-direction:column;gap:2px}}.strategy-results b{{text-align:left}}}}
</style></head><body><header><div class="wrap"><div class="hero-top"><p class="eyebrow">FYP · Temporal Reliability Benchmark</p>
<span class="live-pill">REAL ATLAS · {live_date}</span></div>
<h1>从时间证据审计，升级为<br><span>可纠错的高级金融 RAG。</span></h1>
<p class="hero-copy">FinSearchComp-Audit 将 2024–2026 顶会中的自适应检索、时间感知排序、
冲突仲裁和选择性回答落到金融 point-in-time 场景，并用 20×5 的 Sonnet 真实
Web Search 实验及跨轨迹融合检验开放网页中的可靠性。</p>
<div class="hero-actions"><a class="button primary" href="#live-pilot">查看 20×5 真实实验</a>
<a class="button secondary" href="#advanced-rag">查看 ATLAS-RAG</a></div>
<nav><a href="#evolution">研究演进</a><a href="#top-papers">顶会技术</a><a href="#model-comparison">历史对照</a>
<a href="#advanced-rag">ATLAS-RAG</a><a href="#controlled">受控实验</a><a href="#reproduce">复现</a>
<a href="live-pilot.html">最新 Pilot 报告</a></nav>
</div></header><main class="wrap"><section class="summary">
<div class="stat"><b>20×5</b><small>最新同模型真实对照</small></div>
<div class="stat"><b>{total_live_runs}</b><small>严格验证的真实 trace</small></div>
<div class="stat"><b>{total_searches}</b><small>真实 Web Search</small></div>
<div class="stat"><b>{total_sources:,}</b><small>保存的完整来源</small></div></section>
<h2 id="evolution">研究如何从审计基线升级到 ATLAS-RAG</h2>
<p class="lead"><b>研究问题：</b>当金融搜索 Agent 遇到未来信息、错期间、错版本或错单位时，显式的证据审计能否降低错误证据采用率，同时保留安全证据？</p>
<div class="timeline"><article class="step"><b>01 · AUDIT</b><h3>保存并审计轨迹</h3><p>从 12 条成功/失败案例出发，检查答案、来源、时间和工具调用。</p></article>
<article class="step"><b>02 · BENCHMARK</b><h3>构造受控冲突</h3><p>20 个真实金融问题 × 5 类证据条件，隔离日期、期间、版本和单位错误。</p></article>
<article class="step"><b>03 · LIVE LLM</b><h3>真实模型验证</h3><p>Sonnet 运行 20×5 策略，保留 100 条严格有效 Web Search trace。</p></article>
<article class="step"><b>04 · ATLAS-RAG</b><h3>把失败机制变成系统</h3><p>路由、冲突仲裁、校准 Gate 与跨轨迹 Fusion 形成闭环。</p></article></div>
<div class="research-grid">
<section><h3>研究空白</h3><p>现有 Benchmark 多关注最终答案正确率，难以发现“答案碰巧正确，但证据在当时不可用”的时间穿越。</p></section>
<section><h3>我构建的内容</h3><p>20 个真实金融问题、100 条人工控制证据、四种策略、Temporal Robustness Gap 和逐证据审计 trace。</p></section>
<section><h3>受控实验发现</h3><p>普通策略准确率 20%，完整验证器在人工标注元数据下达到 100%，验证了日期、期间、版本和单位检查机制。</p></section>
<section><h3>真实实验发现</h3><p>严格 Gate 在开放网页上因日期元数据缺失而过度拒答。真实结果与受控上限不同，这正是当前最重要的研究发现。</p></section>
</div>
<span class="section-label">TOP-CONFERENCE RAG · 2024–2026</span>
<h2 id="top-papers">顶会技术如何进入项目</h2>
<div class="research-grid">
<section><h3>自适应检索与路由</h3><p>Adaptive-RAG、R³AG 启发系统先判断问题类型，再选择行情、财报、官方统计或 Web 来源。</p><a href="https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/TOP_CONFERENCE_RAG_2026.md">查看论文与实现映射 →</a></section>
<section><h3>相关性 × 时间新鲜度</h3><p>Re³ 启发检索阶段同时考虑语义相关性、截止日、数据期间、版本与来源效用。</p><a href="#advanced-rag">查看 ATLAS 检索结果 →</a></section>
<section><h3>冲突感知证据仲裁</h3><p>Astute RAG、FaithfulRAG 与 SeCon-RAG 启发事实级冲突边和 listwise 来源仲裁。</p><a href="advanced-rag/README.md">查看证据图设计 →</a></section>
<section><h3>纠错检索与选择性回答</h3><p>Self-RAG、DRAGIN 与 GRIP 启发低置信度补检索；证据仍不足时明确拒答。</p><a href="advanced-rag/traces.jsonl">查看状态轨迹 →</a></section>
</div>
<span class="section-label">HISTORICAL BASELINE</span>
<h2 id="model-comparison">第一轮 Sonnet 与当前同模型四个旧策略</h2>
<p class="section-copy">两轮都调用真实 Sonnet 5 和 Web Search，但运行日期、提示实现与输出预算不同，
所以这里只展示历史演进，不把跨时间差异当作严格因果效果。</p>
<div class="panel"><table><thead><tr><th>策略</th><th>Sonnet 正确率</th><th>Sonnet 覆盖率</th>
<th>当前轮正确率</th><th>当前轮覆盖率</th></tr></thead><tbody>{comparison_rows}</tbody></table></div>
<div class="artifact-links"><a href="https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/temporal_clash/results/live_pilot_20q_claude_complete/README.md">第一轮 Sonnet 研究卡</a>
<a href="live-pilot.html">当前 20×5 研究卡</a></div>
<span class="section-label">LIVE WEB SEARCH STUDY</span>
<h2 id="live-pilot">ATLAS 真实 Web Search Agent：{live_questions} 题 × {live_strategies} 策略</h2>
<p class="section-copy">同一个 <code>{html.escape(live_model)}</code>、同一批问题、相同推理强度与搜索上限，
只改变 {live_strategies} 种策略。{live_runs} 条记录全部通过严格 trace 校验；所有已回答记录都有原生引用，
所有运行都保存了完整搜索来源。</p>
<div class="pilot-grid"><div class="panel"><table><thead><tr><th>策略</th><th>最终正确率</th>
<th>模型初稿正确率</th><th>回答覆盖率</th><th>引用覆盖</th><th>完整来源</th></tr></thead>
<tbody>{metric_rows}</tbody></table></div>
<aside class="finding"><h3>{finding['title']}</h3><div class="big">{finding['big']}</div>
<p>完整证据验证器最终正确率为 {finding['teg_accuracy']}，单轨迹 ATLAS 为
{finding['atlas_accuracy']}，普通 Agent 为 {finding['plain_accuracy']}。{finding['detail']}</p>
<p>{finding['conclusion']}</p></aside></div>
<div class="artifact-links"><a href="live-pilot.html">阅读完整研究卡</a>
<a href="live-pilot/case_outcomes.csv">下载逐题结果</a>
<a href="live-pilot/case_analysis.md">阅读 20 道逐题说明</a>
<a href="live-pilot/trace.jsonl">查看 {live_runs} 条 trace</a>
<a href="live-pilot/exclusions.json">查看排除记录</a></div>
<h2 id="live-cases">三个来自真实 trace 的例子</h2>
<p class="section-copy">以下不是演示脚本，而是 {live_date} 实际运行记录的逐题对照。
每张卡片都汇总同一道题的 {live_strategies} 种策略；完整 {live_questions} 题结果和中文解释可从上方下载。</p>
<div class="live-cases">{case_cards}</div>
<h2 id="atlas-fusion">ATLAS-Fusion：跨五条搜索轨迹的证据仲裁</h2>
<p class="section-copy">探索性 Fusion 不新增搜索，而是让同一 Sonnet 对每题五条已锁定真实搜索轨迹做
listwise 仲裁。它的计算成本高于单策略，因此单独报告，不能伪装成同预算比较。</p>
<div class="pilot-grid"><div class="panel"><table><thead><tr><th>方法</th><th>最终正确率</th>
<th>仲裁初稿正确率</th><th>回答覆盖率</th></tr></thead><tbody>
<tr><td>普通搜索 Agent</td><td>{finding['plain_accuracy']}</td><td>{finding['plain_accuracy']}</td><td>75%</td></tr>
<tr><td>单轨迹 ATLAS</td><td>{finding['atlas_accuracy']}</td><td>45%</td><td>{finding['atlas_coverage']}</td></tr>
<tr><td><b>ATLAS-Fusion</b></td><td><b>{fusion_metrics['decision_accuracy']:.0%}</b></td>
<td><b>{fusion_metrics['model_decision_accuracy']:.0%}</b></td><td>{fusion_metrics['answer_coverage']:.0%}</td></tr>
</tbody></table></div><aside class="finding"><h3>诚实结论</h3><div class="big">55% / 65%</div>
<p>Fusion 最终准确率与普通 Agent 持平，但仲裁初稿达到 65%，等于五轨迹 oracle 上限。</p>
<p>Gate 仍拒掉部分正确派生答案；本仓库不继续用同一 20 题调阈值，避免测试集过拟合。</p></aside></div>
<div class="artifact-links"><a href="atlas-fusion/README.md">阅读 Fusion 研究卡</a>
<a href="atlas-fusion/case_outcomes.csv">下载 Fusion 逐题结果</a>
<a href="atlas-fusion/trace.jsonl">查看 20 条 Fusion trace</a></div>
<h2 id="controlled">100 条受控实验：四策略对照</h2>
<div class="panel"><table><thead><tr><th>方法</th><th>决策准确率 ↑</th><th>挑战准确率 ↑</th><th>TRG ↓</th><th>检测 F1 ↑</th></tr></thead><tbody>
<tr><td>普通 Agent</td><td>20.0%</td><td>0.0%</td><td>100.0%</td><td>0.0%</td></tr>
<tr><td>时间约束 Prompt</td><td>40.0%</td><td>25.0%</td><td>75.0%</td><td>40.0%</td></tr>
<tr><td>元数据过滤器</td><td>80.0%</td><td>75.0%</td><td>25.0%</td><td>85.7%</td></tr>
<tr><td><b>完整证据验证器</b></td><td><b>100.0%</b></td><td><b>100.0%</b></td><td><b>0.0%</b></td><td><b>100.0%</b></td></tr>
</tbody></table></div>
<p class="boundary"><b>结论边界：</b>受控实验是确定性协议验证，不是真实 LLM 排名。
真实 {live_questions}×{live_strategies} pilot 表明开放网页中的日期缺失和模型拒答倾向都会改变结论，因此受控与真实实验必须分开报告。</p>
<span class="section-label">ADVANCED TEMPORAL RAG</span>
<h2 id="advanced-rag">ATLAS-RAG：从静态 Top-K 到自适应检索与冲突仲裁</h2>
<p class="section-copy">受 2024–2026 年 Adaptive RAG、temporal retrieval、GraphRAG 和
conflict-aware RAG 启发，新增自适应来源路由、BM25/向量特征/时间/来源效用融合、
事实级冲突图、低置信度纠错检索与选择性回答。实现不会读取 gold、是否扰动等评测标签。</p>
<div class="atlas-flow"><div>问题分析<br>来源路由</div><div>混合检索<br>时间排序</div><div>事实冲突图<br>listwise 仲裁</div><div>低置信度<br>纠错检索</div><div>回答 / 拒答<br>完整 trace</div></div>
<div class="pilot-grid"><div class="panel"><table><thead><tr><th>方法</th><th>Recall@5 ↑</th>
<th>MRR@10 ↑</th><th>Top-5 未来证据率 ↓</th></tr></thead><tbody>
<tr><td>BM25</td><td>95%</td><td>0.563</td><td>37%</td></tr>
<tr><td>Hybrid RRF</td><td>95%</td><td>0.642</td><td>42%</td></tr>
<tr><td><b>ATLAS temporal</b></td><td><b>100%</b></td><td><b>0.929</b></td><td><b>30%</b></td></tr>
</tbody></table></div><aside class="finding"><h3>选择性回答</h3><div class="big">95% / 100%</div>
<p>20 题回答覆盖率为 95%，已回答样本准确率为 100%；一道低置信度题在纠错检索后仍选择拒答。</p>
<p>这些是受控离线结果，不是论文复现或真实 Web 的 SOTA 声明。</p></aside></div>
<div class="artifact-links"><a href="advanced-rag/README.md">阅读离线实验报告</a>
<a href="advanced-rag/retrieval_per_query.csv">下载逐题检索结果</a>
<a href="advanced-rag/system_per_query.csv">下载逐题决策结果</a>
<a href="advanced-rag/traces.jsonl">查看完整状态 trace</a></div>
<h2 id="reproduce">一分钟复现</h2><div class="panel"><p><b>确定性复现：</b>从保存的 Agent 轨迹重新生成报告并验证一致性；不会把记录数据冒充成实时搜索。</p>
<pre><code>git clone https://github.com/QiQiyzhu/FinSearchComp-Audit.git
cd FinSearchComp-Audit
python reproduce.py</code></pre>
<p>无需 API Key。命令会验证 {run_count} 条保存轨迹，运行 100 条受控时间可靠性实例，
并重建 ATLAS-RAG 的检索与选择性回答评测。</p></div>
<details class="archive"><summary>展开早期搜索审计基线：12 个成功/失败案例</summary>
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
</details>
<footer>真实实验：<a href="live-pilot.html">研究卡</a> ·
<a href="live-pilot/trace.jsonl">{live_runs} 条 trace</a> ·
受控实验：<a href="temporal-audit.html">100 条实例</a> ·
高级 RAG：<a href="advanced-rag/README.md">ATLAS-RAG</a> ·
历史审计：<a href="report.md">report.md</a> ·
<a href="https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/REPRODUCIBILITY.md">复现说明</a></footer>
</main></body></html>"""


def live_pilot_report(live_pilot: dict) -> str:
    manifest = live_pilot["manifest"]
    scope = manifest["scope"]
    usage = manifest["selected_usage"]
    source = manifest["source_run"]
    metric_rows = live_metrics_rows(live_pilot)
    case_cards = live_case_cards(live_pilot)
    all_case_rows = live_all_case_rows(live_pilot)
    finding = live_finding(live_pilot)
    model = manifest["source_protocol"]["requested_model"]
    questions = int(scope["questions"])
    valid_runs = int(scope["valid_runs"])
    strategies = int(scope["strategies"])
    transport_errors = len(live_pilot["exclusions"]["transport_errors"])
    excluded_successes = len(
        live_pilot["exclusions"]["successful_records_outside_subset"]
    )
    if excluded_successes:
        selection_text = (
            f"公开指标按固定数据集顺序保留前 {questions} 道、每题 {strategies} 策略完整的问题，"
            f"共 {valid_runs} 条；另有 {excluded_successes} 条完整前缀外的成功记录未纳入比较。"
        )
    else:
        selection_text = (
            f"公开指标保留预定的全部 {questions} 道问题与 {strategies} 种策略，共 "
            f"{valid_runs} 条有效记录；没有按结果排除任何成功的 case–strategy 单元。"
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Claude 真实 Web Search Agent {questions}题×{strategies}策略 pilot 的结果、案例和审计产物">
<title>真实 {questions}×{strategies} Pilot · FinSearchComp-Audit</title>
<style>
:root{{--ink:#101828;--muted:#667085;--line:#e4e7ec;--green:#14804a;--red:#b42318;--amber:#b54708}}
*{{box-sizing:border-box}}body{{margin:0;background:#f6f8fb;color:var(--ink);font:16px/1.65 Inter,system-ui,"Microsoft YaHei",sans-serif}}
header{{background:linear-gradient(130deg,#07111f,#163b59);color:white;padding:58px 24px}}.wrap{{max-width:1120px;margin:auto}}
.back{{color:#a5f3fc;text-decoration:none;font-weight:700}}h1{{font-size:clamp(34px,5vw,58px);line-height:1.08;letter-spacing:-.035em;margin:28px 0 16px}}header p{{color:#d7e5f4;max-width:820px;font-size:18px}}
main{{padding:38px 24px 80px}}h2{{font-size:clamp(26px,4vw,38px);margin-top:52px}}.facts,.live-cases{{display:grid;gap:18px}}.facts{{grid-template-columns:repeat(4,1fr);margin-top:-65px}}
.fact,.panel,.live-case,.note{{background:white;border:1px solid var(--line);border-radius:16px;box-shadow:0 10px 30px #14264a12}}.fact,.panel,.live-case,.note{{padding:22px}}.fact b{{display:block;font-size:30px;color:#155e75}}.fact span{{color:var(--muted);font-size:13px}}
.panel{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#f8fafc;font-size:13px}}.all-cases td:first-child{{min-width:280px;white-space:normal}}.all-cases td:not(:first-child){{min-width:125px}}
.live-cases{{grid-template-columns:repeat(3,1fr)}}.live-case{{display:flex;flex-direction:column}}.live-case h3{{min-height:76px}}.case-kicker{{display:inline-block;background:#ecfdf3;color:var(--green);border-radius:999px;padding:3px 9px;font-size:12px;font-weight:800}}
.gold{{background:#f0f9ff;border-radius:9px;padding:9px 11px}}.strategy-results{{list-style:none;margin:4px 0 15px;padding:0;border-top:1px solid var(--line)}}.strategy-results li{{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:9px 0;font-size:14px}}.strategy-results b{{text-align:right}}
.correct{{color:var(--green)}}.wrong{{color:var(--red)}}.warn{{color:var(--amber)}}.neutral{{color:var(--muted)}}.case-meta,.links{{display:flex;gap:8px;flex-wrap:wrap}}.case-meta{{margin-top:auto;font-size:12px}}.case-meta span{{background:#f2f4f7;border-radius:8px;padding:5px 9px}}
.note{{background:#fff8e8;border-color:#efd99d}}.links a{{background:#e8f1ff;color:#1d4ed8;text-decoration:none;border-radius:9px;padding:9px 12px;font-weight:700}}
footer{{margin-top:48px;color:var(--muted)}}code{{background:#eef2f6;border-radius:5px;padding:2px 6px}}
@media(max-width:850px){{.facts{{grid-template-columns:repeat(2,1fr)}}.live-cases{{grid-template-columns:1fr}}.live-case h3{{min-height:0}}}}
@media(max-width:520px){{.facts{{grid-template-columns:1fr}}.strategy-results li{{flex-direction:column;gap:2px}}.strategy-results b{{text-align:left}}}}
</style></head><body><header><div class="wrap"><a class="back" href="index.html">← 返回项目首页</a>
<h1>Claude 真实 Web Search Agent<br>{questions} 题 × {strategies} 策略 Pilot</h1>
<p>一个模型、同一批问题、相同推理强度和搜索上限，只改变策略。
这是外部有效性 pilot，不是完整 benchmark 或模型排名。</p></div></header>
<main class="wrap"><section class="facts">
<div class="fact"><b>{valid_runs}</b><span>严格有效记录</span></div>
<div class="fact"><b>{int(usage['web_search_calls'])}</b><span>真实 Web Search</span></div>
<div class="fact"><b>{int(usage['search_sources'])}</b><span>完整来源</span></div>
<div class="fact"><b>{int(usage['api_citations'])}</b><span>原生 citations</span></div>
</section>
<h2>核心结果</h2><p>请求和实际模型均为 <code>{html.escape(model)}</code>。
所有已回答记录的引用覆盖率为 100%，全部 {valid_runs} 条记录都保存了完整来源。</p>
<div class="panel"><table><thead><tr><th>策略</th><th>最终正确率</th>
<th>模型初稿正确率</th><th>回答覆盖率</th><th>引用覆盖</th><th>完整来源</th></tr></thead>
<tbody>{metric_rows}</tbody></table></div>
<h2>如何解释结果</h2><div class="note"><b>{finding['title']}。</b>
完整验证器的模型初稿正确率为 {finding['teg_draft_accuracy']}，最终正确率为
{finding['teg_accuracy']}，回答覆盖率为 {finding['teg_coverage']}。元数据过滤器和完整验证器合计有
{finding['over_rejections']} 条正确初稿最终被拒答。这说明下一步需要独立元数据获取和拒答校准；
{finding['conclusion']}</div>
<h2>实际逐题例子</h2><div class="live-cases">{case_cards}</div>
<h2>全部 {questions} 题的结果说明</h2>
<p>“正确初稿→拒答”表示模型给出的数字正确，但 Gate 因证据元数据未通过而改变为拒答；
它是需要复核的过度拒答候选。完整证据与触发原因保存在 trace 和中文逐题说明中。</p>
<div class="panel"><table class="all-cases"><thead><tr><th>问题</th><th>结论类型</th><th>参考答案</th>
<th>普通 Agent</th><th>时间 Prompt</th><th>元数据过滤</th><th>完整验证器</th><th>ATLAS-RAG</th></tr></thead>
<tbody>{all_case_rows}</tbody></table></div>
<h2>选择规则与排除</h2><div class="panel"><p>源运行共产生
<b>{source['successful_records']} 条成功记录</b>。{selection_text}</p>
<p>另有 {transport_errors} 次中转连接中断。总 HTTP 尝试估计为
{source['http_attempts_lower_bound']}–{source['http_attempts_upper_bound']} 次，
未超过批准上限 {source['approved_http_cap']}。这些错误均写入排除记录。</p></div>
<h2>下载可审计产物</h2><div class="links">
<a href="live-pilot/case_outcomes.csv">逐题结果 CSV</a>
<a href="live-pilot/case_analysis.md">{questions} 道中文逐题说明</a>
<a href="live-pilot/metrics.csv">策略指标 CSV</a>
<a href="live-pilot/confidence_intervals.csv">Bootstrap 95% CI</a>
<a href="live-pilot/trace.jsonl">{valid_runs} 条标准化 trace</a>
<a href="live-pilot/exclusions.json">排除记录</a>
<a href="live-pilot/study_manifest.json">研究清单</a>
<a href="https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/LITERATURE_AND_ROADMAP.md">相关论文与升级路线</a></div>
<footer>原始供应商响应仅保存在本地，公开 trace 保存其相对路径和 SHA-256；API 密钥没有进入仓库。</footer>
</main></body></html>"""


def write_outputs(input_path: Path, output_dir: Path, announce: bool = True) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    live_pilot = load_live_pilot()
    xbrl_result = load_xbrl_result()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(
        build_homepage(xbrl_result), encoding="utf-8"
    )
    (output_dir / "xbrl-study.html").write_text(
        build_study_page(xbrl_result), encoding="utf-8"
    )
    (output_dir / "live-pilot.html").write_text(
        live_pilot_report(live_pilot), encoding="utf-8"
    )
    live_output = output_dir / "live-pilot"
    live_output.mkdir(parents=True, exist_ok=True)
    for filename in LIVE_PILOT_FILES:
        shutil.copy2(live_pilot["result_dir"] / filename, live_output / filename)
    fusion_output = output_dir / "atlas-fusion"
    fusion_output.mkdir(parents=True, exist_ok=True)
    for filename in FUSION_FILES:
        shutil.copy2(FUSION_RESULT_DIR / filename, fusion_output / filename)
    xbrl_output = output_dir / "atlas-pit-xbrl"
    xbrl_output.mkdir(parents=True, exist_ok=True)
    for filename in XBRL_FILES:
        shutil.copy2(xbrl_result["result_dir"] / filename, xbrl_output / filename)
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
