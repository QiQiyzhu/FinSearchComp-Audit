from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_agent import STRATEGIES, STRATEGY_LABELS
from .live_evaluate import (
    answer_is_correct,
    model_answer_is_correct,
    write_metrics,
)
from .live_validate import (
    canonical_sha256,
    validate_live_record,
    validate_protocol_consistency,
)
from .run_live_pilot import BASE_CASES_PATH, load_existing, write_json


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_child(root: Path, relative: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"Path escapes run directory: {relative}")
    return candidate


def selected_records(
    records: list[dict[str, Any]],
    case_ids: list[str],
) -> list[dict[str, Any]]:
    latest_success = {
        (record["case"]["id"], record["strategy"]): record
        for record in records
        if record.get("status") == "ok"
    }
    selected = []
    missing = []
    for case_id in case_ids:
        for strategy in STRATEGIES:
            record = latest_success.get((case_id, strategy))
            if record is None:
                missing.append(f"{case_id}/{strategy}")
            else:
                selected.append(record)
    if missing:
        raise RuntimeError(
            "Cannot finalize an incomplete strategy block: " + ", ".join(missing)
        )
    return selected


def write_case_outcomes(
    records: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    fields = [
        "case_id",
        "question_zh",
        "strategy",
        "model_action",
        "final_action",
        "answer_value",
        "unit",
        "gold_answer",
        "canonical_unit",
        "model_draft_correct",
        "final_decision_correct",
        "filter_triggered",
        "evidence_count",
        "accepted_evidence_count",
        "web_search_calls",
        "source_count",
        "citation_count",
        "latency_seconds",
        "input_tokens",
        "output_tokens",
    ]
    with (output_dir / "case_outcomes.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            result = record.get("result") or {}
            case = record["case"]
            writer.writerow(
                {
                    "case_id": case["id"],
                    "question_zh": case["question_zh"],
                    "strategy": record["strategy"],
                    "model_action": result.get(
                        "model_action", result.get("action")
                    ),
                    "final_action": result.get("action"),
                    "answer_value": result.get("answer_value"),
                    "unit": result.get("unit"),
                    "gold_answer": case.get("gold_answer"),
                    "canonical_unit": case.get("canonical_unit"),
                    "model_draft_correct": int(
                        model_answer_is_correct(record)
                    ),
                    "final_decision_correct": int(answer_is_correct(record)),
                    "filter_triggered": int(
                        bool(result.get("filter_triggered"))
                    ),
                    "evidence_count": len(result.get("evidence") or []),
                    "accepted_evidence_count": len(
                        result.get("accepted_evidence") or []
                    ),
                    "web_search_calls": len(
                        record.get("search_actions") or []
                    ),
                    "source_count": len(record.get("search_sources") or []),
                    "citation_count": len(record.get("api_citations") or []),
                    "latency_seconds": record.get("latency_seconds"),
                    "input_tokens": (record.get("usage") or {}).get(
                        "input_tokens"
                    ),
                    "output_tokens": (record.get("usage") or {}).get(
                        "output_tokens"
                    ),
                }
            )


UNIT_LABELS = {
    "percent": "%",
    "percentage_point": " 个百分点",
    "basis_point": " 个基点",
    "USD_million": " 百万美元",
    "USD_100million": " 亿美元",
    "ratio": "",
    "times": " 倍",
    "shares_per_share": " 股/股",
}


def _display_answer(value: Any, unit: Any) -> str:
    if value is None:
        return "—"
    suffix = UNIT_LABELS.get(str(unit), f" {unit}" if unit else "")
    return f"{value}{suffix}"


def _case_interpretation(records: list[dict[str, Any]]) -> tuple[str, str]:
    by_strategy = {record["strategy"]: record for record in records}
    correct = {
        strategy: answer_is_correct(record)
        for strategy, record in by_strategy.items()
    }
    over_rejected = [
        strategy
        for strategy, record in by_strategy.items()
        if model_answer_is_correct(record)
        and not answer_is_correct(record)
        and (record.get("result") or {}).get("action") == "abstain"
    ]
    if all(correct.values()):
        return "四策略一致正确", "四种策略均给出正确最终答案，严格检查没有降低覆盖率。"
    if over_rejected:
        names = "、".join(STRATEGY_LABELS[item] for item in over_rejected)
        return (
            "过度拒答候选",
            f"{names} 的模型初稿正确，但经过 Gate 后拒答；需要独立补全来源元数据。",
        )
    if correct.get("temporal_prompt") and not correct.get("plain_agent"):
        return "Prompt 修正基线", "时间约束 Prompt 正确而普通 Agent 错误，提示词在本题产生正向作用。"
    if (
        correct.get("metadata_filter") or correct.get("teg_validator")
    ) and not correct.get("plain_agent"):
        return "验证策略修正基线", "至少一种显式验证策略正确，而普通 Agent 错误。"
    if correct.get("plain_agent") and not (
        correct.get("metadata_filter") or correct.get("teg_validator")
    ):
        return "严格策略降低覆盖", "普通 Agent 正确，但严格策略未保留正确最终答案。"
    if not any(correct.values()):
        return "四策略均未解决", "四种策略都没有得到正确最终决策，是后续误差分析的重点。"
    return "策略结果分化", "不同策略结果不一致，需要结合逐条证据和 Gate 触发原因解释。"


def write_case_analysis(
    records: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, int]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_case.setdefault(record["case"]["id"], []).append(record)

    category_counts: dict[str, int] = {}
    sections = [
        f"# {len(by_case)} 道真实案例的四策略结果说明",
        "",
        "> 每一道题都来自同一模型、同一运行协议和真实 Web Search。"
        "“正确初稿 → Gate 拒答”表示模型数值本来正确，但最终因证据元数据未通过而拒答；"
        "它是过度拒答候选，不等同于已经证明 Gate 错误。",
        "",
        "## 结果总览",
        "",
        "| 题目 | 结论类型 | 普通 Agent | 时间 Prompt | 元数据过滤 | 完整验证器 |",
        "|---|---|---|---|---|---|",
    ]

    details: list[str] = []
    order = {strategy: index for index, strategy in enumerate(STRATEGIES)}
    for case_id, case_records in by_case.items():
        case_records.sort(key=lambda item: order[item["strategy"]])
        category, interpretation = _case_interpretation(case_records)
        category_counts[category] = category_counts.get(category, 0) + 1
        case = case_records[0]["case"]
        strategy_results: list[str] = []
        detailed_rows: list[str] = []
        for record in case_records:
            result = record.get("result") or {}
            action = result.get("action")
            draft_correct = model_answer_is_correct(record)
            final_correct = answer_is_correct(record)
            if action == "abstain":
                compact = (
                    "正确初稿→拒答" if draft_correct else "拒答"
                )
            else:
                compact = (
                    f"{_display_answer(result.get('answer_value'), result.get('unit'))}"
                    f"（{'正确' if final_correct else '错误'}）"
                )
            strategy_results.append(compact)
            detailed_rows.append(
                "| {} | {} | {} | {} | {} | {} |".format(
                    STRATEGY_LABELS[record["strategy"]],
                    result.get("model_action", action),
                    action,
                    _display_answer(
                        result.get("answer_value"), result.get("unit")
                    ),
                    "是" if final_correct else "否",
                    len(result.get("accepted_evidence") or []),
                )
            )
        sections.append(
            "| {} | {} | {} |".format(
                case["question_zh"].replace("|", "\\|"),
                category,
                " | ".join(item.replace("|", "\\|") for item in strategy_results),
            )
        )
        details.extend(
            [
                "",
                f"### {case_id}",
                "",
                case["question_zh"],
                "",
                f"- 参考答案：`{_display_answer(case.get('gold_answer'), case.get('canonical_unit'))}`",
                f"- 结论：**{category}**。{interpretation}",
                "",
                "| 策略 | 模型动作 | 最终动作 | 返回值 | 最终正确 | 采用证据数 |",
                "|---|---|---|---|---:|---:|",
                *detailed_rows,
            ]
        )

    category_lines = [
        f"- {category}：{count} 题"
        for category, count in sorted(
            category_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    sections.extend(
        [
            "",
            "## 按失败模式统计",
            "",
            *category_lines,
            "",
            "## 逐题解释",
            *details,
            "",
        ]
    )
    (output_dir / "case_analysis.md").write_text(
        "\n".join(sections), encoding="utf-8"
    )
    return category_counts


def write_bootstrap_intervals(
    records: list[dict[str, Any]],
    output_dir: Path,
    *,
    samples: int = 10_000,
    seed: int = 20260731,
) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        by_case.setdefault(record["case"]["id"], {})[
            record["strategy"]
        ] = record
    case_ids = list(by_case)
    if any(set(by_case[case_id]) != set(STRATEGIES) for case_id in case_ids):
        raise RuntimeError("Bootstrap requires every case x strategy cell")

    rng = random.Random(seed)
    draws = [
        [rng.randrange(len(case_ids)) for _ in case_ids]
        for _ in range(samples)
    ]

    def interval(values: list[float]) -> tuple[float, float, float]:
        estimate = sum(values) / len(values)
        boot = sorted(
            sum(values[index] for index in draw) / len(draw)
            for draw in draws
        )
        lower = boot[int(0.025 * (len(boot) - 1))]
        upper = boot[int(0.975 * (len(boot) - 1))]
        return estimate, lower, upper

    predicates = {
        "decision_accuracy": answer_is_correct,
        "model_decision_accuracy": model_answer_is_correct,
        "answer_coverage": lambda record: (
            (record.get("result") or {}).get("action") == "answer"
        ),
    }
    rows: list[dict[str, Any]] = []
    values_by_metric: dict[str, dict[str, list[float]]] = {}
    for metric, predicate in predicates.items():
        values_by_metric[metric] = {}
        for strategy in STRATEGIES:
            values = [
                float(bool(predicate(by_case[case_id][strategy])))
                for case_id in case_ids
            ]
            values_by_metric[metric][strategy] = values
            estimate, lower, upper = interval(values)
            rows.append(
                {
                    "comparison": STRATEGY_LABELS[strategy],
                    "metric": metric,
                    "estimate": estimate,
                    "ci_lower": lower,
                    "ci_upper": upper,
                    "n_questions": len(case_ids),
                    "method": "question_cluster_bootstrap_percentile",
                    "bootstrap_samples": samples,
                    "seed": seed,
                }
            )

    plain_values = values_by_metric["decision_accuracy"]["plain_agent"]
    for strategy in STRATEGIES:
        if strategy == "plain_agent":
            continue
        strategy_values = values_by_metric["decision_accuracy"][strategy]
        differences = [
            strategy_value - plain_value
            for strategy_value, plain_value in zip(
                strategy_values, plain_values
            )
        ]
        estimate, lower, upper = interval(differences)
        rows.append(
            {
                "comparison": (
                    f"{STRATEGY_LABELS[strategy]} - "
                    f"{STRATEGY_LABELS['plain_agent']}"
                ),
                "metric": "paired_decision_accuracy_difference",
                "estimate": estimate,
                "ci_lower": lower,
                "ci_upper": upper,
                "n_questions": len(case_ids),
                "method": "paired_question_bootstrap_percentile",
                "bootstrap_samples": samples,
                "seed": seed,
            }
        )

    with (output_dir / "confidence_intervals.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "estimate": f"{row['estimate']:.4f}",
                    "ci_lower": f"{row['ci_lower']:.4f}",
                    "ci_upper": f"{row['ci_upper']:.4f}",
                }
            )
    return rows


def write_study_readme(
    output_dir: Path,
    manifest: dict[str, Any],
) -> None:
    scope = manifest["scope"]
    usage = manifest["selected_usage"]
    source = manifest["source_run"]
    protocol = manifest["source_protocol"]
    request_config = protocol["request_config"]
    with (output_dir / "confidence_intervals.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        ci_rows = [
            row
            for row in csv.DictReader(handle)
            if row["metric"] == "decision_accuracy"
        ]
    metric_rows = []
    for strategy in STRATEGIES:
        row = manifest["metrics"][strategy]
        metric_rows.append(
            "| {} | {:.1%} | {:.1%} | {:.1%} | {:.1%} |".format(
                STRATEGY_LABELS[strategy],
                row["decision_accuracy"],
                row["model_decision_accuracy"],
                row["answer_coverage"],
                row["declared_temporal_leakage_rate"],
            )
        )
    ci_lines = [
        "| {} | {:.1%} | {:.1%}–{:.1%} |".format(
            row["comparison"],
            float(row["estimate"]),
            float(row["ci_lower"]),
            float(row["ci_upper"]),
        )
        for row in ci_rows
    ]
    text = f"""# Claude 真实 Web Search Agent：{scope['questions']} 题 × {scope['strategies']} 策略

这是一次真实外部有效性 pilot：实际调用 `{protocol['requested_model']}`，
每条运行强制 Web Search，并保存完整来源、原生 citations、提示词、token、响应 ID 和 raw hash。

## 固定实验条件

- 问题：{scope['questions']} 道，四策略共享同一批题；
- 有效记录：{scope['valid_runs']}；
- 模型：`{protocol['requested_model']}`；
- 推理强度：`{request_config['reasoning_effort']}`；
- 每次最多搜索：{request_config['max_tool_calls']}；
- 运行窗口：{manifest['selected_run_window']['first_record_at']} 至 {manifest['selected_run_window']['last_record_at']}；
- 批次哈希：`{scope['case_batch_sha256']}`。

## 核心结果

| 策略 | 最终决策准确率 | 模型初稿准确率 | 回答覆盖率 | 已采用证据时间泄漏 |
|---|---:|---:|---:|---:|
{chr(10).join(metric_rows)}

这张表不能被解释为模型排名。最终正确率同时受到答案质量和拒答策略影响；
`case_analysis.md` 明确区分了数值错误、拒答和“正确初稿被 Gate 拒绝”。

### 按题目 bootstrap 95% CI

| 策略 | 最终正确率 | 95% CI |
|---|---:|---:|
{chr(10).join(ci_lines)}

区间由 {scope['questions']} 道题配对重采样 10,000 次得到；它描述当前题集上的抽样不确定性，
不包含模型重复运行的随机性。

## 审计规模

- Web Search：{usage['web_search_calls']} 次；
- 捕获的搜索来源：{usage['search_sources']} 个；
- 原生 citations：{usage['api_citations']} 条；
- 输入 / 输出 token：{usage['input_tokens']} / {usage['output_tokens']}；
- 源运行 transport failures：{len(json.loads((output_dir / 'exclusions.json').read_text(encoding='utf-8'))['transport_errors'])}；
- HTTP 尝试区间：{source['http_attempts_lower_bound']}–{source['http_attempts_upper_bound']}，
  批准上限 {source['approved_http_cap']}。

## 文件

- `metrics.csv`：四策略聚合指标；
- `case_outcomes.csv`：{scope['valid_runs']} 个 case × strategy 结果；
- `case_analysis.md`：{scope['questions']} 道题的中文逐题解释与失败类型；
- `confidence_intervals.csv`：按题目配对的 bootstrap 区间及相对普通 Agent 的差值；
- `trace.jsonl`：完整标准化 trace；
- `exclusions.json`：transport errors、invalid records 和排除规则；
- `study_manifest.json`：协议、哈希、运行窗口、预算与限制。

## 结论边界

本轮能说明四种策略在同一模型和题集上的行为差异，并揭示真实网页元数据缺失导致的
过度拒答问题。由于只有 20 题、单模型和单次运行，它仍是 pilot；下一阶段应独立解析
发布日期、重复至少三次，并报告跨运行均值/标准差与层级 bootstrap 置信区间。
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def finalize(
    source_dir: Path,
    output_dir: Path,
    limit: int,
    approved_http_cap: int,
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    if source_dir == output_dir:
        raise ValueError("Source and output directories must differ")

    cases = json.loads(BASE_CASES_PATH.read_text(encoding="utf-8"))
    if not 1 <= limit <= len(cases):
        raise ValueError(f"--limit must be between 1 and {len(cases)}")
    case_ids = [case["id"] for case in cases[:limit]]

    source_trace = source_dir / "trace.jsonl"
    source_manifest = source_dir / "study_manifest.json"
    records = load_existing(source_trace)
    selected = selected_records(records, case_ids)

    record_errors = {
        f"{record['case']['id']}/{record['strategy']}": validate_live_record(
            record, source_dir
        )
        for record in selected
    }
    record_errors = {
        key: errors for key, errors in record_errors.items() if errors
    }
    protocol_errors = validate_protocol_consistency(selected, source_dir)
    if record_errors or protocol_errors:
        raise RuntimeError(
            f"Selected records failed validation: {record_errors}; "
            f"protocol={protocol_errors}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    for record in selected:
        relative = str(record["raw_response_path"])
        source_raw = safe_child(source_dir, relative)
        output_raw = safe_child(output_dir, relative)
        output_raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_raw, output_raw)
        copied = json.loads(output_raw.read_text(encoding="utf-8"))
        if canonical_sha256(copied) != record["raw_response_sha256"]:
            raise RuntimeError(f"Copied raw response hash mismatch: {relative}")

    selected_trace = output_dir / "trace.jsonl"
    selected_trace.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in selected
        ),
        encoding="utf-8",
    )
    summaries = write_metrics(selected, output_dir)
    write_case_outcomes(selected, output_dir)
    case_categories = write_case_analysis(selected, output_dir)
    bootstrap_rows = write_bootstrap_intervals(selected, output_dir)

    selected_keys = {
        (record["case"]["id"], record["strategy"]) for record in selected
    }
    excluded_successes = [
        {
            "case_id": record["case"]["id"],
            "strategy": record["strategy"],
            "run_at": record.get("run_at"),
            "reason": "outside selected fixed question prefix",
        }
        for record in records
        if record.get("status") == "ok"
        and (record["case"]["id"], record["strategy"]) not in selected_keys
    ]
    transport_errors = [
        {
            "case_id": record["case"]["id"],
            "strategy": record["strategy"],
            "run_at": record.get("run_at"),
            "error_type": record.get("error_type"),
            "error": record.get("error"),
        }
        for record in records
        if record.get("status") == "error"
    ]
    invalid_records = [
        {
            "case_id": record["case"]["id"],
            "strategy": record["strategy"],
            "run_at": record.get("run_at"),
            "trace_validation_errors": record.get("trace_validation_errors"),
        }
        for record in records
        if record.get("status") == "invalid"
    ]
    write_json(
        output_dir / "exclusions.json",
        {
            "transport_errors": transport_errors,
            "invalid_records": invalid_records,
            "successful_records_outside_subset": excluded_successes,
        },
    )

    source_study = json.loads(source_manifest.read_text(encoding="utf-8"))
    successful_source = [
        record for record in records if record.get("status") == "ok"
    ]
    successful_http = sum(
        int(record.get("http_attempts") or 0) for record in successful_source
    )
    error_count = len(transport_errors)
    source_searches = sum(
        len(record.get("search_actions") or [])
        for record in successful_source
    )
    selected_searches = sum(
        len(record.get("search_actions") or []) for record in selected
    )
    selected_sources = sum(
        len(record.get("search_sources") or []) for record in selected
    )
    selected_citations = sum(
        len(record.get("api_citations") or []) for record in selected
    )
    input_tokens = sum(
        int((record.get("usage") or {}).get("input_tokens") or 0)
        for record in selected
    )
    output_tokens = sum(
        int((record.get("usage") or {}).get("output_tokens") or 0)
        for record in selected
    )

    full_study = limit == len(cases)
    source_protocol = source_study.get("protocol") or source_study.get(
        "source_protocol"
    )
    if not source_protocol:
        raise RuntimeError("Source study manifest does not contain a protocol")

    manifest = {
        "status": "completed" if full_study else "completed_subset",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_type": (
            "real_web_search_agent_pilot"
            if full_study
            else "real_web_search_agent_prefix_pilot"
        ),
        "scope": {
            "questions": limit,
            "strategies": len(STRATEGIES),
            "valid_runs": len(selected),
            "case_ids": case_ids,
            "selection_rule": (
                f"first {limit} questions in base_cases.json, retaining one latest "
                "strictly valid record for every case x strategy cell"
            ),
            "case_batch_sha256": canonical_sha256(
                [case for case in cases[:limit]]
            ),
        },
        "selected_run_window": {
            "first_record_at": min(
                str(record.get("run_at")) for record in selected
            ),
            "last_record_at": max(
                str(record.get("run_at")) for record in selected
            ),
        },
        "source_run": {
            "directory_name": source_dir.name,
            "source_manifest_status": source_study.get("status"),
            "protocol_sha256": source_study.get("protocol_sha256"),
            "trace_sha256": file_sha256(source_trace),
            "study_manifest_sha256": file_sha256(source_manifest),
            "successful_records": len(successful_source),
            "transport_errors": error_count,
            "invalid_records": len(invalid_records),
            "successful_records_excluded": len(excluded_successes),
            "approved_http_cap": approved_http_cap,
            "http_attempts_lower_bound": successful_http + error_count,
            "http_attempts_upper_bound": successful_http + (2 * error_count),
            "web_search_calls": source_searches,
        },
        "selected_usage": {
            "http_stages": sum(
                int(record.get("http_attempts") or 0) for record in selected
            ),
            "web_search_calls": selected_searches,
            "search_sources": selected_sources,
            "api_citations": selected_citations,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "validation": {
            "record_errors": record_errors,
            "protocol_errors": protocol_errors,
            "all_raw_response_hashes_verified": True,
        },
        "source_protocol": source_protocol,
        "case_analysis_categories": case_categories,
        "bootstrap": {
            "method": "paired question-cluster percentile bootstrap",
            "samples": 10_000,
            "seed": 20260731,
            "rows": len(bootstrap_rows),
        },
        "artifacts": {
            "trace": "trace.jsonl",
            "metrics": "metrics.csv",
            "summary": "summary.md",
            "case_outcomes": "case_outcomes.csv",
            "case_analysis": "case_analysis.md",
            "confidence_intervals": "confidence_intervals.csv",
            "exclusions": "exclusions.json",
            "raw_responses": "local/private; linked by path and SHA-256",
        },
        "metrics": summaries,
        "limitations": [
            (
                "This is the complete planned 20-question pilot, but it remains too "
                "small for a general model ranking."
                if full_study
                else (
                    f"This is a {limit}-question fixed-prefix subset, not the full "
                    f"{len(cases)}-question study."
                )
            ),
            (
                "The completion decision followed a previously stopped partial run; "
                "all 20 fixed questions are retained, so no successful case-strategy "
                "cell was selected by outcome."
                if full_study
                else (
                    "The stop decision was made after data collection began; the fixed "
                    "prefix prevents cherry-picking individual outcomes but was not "
                    "preregistered."
                )
            ),
            "Publication dates remain model-declared until independently verified.",
            (
                f"{error_count} transport failure(s) occurred in the source run and "
                "are excluded from strategy metrics but disclosed in exclusions.json."
            ),
            "This is one run with one model and one relay; repeated and multi-model "
            "experiments are still required.",
        ],
    }
    write_json(output_dir / "study_manifest.json", manifest)
    write_study_readme(output_dir, manifest)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Finalize a complete fixed-prefix or full live study without "
            "cherry-picking individual case-strategy outcomes."
        )
    )
    result.add_argument("--source-dir", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--limit", type=int, required=True)
    result.add_argument("--approved-http-cap", type=int, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    manifest = finalize(
        args.source_dir,
        args.output_dir,
        args.limit,
        args.approved_http_cap,
    )
    print(
        f"Finalized {manifest['scope']['valid_runs']} validated records in "
        f"{args.output_dir}"
    )


if __name__ == "__main__":
    main()
