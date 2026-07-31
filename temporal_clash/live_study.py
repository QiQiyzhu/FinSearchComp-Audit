from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from .live_agent import STRATEGIES, STRATEGY_LABELS


RATE_FIELDS = (
    "api_success_rate",
    "answer_coverage",
    "decision_accuracy",
    "model_decision_accuracy",
    "citation_coverage",
    "source_capture_coverage",
    "declared_temporal_leakage_rate",
    "missing_declared_date_rate",
    "candidate_declared_future_rate",
    "filter_trigger_rate",
)

COUNT_FIELDS = (
    "average_latency_seconds",
    "average_sources_per_run",
    "web_search_calls",
    "input_tokens",
    "output_tokens",
)


def aggregate_repeats(
    repeat_summaries: list[dict[str, dict[str, float]]],
) -> dict[str, dict[str, dict[str, float]]]:
    aggregate: dict[str, dict[str, dict[str, float]]] = {}
    for strategy in STRATEGIES:
        aggregate[strategy] = {}
        for field in (*RATE_FIELDS, *COUNT_FIELDS):
            values = [
                float(summary[strategy][field]) for summary in repeat_summaries
            ]
            aggregate[strategy][field] = {
                "mean": statistics.fmean(values) if values else 0.0,
                "sample_std": statistics.stdev(values) if len(values) > 1 else 0.0,
                "min": min(values) if values else 0.0,
                "max": max(values) if values else 0.0,
            }
    return aggregate


def write_aggregate(
    repeat_summaries: list[dict[str, dict[str, float]]],
    output_dir: Path,
) -> dict[str, dict[str, dict[str, float]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    aggregate = aggregate_repeats(repeat_summaries)
    fields = ["strategy", "metric", "mean", "sample_std", "min", "max"]
    with (output_dir / "aggregate_metrics.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for strategy in STRATEGIES:
            for metric in (*RATE_FIELDS, *COUNT_FIELDS):
                values = aggregate[strategy][metric]
                writer.writerow(
                    {
                        "strategy": STRATEGY_LABELS[strategy],
                        "metric": metric,
                        "mean": f"{values['mean']:.6f}",
                        "sample_std": f"{values['sample_std']:.6f}",
                        "min": f"{values['min']:.6f}",
                        "max": f"{values['max']:.6f}",
                    }
                )

    lines = [
        "# 真实 Web Search Agent 重复实验汇总",
        "",
        f"- 独立重复次数：{len(repeat_summaries)}",
        "- 表中为各重复的均值 ± 样本标准差；仅 3 次重复时不把区间解释为总体性能保证。",
        "",
        "| 策略 | 决策准确率 | 引用覆盖率 | 完整来源捕获率 | 时间泄漏率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for strategy in STRATEGIES:
        values = aggregate[strategy]

        def mean_sd(field: str) -> str:
            item = values[field]
            return f"{item['mean'] * 100:.1f}% ± {item['sample_std'] * 100:.1f}%"

        lines.append(
            "| "
            + " | ".join(
                [
                    STRATEGY_LABELS[strategy],
                    mean_sd("decision_accuracy"),
                    mean_sd("citation_coverage"),
                    mean_sd("source_capture_coverage"),
                    mean_sd("declared_temporal_leakage_rate"),
                ]
            )
            + " |"
        )
    (output_dir / "aggregate_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output_dir / "aggregate_metrics.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return aggregate
