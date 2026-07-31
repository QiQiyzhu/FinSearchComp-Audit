from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from .live_agent import STRATEGIES, STRATEGY_LABELS
from .live_validate import observed_search_calls


ABS_TOLERANCE = {
    "percent": 0.15,
    "percentage_point": 0.15,
    "basis_point": 0.5,
    "USD_million": 1.0,
    "USD_100million": 1.0,
    "ratio": 0.02,
    "times": 0.03,
    "shares_per_share": 0.01,
}


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text)
    if not match:
        return None
    return float(match.group(0))


def answer_is_correct(record: dict[str, Any]) -> bool:
    result = record.get("result") or {}
    case = record["case"]
    if result.get("action") != "answer":
        return False
    if result.get("unit") != case["canonical_unit"]:
        return False
    observed = parse_number(result.get("answer_value"))
    expected = parse_number(case["gold_answer"])
    if observed is None or expected is None:
        return False
    tolerance = ABS_TOLERANCE.get(case["canonical_unit"], 1e-6)
    return math.isclose(observed, expected, rel_tol=0.0, abs_tol=tolerance)


def _declared_temporal_status(record: dict[str, Any]) -> tuple[bool, bool]:
    result = record.get("result") or {}
    cutoff = date.fromisoformat(record["case"]["cutoff_date"])
    evidence = result.get("evidence") or []
    if not evidence:
        return False, True
    has_future = False
    has_missing = False
    for item in evidence:
        published_at = item.get("published_at")
        if not published_at:
            has_missing = True
            continue
        try:
            has_future = has_future or date.fromisoformat(str(published_at)) > cutoff
        except ValueError:
            has_missing = True
    return has_future, has_missing


def _rate(records: list[dict[str, Any]], predicate) -> float:
    if not records:
        return 0.0
    return sum(bool(predicate(record)) for record in records) / len(records)


def summarize(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    latest_by_run = {
        (record["case"]["id"], record["strategy"]): record for record in records
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in latest_by_run.values():
        grouped[record["strategy"]].append(record)

    summaries: dict[str, dict[str, float]] = {}
    for strategy in STRATEGIES:
        items = grouped.get(strategy, [])
        api_ok = [record for record in items if record.get("status") == "ok"]
        answered = [
            record
            for record in api_ok
            if (record.get("result") or {}).get("action") == "answer"
        ]
        input_tokens = sum(
            int((record.get("usage") or {}).get("input_tokens") or 0)
            for record in api_ok
        )
        output_tokens = sum(
            int((record.get("usage") or {}).get("output_tokens") or 0)
            for record in api_ok
        )
        search_calls = sum(observed_search_calls(record) for record in api_ok)
        source_count = sum(
            len(record.get("search_sources") or []) for record in api_ok
        )
        summaries[strategy] = {
            "runs": float(len(items)),
            "api_success_rate": _rate(
                items, lambda record: record.get("status") == "ok"
            ),
            "answer_coverage": _rate(
                api_ok,
                lambda record: (record.get("result") or {}).get("action") == "answer",
            ),
            "decision_accuracy": _rate(api_ok, answer_is_correct),
            "citation_coverage": _rate(
                answered, lambda record: bool(record.get("api_citations"))
            ),
            "source_capture_coverage": _rate(
                api_ok, lambda record: bool(record.get("search_sources"))
            ),
            "declared_temporal_leakage_rate": _rate(
                answered, lambda record: _declared_temporal_status(record)[0]
            ),
            "missing_declared_date_rate": _rate(
                answered, lambda record: _declared_temporal_status(record)[1]
            ),
            "filter_trigger_rate": _rate(
                api_ok,
                lambda record: bool(
                    (record.get("result") or {}).get("filter_triggered")
                ),
            ),
            "average_latency_seconds": (
                sum(float(record.get("latency_seconds") or 0) for record in api_ok)
                / len(api_ok)
                if api_ok
                else 0.0
            ),
            "web_search_calls": float(search_calls),
            "average_sources_per_run": (
                source_count / len(api_ok) if api_ok else 0.0
            ),
            "input_tokens": float(input_tokens),
            "output_tokens": float(output_tokens),
        }
    return summaries


def write_metrics(
    records: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, dict[str, float]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = summarize(records)
    fields = [
        "strategy",
        "runs",
        "api_success_rate",
        "answer_coverage",
        "decision_accuracy",
        "citation_coverage",
        "source_capture_coverage",
        "declared_temporal_leakage_rate",
        "missing_declared_date_rate",
        "filter_trigger_rate",
        "average_latency_seconds",
        "web_search_calls",
        "average_sources_per_run",
        "input_tokens",
        "output_tokens",
    ]
    with (output_dir / "metrics.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for strategy in STRATEGIES:
            summary = summaries[strategy]
            writer.writerow(
                {
                    "strategy": STRATEGY_LABELS[strategy],
                    "runs": int(summary["runs"]),
                    "api_success_rate": f"{summary['api_success_rate']:.4f}",
                    "answer_coverage": f"{summary['answer_coverage']:.4f}",
                    "decision_accuracy": f"{summary['decision_accuracy']:.4f}",
                    "citation_coverage": f"{summary['citation_coverage']:.4f}",
                    "source_capture_coverage": (
                        f"{summary['source_capture_coverage']:.4f}"
                    ),
                    "declared_temporal_leakage_rate": (
                        f"{summary['declared_temporal_leakage_rate']:.4f}"
                    ),
                    "missing_declared_date_rate": (
                        f"{summary['missing_declared_date_rate']:.4f}"
                    ),
                    "filter_trigger_rate": f"{summary['filter_trigger_rate']:.4f}",
                    "average_latency_seconds": (
                        f"{summary['average_latency_seconds']:.3f}"
                    ),
                    "web_search_calls": int(summary["web_search_calls"]),
                    "average_sources_per_run": (
                        f"{summary['average_sources_per_run']:.3f}"
                    ),
                    "input_tokens": int(summary["input_tokens"]),
                    "output_tokens": int(summary["output_tokens"]),
                }
            )

    lines = [
        "# 真实 Web Search Agent Pilot",
        "",
        "> 这是外部有效性 pilot，不替代 100 条人工扰动的受控实验。来源日期来自 Agent "
        "结构化报告，属于待进一步抓取验证的元数据。",
        "",
        "| 策略 | 成功率 | 覆盖率 | 正确率 | 引用覆盖 | 完整来源捕获 | 声明的时间泄漏 | 日期缺失 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy in STRATEGIES:
        summary = summaries[strategy]

        def pct(key: str) -> str:
            return f"{summary[key] * 100:.1f}%"

        lines.append(
            "| "
            + " | ".join(
                [
                    STRATEGY_LABELS[strategy],
                    pct("api_success_rate"),
                    pct("answer_coverage"),
                    pct("decision_accuracy"),
                    pct("citation_coverage"),
                    pct("source_capture_coverage"),
                    pct("declared_temporal_leakage_rate"),
                    pct("missing_declared_date_rate"),
                ]
            )
            + " |"
        )
    (output_dir / "summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return summaries
