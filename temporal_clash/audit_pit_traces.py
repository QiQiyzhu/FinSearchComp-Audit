from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .pit_audit import aggregate_temporal_metrics, audit_record_temporality
from .run_xbrl_study import exact_two_decimal_correct


def audit_trace_file(path: Path, *, limit_cases: int | None = None) -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = list(dict.fromkeys(record["case"]["id"] for record in records))
    if limit_cases:
        allowed = set(case_ids[:limit_cases])
        records = [record for record in records if record["case"]["id"] in allowed]
    result: dict[str, Any] = {
        "audit_version": "pit-audit-1.0",
        "source_trace": str(path),
        "case_count": len({record["case"]["id"] for record in records}),
        "strategies": {},
    }
    for strategy in dict.fromkeys(record["strategy"] for record in records):
        group = [record for record in records if record["strategy"] == strategy]
        for record in group:
            record["point_in_time_audit"] = audit_record_temporality(record)
        temporal = aggregate_temporal_metrics(group)
        reliable = sum(
            exact_two_decimal_correct(record)
            and record["point_in_time_audit"]["final_temporally_compliant"]
            and record["point_in_time_audit"]["final_provenance_complete"]
            for record in group
        )
        result["strategies"][strategy] = {
            "runs": len(group),
            "decision_accuracy": sum(exact_two_decimal_correct(record) for record in group)
            / len(group),
            "answer_coverage": sum(
                (record.get("result") or {}).get("action") == "answer"
                for record in group
            )
            / len(group),
            **temporal,
            "joint_reliable_answer_rate": reliable / len(group),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Independently audit source and final-evidence dates in a trace"
    )
    parser.add_argument("trace", type=Path)
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_trace_file(args.trace, limit_cases=args.limit_cases)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
