from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_agent import STRATEGIES
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

    selected_keys = {
        (record["case"]["id"], record["strategy"]) for record in selected
    }
    excluded_successes = [
        {
            "case_id": record["case"]["id"],
            "strategy": record["strategy"],
            "run_at": record.get("run_at"),
            "reason": "outside first complete question prefix",
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

    manifest = {
        "status": "completed_subset",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_type": "real_web_search_agent_prefix_pilot",
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
            "status_at_manual_stop": source_study.get("status"),
            "protocol_sha256": source_study.get("protocol_sha256"),
            "trace_sha256": file_sha256(source_trace),
            "study_manifest_sha256": file_sha256(source_manifest),
            "successful_records_at_stop": len(successful_source),
            "transport_errors_at_stop": error_count,
            "invalid_records_at_stop": len(invalid_records),
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
        "source_protocol": source_study.get("protocol"),
        "artifacts": {
            "trace": "trace.jsonl",
            "metrics": "metrics.csv",
            "summary": "summary.md",
            "case_outcomes": "case_outcomes.csv",
            "exclusions": "exclusions.json",
            "raw_responses": "local/private; linked by path and SHA-256",
        },
        "metrics": summaries,
        "limitations": [
            "This is a 10-question prefix pilot, not the full 20-question study.",
            "The stop decision was made after data collection began; the fixed prefix "
            "rule prevents cherry-picking individual outcomes but is not preregistered.",
            "Publication dates remain model-declared until independently verified.",
            "Four transport failures occurred in the source run and are excluded from "
            "strategy metrics but disclosed in exclusions.json.",
        ],
    }
    write_json(output_dir / "study_manifest.json", manifest)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Finalize a complete prefix of a stopped live study without "
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
