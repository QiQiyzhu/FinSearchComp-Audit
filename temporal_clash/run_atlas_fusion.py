from __future__ import annotations

import argparse
import csv
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atlas_fusion import AtlasFusionClient, FUSION_LABEL
from .live_agent import RequestConfig, credential_is_available, redact_sensitive
from .live_evaluate import answer_is_correct, model_answer_is_correct
from .live_validate import canonical_sha256, validate_live_record
from .run_live_pilot import append_jsonl, load_existing, write_json


SOURCE_STRATEGIES = (
    "plain_agent",
    "temporal_prompt",
    "metadata_filter",
    "teg_validator",
    "atlas_rag",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_source_groups(source_dir: Path) -> list[list[dict[str, Any]]]:
    records = load_existing(source_dir / "trace.jsonl")
    valid = [
        record
        for record in records
        if not validate_live_record(record, source_dir)
    ]
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    order: list[str] = []
    for record in valid:
        case_id = record["case"]["id"]
        if case_id not in grouped:
            grouped[case_id] = {}
            order.append(case_id)
        grouped[case_id][record["strategy"]] = record
    missing = [
        f"{case_id}/{strategy}"
        for case_id in order
        for strategy in SOURCE_STRATEGIES
        if strategy not in grouped[case_id]
    ]
    if missing:
        raise RuntimeError("Incomplete fusion input: " + ", ".join(missing))
    return [
        [grouped[case_id][strategy] for strategy in SOURCE_STRATEGIES]
        for case_id in order
    ]


def _rate(records: list[dict[str, Any]], predicate) -> float:
    return sum(bool(predicate(record)) for record in records) / len(records)


def write_evaluation(
    fusion_records: list[dict[str, Any]],
    source_groups: list[list[dict[str, Any]]],
    output_dir: Path,
) -> dict[str, Any]:
    source_plain = [group[0] for group in source_groups]
    current = {
        record["case"]["id"]: record
        for record in fusion_records
        if record.get("status") == "ok"
    }
    ordered = [current[group[0]["case"]["id"]] for group in source_groups]
    metrics = {
        "runs": len(ordered),
        "decision_accuracy": _rate(ordered, answer_is_correct),
        "model_decision_accuracy": _rate(ordered, model_answer_is_correct),
        "answer_coverage": _rate(
            ordered, lambda item: item["result"]["action"] == "answer"
        ),
        "filter_trigger_rate": _rate(
            ordered, lambda item: bool(item["result"].get("filter_triggered"))
        ),
        "source_plain_decision_accuracy": _rate(source_plain, answer_is_correct),
        "http_attempts": sum(int(item.get("http_attempts") or 0) for item in ordered),
        "input_search_calls": sum(int(item["input_search_calls"]) for item in ordered),
        "input_sources": sum(int(item["input_source_count"]) for item in ordered),
        "input_citations": sum(int(item["input_citation_count"]) for item in ordered),
    }
    differences = [
        float(answer_is_correct(fusion)) - float(answer_is_correct(plain))
        for fusion, plain in zip(ordered, source_plain)
    ]
    rng = random.Random(20260813)
    boot = sorted(
        sum(differences[rng.randrange(len(differences))] for _ in differences)
        / len(differences)
        for _ in range(10_000)
    )
    metrics["paired_difference_vs_plain"] = sum(differences) / len(differences)
    metrics["paired_difference_ci95"] = [boot[249], boot[9749]]
    write_json(output_dir / "metrics.json", metrics)

    fields = (
        "case_id",
        "question_zh",
        "fusion_action",
        "fusion_answer",
        "unit",
        "gold_answer",
        "fusion_correct",
        "plain_correct",
        "filter_triggered",
        "input_search_calls",
        "input_source_count",
        "input_citation_count",
    )
    with (output_dir / "case_outcomes.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for fusion, plain in zip(ordered, source_plain):
            result = fusion["result"]
            writer.writerow(
                {
                    "case_id": fusion["case"]["id"],
                    "question_zh": fusion["case"]["question_zh"],
                    "fusion_action": result["action"],
                    "fusion_answer": result.get("answer_value"),
                    "unit": result.get("unit"),
                    "gold_answer": fusion["case"]["gold_answer"],
                    "fusion_correct": int(answer_is_correct(fusion)),
                    "plain_correct": int(answer_is_correct(plain)),
                    "filter_triggered": int(bool(result.get("filter_triggered"))),
                    "input_search_calls": fusion["input_search_calls"],
                    "input_source_count": fusion["input_source_count"],
                    "input_citation_count": fusion["input_citation_count"],
                }
            )
    return metrics


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    groups = load_source_groups(source_dir)
    if args.limit:
        groups = groups[: args.limit]
    planned = len(groups)
    if planned > args.max_api_calls:
        raise ValueError(
            f"Planned {planned} fusion calls exceeds --max-api-calls={args.max_api_calls}"
        )
    print(
        f"ATLAS-Fusion plan: {planned} questions x 1 structured arbitration = "
        f"{planned} HTTP calls; model={args.model}. Underlying real-search calls are "
        "reused, not repeated. Paid model usage may occur."
    )
    if not args.confirm_live:
        return []
    if not credential_is_available("anthropic"):
        raise RuntimeError("No Anthropic credential is available")

    config = RequestConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        max_output_tokens=args.max_output_tokens,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    client = AtlasFusionClient(config, base_url=args.base_url)
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "trace.jsonl"
    existing = load_existing(trace_path) if args.resume else []
    completed = {
        record["case"]["id"]
        for record in existing
        if record.get("status") == "ok"
    }
    source_manifest = json.loads(
        (source_dir / "study_manifest.json").read_text(encoding="utf-8")
    )
    protocol = {
        "protocol_version": "atlas-fusion-study-1.0",
        "source_protocol_sha256": source_manifest["protocol_sha256"],
        "source_strategies": list(SOURCE_STRATEGIES),
        "source_trajectories_per_case": len(SOURCE_STRATEGIES),
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_output_tokens": args.max_output_tokens,
        "no_additional_web_search": True,
        "evaluation_labels_excluded_from_prompt": True,
    }
    protocol_hash = canonical_sha256(protocol)
    manifest_path = output_dir / "study_manifest.json"
    previous_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if args.resume and manifest_path.exists()
        else None
    )
    if previous_manifest and previous_manifest.get("protocol_sha256") != protocol_hash:
        raise RuntimeError("Existing fusion output uses a different protocol")
    started_at = (
        previous_manifest.get("started_at")
        if previous_manifest
        else utc_now()
    )
    write_json(
        manifest_path,
        {
            "status": "running",
            "started_at": started_at,
            "protocol_sha256": protocol_hash,
            "protocol": protocol,
            "source_directory": source_dir.name,
            "planned_calls": planned,
        },
    )

    for index, group in enumerate(groups, start=1):
        case = group[0]["case"]
        if case["id"] in completed:
            continue
        print(f"[{index}/{planned}] {case['id']} - atlas_fusion")
        started = time.perf_counter()
        base = {
            "run_at": utc_now(),
            "status": "ok",
            "case": case,
            "study_protocol_sha256": protocol_hash,
        }
        try:
            result = client.run(group)
            raw = result.pop("raw_response")
            raw_path = output_dir / "raw_responses" / f"{case['id']}.json"
            write_json(raw_path, raw)
            result["raw_response_path"] = str(raw_path.relative_to(output_dir))
            result["latency_seconds"] = round(time.perf_counter() - started, 3)
            record = {**base, **result}
        except Exception as exc:
            record = {
                **base,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": redact_sensitive(str(exc)),
            }
        append_jsonl(trace_path, record)
        existing.append(record)
        if record["status"] != "ok" and not args.continue_on_error:
            break

    latest = {record["case"]["id"]: record for record in existing}
    selected = [latest[group[0]["case"]["id"]] for group in groups]
    complete = all(record.get("status") == "ok" for record in selected)
    metrics = write_evaluation(selected, groups, output_dir) if complete else None
    write_json(
        manifest_path,
        {
            "status": "completed" if complete else "incomplete",
            "started_at": started_at,
            "completed_at": utc_now(),
            "protocol_sha256": protocol_hash,
            "protocol": protocol,
            "source_directory": source_dir.name,
            "planned_calls": planned,
            "valid_runs": sum(record.get("status") == "ok" for record in selected),
            "metrics": metrics,
        },
    )
    if not complete:
        raise RuntimeError("ATLAS-Fusion stopped before complete valid output")
    print(f"ATLAS-Fusion completed: {len(selected)} validated arbitration records")
    return selected


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Fuse saved real-search trajectories")
    value.add_argument("--source-dir", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--base-url")
    value.add_argument("--model", default="claude-sonnet-5")
    value.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="medium")
    value.add_argument("--max-output-tokens", type=int, default=4800)
    value.add_argument("--timeout", type=int, default=300)
    value.add_argument("--max-retries", type=int, default=0)
    value.add_argument("--max-api-calls", type=int, default=20)
    value.add_argument("--limit", type=int)
    value.add_argument("--confirm-live", action="store_true")
    value.add_argument("--no-resume", dest="resume", action="store_false")
    value.add_argument("--continue-on-error", action="store_true")
    value.set_defaults(resume=True)
    return value


def main() -> None:
    run(parser().parse_args())


if __name__ == "__main__":
    main()
