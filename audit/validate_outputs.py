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
        relative = href.split("#", 1)[0]
        if not relative:
            continue
        target = (html_path.parent / relative).resolve()
        require(
            target == root or root in target.parents,
            f"local link escapes output directory: {href}",
        )
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
    live_manifest = json.loads(
        (live_dir / "study_manifest.json").read_text(encoding="utf-8")
    )
    expected_live_runs = int(live_manifest["scope"]["valid_runs"])
    expected_live_cases = int(live_manifest["scope"]["questions"])

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
    for run in payload["runs"]:
        require(html.escape(run["label"]) in html_text, f"index.html is missing label: {run['label']}")
    for required_showcase_text in (
        "30 秒看懂研究",
        "100 条受控实验",
        "真实 Web Search Agent",
        "确定性协议验证",
        "三个来自真实 trace 的例子",
        f"{expected_live_runs} 条记录全部通过严格 trace 校验",
    ):
        require(
            required_showcase_text in html_text,
            f"index.html is missing teacher-showcase text: {required_showcase_text}",
        )
    for heading in ("完整任务轨迹", "6 个成功案例和 6 个失败案例", "普通网页搜索 vs 金融数据接口"):
        require(heading in report_text, f"report.md is missing section: {heading}")

    live_html = (output_dir / "live-pilot.html").read_text(encoding="utf-8")
    for required_live_text in (
        f"{expected_live_cases} 题 × 4 策略 Pilot",
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
    require(len(live_metric_rows) == 4, "live pilot must contain four strategy rows")
    require(
        len(confidence_rows) == 15,
        "live pilot must contain 12 strategy intervals and 3 paired differences",
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
    validate_local_links(output_dir, output_dir / "live-pilot.html")
    validate_local_links(output_dir, output_dir / "temporal-audit.html")

    return {"files": len(OUTPUT_FILES), **trace_summary}


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
