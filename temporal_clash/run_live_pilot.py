from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_agent import (
    DEFAULT_MODEL,
    STRATEGIES,
    OpenAIResponsesWebSearch,
    RequestConfig,
)
from .live_evaluate import write_metrics


HERE = Path(__file__).resolve().parent
BASE_CASES_PATH = HERE / "base_cases.json"
DEFAULT_OUTPUT_DIR = Path("outputs") / "live_agent_pilot"


def load_cases(limit: int) -> list[dict[str, Any]]:
    cases = json.loads(BASE_CASES_PATH.read_text(encoding="utf-8"))
    if not 1 <= limit <= len(cases):
        raise ValueError(f"--limit must be between 1 and {len(cases)}")
    return cases[:limit]


def load_existing(trace_path: Path) -> list[dict[str, Any]]:
    if not trace_path.exists():
        return []
    records = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def plan(
    limit: int,
    strategies: tuple[str, ...],
    model: str,
    max_calls: int,
    max_retries: int,
) -> str:
    if limit < 1:
        raise ValueError("--limit must be positive")
    if max_retries < 0:
        raise ValueError("--max-retries cannot be negative")
    if max_calls < 1:
        raise ValueError("--max-api-calls must be positive")
    runs = limit * len(strategies)
    requested = runs * (max_retries + 1)
    if requested > max_calls:
        raise ValueError(
            f"Planned up to {requested} HTTP attempts exceeds "
            f"--max-api-calls={max_calls}"
        )
    return (
        f"Pilot plan: {limit} questions x {len(strategies)} strategies = {runs} "
        f"runs, up to {requested} HTTP attempts including retries; model={model}. "
        "Each run may invoke paid web search."
    )


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    strategies = tuple(args.strategies)
    unknown = sorted(set(strategies) - set(STRATEGIES))
    if unknown:
        raise ValueError(f"Unknown strategies: {', '.join(unknown)}")
    print(
        plan(
            args.limit,
            strategies,
            args.model,
            args.max_api_calls,
            args.max_retries,
        )
    )

    if not args.confirm_live:
        print("Plan only: no API request was sent.")
        print(
            "To run live, set OPENAI_API_KEY in the current shell and add "
            "--confirm-live."
        )
        return []
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Set it in the current shell; do not put it "
            "in config.yaml or commit it."
        )

    output_dir = Path(args.output_dir)
    trace_path = output_dir / "trace.jsonl"
    records = load_existing(trace_path) if args.resume else []
    completed = {
        (record["case"]["id"], record["strategy"])
        for record in records
        if record.get("status") == "ok"
    }
    client = OpenAIResponsesWebSearch(
        RequestConfig(
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            search_context_size=args.search_context_size,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
        )
    )
    cases = load_cases(args.limit)
    total = len(cases) * len(strategies)
    current = len(completed)

    for case in cases:
        case_view = {
            key: case[key]
            for key in (
                "id",
                "question_zh",
                "cutoff_date",
                "target_period",
                "required_version",
                "gold_answer",
                "canonical_unit",
            )
        }
        for strategy in strategies:
            key = (case["id"], strategy)
            if key in completed:
                continue
            current += 1
            print(f"[{current}/{total}] {case['id']} - {strategy}")
            base_record = {
                "run_at": datetime.now(timezone.utc).isoformat(),
                "case": case_view,
                "strategy": strategy,
            }
            try:
                result = client.run(case, strategy)
                record = {**base_record, "status": "ok", **result}
            except Exception as exc:
                record = {
                    **base_record,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            append_jsonl(trace_path, record)
            records.append(record)

    write_metrics(records, output_dir)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "limit": args.limit,
        "strategies": list(strategies),
        "planned_max_api_calls": (
            args.limit * len(strategies) * (args.max_retries + 1)
        ),
        "trace": "trace.jsonl",
        "metrics": "metrics.csv",
        "summary": "summary.md",
        "metadata_warning": (
            "Source publication dates are agent-declared until independently fetched."
        ),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Trace:   {trace_path}")
    print(f"Metrics: {output_dir / 'metrics.csv'}")
    print(f"Summary: {output_dir / 'summary.md'}")
    return records


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run a guarded real Web Search Agent pilot."
    )
    result.add_argument("--limit", type=int, default=20)
    result.add_argument(
        "--strategies",
        nargs="+",
        default=list(STRATEGIES),
        choices=list(STRATEGIES),
    )
    result.add_argument("--model", default=DEFAULT_MODEL)
    result.add_argument("--reasoning-effort", default="low")
    result.add_argument(
        "--search-context-size", choices=["low", "medium", "high"], default="low"
    )
    result.add_argument("--max-output-tokens", type=int, default=1200)
    result.add_argument("--timeout", type=int, default=120)
    result.add_argument("--max-retries", type=int, default=0)
    result.add_argument("--max-api-calls", type=int, default=80)
    result.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    result.add_argument(
        "--confirm-live",
        action="store_true",
        help="Actually send paid API/web-search requests.",
    )
    result.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Do not reuse successful records already present in trace.jsonl.",
    )
    result.set_defaults(resume=True)
    return result


def main() -> None:
    args = parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
