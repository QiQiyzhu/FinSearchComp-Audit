"""Validate recorded FinSearchComp traces and generated report artifacts."""

from __future__ import annotations

import argparse
import csv
import html
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


RUN_FIELDS = {
    "run_id",
    "benchmark_id",
    "label",
    "outcome",
    "mode",
    "question",
    "reference_answer",
    "strategy",
    "search_queries",
    "tool_calls",
    "calculation",
    "final_answer",
    "citations",
    "source_audits",
    "metrics",
}
METRIC_FIELDS = {
    "benchmark_correct",
    "current_fact_correct",
    "citation_support_rate",
    "time_compliance_rate",
    "answer_completeness",
    "trace_completeness",
    "tool_call_count",
    "duration_seconds",
}
RATIO_FIELDS = {
    "citation_support_rate",
    "time_compliance_rate",
    "answer_completeness",
    "trace_completeness",
}
OUTPUT_FILES = (
    "index.html",
    "xbrl-study.html",
    "live-pilot.html",
    "report.md",
    "trace.json",
    "metrics.csv",
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
XBRL_FILES = (
    "README.md",
    "metrics.json",
    "case_outcomes.csv",
    "gold_audit.json",
    "study_manifest.json",
    "exclusions.json",
    "trace.jsonl",
)
AGENTIC_FILES = (
    "index.html",
    "budget-curve.svg",
    "e4_gap_ablation.csv",
    "e4_gap_detail.csv",
    "e5_sufficiency_gate.csv",
    "e5_sufficiency_detail.csv",
    "e6_budget_sweep.csv",
    "e6_budget_detail.csv",
    "summary.json",
)


class ValidationError(ValueError):
    """Raised when an audit trace or generated artifact is inconsistent."""


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        href = values.get("href")
        if href:
            self.hrefs.append(href)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def is_web_url(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_local_links(output_dir: Path, html_path: Path) -> None:
    parser = LinkCollector()
    parser.feed(html_path.read_text(encoding="utf-8"))
    root = output_dir.resolve()
    for href in parser.hrefs:
        if href.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative = urlparse(href).path
        if not relative:
            continue
        target = (html_path.parent / relative).resolve()
        require(
            target == root or root in target.parents,
            f"local link escapes output directory: {href}",
        )
        if target.is_dir():
            target = target / "index.html"
        require(target.is_file(), f"broken local link in {html_path.name}: {href}")


def validate_payload(payload: dict, strict_demo: bool = False) -> dict:
    require(isinstance(payload, dict), "Top-level JSON must be an object")
    require(isinstance(payload.get("runs"), list), "`runs` must be a list")
    runs = payload["runs"]
    require(runs, "`runs` must not be empty")

    ids: set[str] = set()
    outcomes = {"success": 0, "failure": 0}

    for index, run in enumerate(runs, start=1):
        prefix = f"run[{index}]"
        require(isinstance(run, dict), f"{prefix} must be an object")
        missing = RUN_FIELDS - set(run)
        require(not missing, f"{prefix} missing fields: {sorted(missing)}")
        require(run["outcome"] in outcomes, f"{prefix} has invalid outcome")
        outcomes[run["outcome"]] += 1

        run_id = run["run_id"]
        require(isinstance(run_id, str) and run_id, f"{prefix}.run_id is empty")
        require(run_id not in ids, f"duplicate run_id: {run_id}")
        ids.add(run_id)

        for field in ("label", "question", "reference_answer", "strategy", "final_answer"):
            require(
                isinstance(run[field], str) and run[field].strip(),
                f"{run_id}.{field} must be non-empty",
            )

        require(isinstance(run["search_queries"], list), f"{run_id}.search_queries must be a list")
        require(isinstance(run["tool_calls"], list), f"{run_id}.tool_calls must be a list")
        require(isinstance(run["citations"], list), f"{run_id}.citations must be a list")
        require(
            isinstance(run["source_audits"], list) and run["source_audits"],
            f"{run_id}.source_audits must be a non-empty list",
        )

        metrics = run["metrics"]
        require(isinstance(metrics, dict), f"{run_id}.metrics must be an object")
        missing_metrics = METRIC_FIELDS - set(metrics)
        require(
            not missing_metrics,
            f"{run_id}.metrics missing fields: {sorted(missing_metrics)}",
        )
        for field in RATIO_FIELDS:
            value = metrics[field]
            require(
                isinstance(value, (int, float)) and 0 <= value <= 1,
                f"{run_id}.metrics.{field} must be between 0 and 1",
            )
        require(
            metrics["tool_call_count"] == len(run["tool_calls"]),
            f"{run_id}.tool_call_count does not match tool_calls",
        )

        known_urls = {
            item.get("url")
            for item in run["citations"]
            if isinstance(item, dict) and item.get("url")
        }
        for call_index, call in enumerate(run["tool_calls"], start=1):
            require(isinstance(call, dict), f"{run_id}.tool_calls[{call_index}] must be an object")
            require(call.get("tool"), f"{run_id}.tool_calls[{call_index}].tool is empty")
            require(
                isinstance(call.get("arguments"), dict),
                f"{run_id}.tool_calls[{call_index}].arguments must be an object",
            )
            require(
                isinstance(call.get("result_summary"), str) and call["result_summary"].strip(),
                f"{run_id}.tool_calls[{call_index}].result_summary is empty",
            )
            source_url = call.get("source_url")
            if source_url:
                require(is_web_url(source_url), f"{run_id} has an invalid tool source URL")
                known_urls.add(source_url)

        for citation_index, citation in enumerate(run["citations"], start=1):
            require(
                isinstance(citation, dict) and is_web_url(citation.get("url")),
                f"{run_id}.citations[{citation_index}] has an invalid URL",
            )

        for audit_index, audit in enumerate(run["source_audits"], start=1):
            require(isinstance(audit, dict), f"{run_id}.source_audits[{audit_index}] must be an object")
            require(
                audit.get("support") in {"pass", "fail", "partial", "unverifiable"},
                f"{run_id}.source_audits[{audit_index}] has invalid support",
            )
            require(
                audit.get("time_check") in {"pass", "fail", "partial", "unverifiable"},
                f"{run_id}.source_audits[{audit_index}] has invalid time_check",
            )
            require(audit.get("claim") and audit.get("reason"), f"{run_id} has an incomplete source audit")
            citation_url = audit.get("citation_url")
            if citation_url:
                require(is_web_url(citation_url), f"{run_id} has an invalid audit URL")
                require(
                    citation_url in known_urls,
                    f"{run_id} audit URL is not present in citations or tool calls",
                )

        gates = (
            metrics["current_fact_correct"],
            metrics["citation_support_rate"] == 1,
            metrics["time_compliance_rate"] == 1,
            metrics["answer_completeness"] == 1,
        )
        if run["outcome"] == "success":
            require(all(gates), f"{run_id} is marked success but fails a trust gate")
            require(metrics["benchmark_correct"], f"{run_id} success must match benchmark")
            require(metrics["trace_completeness"] == 1, f"{run_id} success trace is incomplete")
            require(run["search_queries"], f"{run_id} success must save search queries")
            require(run["citations"], f"{run_id} success must save citations")
        else:
            require(not all(gates), f"{run_id} is marked failure but passes every trust gate")

    if strict_demo:
        require(len(runs) == 12, "strict demo must contain exactly 12 runs")
        require(
            outcomes == {"success": 6, "failure": 6},
            "strict demo must contain 6 success and 6 failure runs",
        )

    return {"runs": len(runs), **outcomes}


def validate_output_dir(output_dir: Path, payload: dict, strict_demo: bool = False) -> dict:
    for filename in OUTPUT_FILES:
        target = output_dir / filename
        require(target.is_file() and target.stat().st_size > 0, f"missing or empty output: {target}")
    live_dir = output_dir / "live-pilot"
    for filename in LIVE_PILOT_FILES:
        target = live_dir / filename
        require(
            target.is_file() and target.stat().st_size > 0,
            f"missing or empty live-pilot output: {target}",
        )
    fusion_dir = output_dir / "atlas-fusion"
    for filename in (
        "README.md",
        "metrics.json",
        "case_outcomes.csv",
        "study_manifest.json",
        "trace.jsonl",
    ):
        target = fusion_dir / filename
        require(
            target.is_file() and target.stat().st_size > 0,
            f"missing or empty atlas-fusion output: {target}",
        )
    xbrl_dir = output_dir / "atlas-pit-xbrl"
    for filename in XBRL_FILES:
        target = xbrl_dir / filename
        require(
            target.is_file() and target.stat().st_size > 0,
            f"missing or empty atlas-pit-xbrl output: {target}",
        )
    advanced_dir = output_dir / "advanced-rag"
    advanced_files = (
        "README.md",
        "retrieval_per_query.csv",
        "retrieval_summary.csv",
        "system_per_query.csv",
        "system_summary.csv",
        "traces.jsonl",
    )
    for filename in advanced_files:
        target = advanced_dir / filename
        require(
            target.is_file() and target.stat().st_size > 0,
            f"missing or empty advanced-rag output: {target}",
        )
    agentic_dir = output_dir / "agentic-eval"
    for filename in AGENTIC_FILES:
        target = agentic_dir / filename
        require(
            target.is_file() and target.stat().st_size > 0,
            f"missing or empty agentic evaluation output: {target}",
        )
    agentic_summary = json.loads((agentic_dir / "summary.json").read_text(encoding="utf-8"))
    require(agentic_summary["protocol"]["cases"] == 8, "agentic fixture must contain 8 cases")
    gap_rows = {
        row["method"]: row for row in agentic_summary["e4_gap_planner"]
    }
    require(
        gap_rows["plain_one_shot"]["answer_accuracy"] == 0.75,
        "E4 plain one-shot accuracy must remain 75%",
    )
    require(
        gap_rows["structured_gap_planner"]["answer_accuracy"] == 1.0,
        "E4 structured planner accuracy must remain 100%",
    )
    gate_rows = {
        row["method"]: row for row in agentic_summary["e5_sufficiency_gate"]
    }
    require(
        gate_rows["sufficiency_gate"]["correct_abstention_rate"] == 1.0,
        "E5 gate correct abstention must remain 100%",
    )
    require(
        gate_rows["sufficiency_gate"]["unsupported_answer_rate"] == 0.0,
        "E5 gate unsupported-answer rate must remain 0%",
    )
    budget_rows = {
        int(row["budget"]): row for row in agentic_summary["e6_budget_sweep"]
    }
    require(
        budget_rows[5]["answer_accuracy"] == budget_rows[8]["answer_accuracy"] == 1.0,
        "E6 budgets 5 and 8 must remain at 100% accuracy",
    )
    require(
        budget_rows[5]["mean_cost_proxy"] == budget_rows[8]["mean_cost_proxy"],
        "E6 early stopping must keep cost flat after sufficiency",
    )
    game_dir = output_dir / "game-qa"
    for filename in ("index.html", "README.md", "report.json", "trace.json"):
        target = game_dir / filename
        require(
            target.is_file() and target.stat().st_size > 0,
            f"missing or empty game-qa output: {target}",
        )
    game_report = json.loads((game_dir / "report.json").read_text(encoding="utf-8"))
    game_trace = json.loads((game_dir / "trace.json").read_text(encoding="utf-8"))
    require(game_report.get("passed") is True, "game-qa reproducibility checks failed")
    require(
        game_trace.get("schema_version") == "game-qa-trace-1.0",
        "game-qa trace schema mismatch",
    )
    require(
        all(event.get("status") == "ok" for event in game_trace.get("events", [])),
        "game-qa trace contains a failed demo event",
    )
    platform_dir = output_dir / "platform"
    for filename in ("index.html", "README.md", "platform_demo.json", "openapi.json"):
        target = platform_dir / filename
        require(
            target.is_file() and target.stat().st_size > 0,
            f"missing or empty platform output: {target}",
        )
    platform_demo = json.loads(
        (platform_dir / "platform_demo.json").read_text(encoding="utf-8")
    )
    platform_openapi = json.loads(
        (platform_dir / "openapi.json").read_text(encoding="utf-8")
    )
    require(platform_demo.get("passed") is True, "platform demo checks failed")
    require(
        all(platform_demo.get("checks", {}).values()),
        "platform demo contains a failed invariant",
    )
    for path in (
        "/api/v1/query",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/trace",
        "/api/v1/evaluations",
        "/api/v1/runs/{run_id}/replay",
    ):
        require(path in platform_openapi["paths"], f"OpenAPI is missing path: {path}")
    live_manifest = json.loads(
        (live_dir / "study_manifest.json").read_text(encoding="utf-8")
    )
    expected_live_runs = int(live_manifest["scope"]["valid_runs"])
    expected_live_cases = int(live_manifest["scope"]["questions"])
    expected_live_strategies = int(live_manifest["scope"]["strategies"])

    trace = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
    trace_summary = validate_payload(trace, strict_demo=strict_demo)
    expected_ids = [run["run_id"] for run in payload["runs"]]
    actual_ids = [run["run_id"] for run in trace["runs"]]
    require(actual_ids == expected_ids, "trace.json run order or IDs differ from input")

    with (output_dir / "metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(len(rows) == len(payload["runs"]), "metrics.csv row count does not match runs")
    require([row["run_id"] for row in rows] == expected_ids, "metrics.csv run IDs differ from input")

    html_text = (output_dir / "index.html").read_text(encoding="utf-8")
    report_text = (output_dir / "report.md").read_text(encoding="utf-8")
    for required_showcase_text in (
        "ATLAS-PIT-XBRL",
        "20道全新冻结题上",
        "结果在运行前冻结",
        "普通搜索错或拒答的7题",
        "Gold与协议",
        "Query Decomposition for RAG",
        "FinMRAGBench",
        "40条有效Trace",
        "100% vs 65%",
        "0 / 32 vs 80 / 392",
        "联合可靠回答率",
        "同一条可靠性原则，也能落到三消客户端测试",
        "5 → 3 → 12",
        "从一次性实验，升级为可排队、可诊断、可重放的平台",
        "SQLite WAL",
        "Structured Gap Planner",
        "Sufficiency Gate",
        "Budget Sweep",
        "无依据回答66.7% → 0%",
    ):
        require(
            required_showcase_text in html_text,
            f"index.html is missing teacher-showcase text: {required_showcase_text}",
        )
    require("100 条受控实验" not in html_text, "homepage must not display offline benchmark data")
    xbrl_html = (output_dir / "xbrl-study.html").read_text(encoding="utf-8")
    for required_xbrl_text in (
        "ATLAS-PIT-XBRL",
        "20道题，每个答案和每次时间暴露都可检查",
        "65%",
        "100%",
        "+35pp",
        "40有效 + 1排除Trace",
    ):
        require(
            required_xbrl_text in xbrl_html,
            f"xbrl-study.html is missing text: {required_xbrl_text}",
        )
    with (xbrl_dir / "case_outcomes.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        xbrl_outcomes = list(csv.DictReader(handle))
    require(
        len(xbrl_outcomes) == 20,
        "ATLAS-PIT-XBRL result must contain 20 questions",
    )
    require(
        sum(int(row["plain_correct"]) for row in xbrl_outcomes) == 13,
        "published baseline score must be 13/20",
    )
    require(
        sum(int(row["xbrl_correct"]) for row in xbrl_outcomes) == 20,
        "published ATLAS-PIT-XBRL score must be 20/20",
    )
    require(
        sum(int(row["plain_final_future_evidence"]) for row in xbrl_outcomes) == 7,
        "published baseline must contain 7 confirmed future final-evidence items",
    )
    require(
        sum(int(row["xbrl_final_future_evidence"]) for row in xbrl_outcomes) == 0,
        "published ATLAS-PIT-XBRL result must have zero future final evidence",
    )
    for heading in ("完整任务轨迹", "6 个成功案例和 6 个失败案例", "普通网页搜索 vs 金融数据接口"):
        require(heading in report_text, f"report.md is missing section: {heading}")

    live_html = (output_dir / "live-pilot.html").read_text(encoding="utf-8")
    for required_live_text in (
        f"{expected_live_cases} 题 × {expected_live_strategies} 策略 Pilot",
        "如何解释结果",
        f"全部 {expected_live_cases} 题的结果说明",
        "选择规则与排除",
        "实际逐题例子",
    ):
        require(
            required_live_text in live_html,
            f"live-pilot.html is missing text: {required_live_text}",
        )
    with (live_dir / "metrics.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        live_metric_rows = list(csv.DictReader(handle))
    with (live_dir / "case_outcomes.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        live_outcomes = list(csv.DictReader(handle))
    with (live_dir / "confidence_intervals.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        confidence_rows = list(csv.DictReader(handle))
    live_trace_lines = [
        line
        for line in (live_dir / "trace.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    require(
        len(live_metric_rows) == expected_live_strategies,
        "live pilot must contain one row per strategy",
    )
    require(
        len(confidence_rows) == 4 * expected_live_strategies - 1,
        "live pilot confidence interval row count is inconsistent with strategies",
    )
    require(
        all(
            int(row["n_questions"]) == expected_live_cases
            for row in confidence_rows
        ),
        "bootstrap rows must use the complete question batch",
    )
    require(
        len(live_outcomes) == expected_live_runs,
        f"live pilot must contain {expected_live_runs} outcomes",
    )
    require(
        len(live_trace_lines) == expected_live_runs,
        f"live pilot must contain {expected_live_runs} trace records",
    )
    require(
        len({row["case_id"] for row in live_outcomes})
        == expected_live_cases,
        f"live pilot must contain {expected_live_cases} unique cases",
    )
    for line_number, line in enumerate(live_trace_lines, start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as error:
            raise ValidationError(
                f"live trace line {line_number} is not valid JSON"
            ) from error
    validate_local_links(output_dir, output_dir / "index.html")
    validate_local_links(output_dir, output_dir / "xbrl-study.html")
    validate_local_links(output_dir, output_dir / "live-pilot.html")
    validate_local_links(output_dir, output_dir / "temporal-audit.html")
    validate_local_links(output_dir, agentic_dir / "index.html")

    return {
        "files": len(OUTPUT_FILES) + len(advanced_files) + len(AGENTIC_FILES),
        **trace_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).with_name("sample_runs.json"),
        help="Recorded audit-run JSON",
    )
    parser.add_argument("--output", type=Path, default=Path("site"), help="Generated artifact directory")
    parser.add_argument("--strict-demo", action="store_true", help="Require the published 6-success/6-failure demo")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    summary = validate_payload(payload, strict_demo=args.strict_demo)
    artifact_summary = validate_output_dir(args.output, payload, strict_demo=args.strict_demo)
    print(
        "Validated "
        f"{summary['runs']} runs ({summary['success']} success, {summary['failure']} failure) "
        f"and {artifact_summary['files']} report artifacts"
    )


if __name__ == "__main__":
    main()
