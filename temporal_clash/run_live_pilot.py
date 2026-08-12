from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_agent import (
    ANTHROPIC_PROVIDER,
    PROVIDERS,
    STRATEGIES,
    RequestConfig,
    build_prompt,
    create_client,
    credential_help,
    credential_is_available,
    default_model_for_provider,
    http_calls_per_run,
    prompt_sha256,
    redact_sensitive,
    request_config_view,
)
from .live_atlas import ATLAS_METHOD_VERSION
from .live_evaluate import write_metrics
from .live_study import write_aggregate
from .live_validate import (
    canonical_sha256,
    validate_live_record,
    validate_protocol_consistency,
)


HERE = Path(__file__).resolve().parent
BASE_CASES_PATH = HERE / "base_cases.json"
DEFAULT_OUTPUT_DIR = Path("outputs") / "live_agent_pilot"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def git_state() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return {"commit": commit, "dirty": bool(status.strip())}
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def implementation_sha256() -> str:
    files = (
        HERE / "live_agent.py",
        HERE / "live_atlas.py",
        HERE / "live_evaluate.py",
        HERE / "live_study.py",
        HERE / "live_validate.py",
        HERE / "run_live_pilot.py",
    )
    return canonical_sha256(
        [
            {
                "path": path.name,
                "sha256": canonical_sha256(path.read_text(encoding="utf-8")),
            }
            for path in files
        ]
    )


def plan(
    limit: int,
    strategies: tuple[str, ...],
    model: str,
    max_calls: int,
    max_retries: int,
    provider: str = "openai",
    repeats: int = 1,
    structured_output: bool = True,
    max_tool_calls: int = 3,
) -> str:
    if limit < 1:
        raise ValueError("--limit must be positive")
    if max_retries < 0:
        raise ValueError("--max-retries cannot be negative")
    if max_calls < 1:
        raise ValueError("--max-api-calls must be positive")
    if repeats < 1:
        raise ValueError("--repeats must be positive")
    if max_tool_calls < 1:
        raise ValueError("--max-tool-calls must be positive")

    runs = limit * len(strategies) * repeats
    stages = http_calls_per_run(provider, structured_output)
    requested = runs * stages * (max_retries + 1)
    searches = runs * max_tool_calls
    if requested > max_calls:
        raise ValueError(
            f"Planned up to {requested} HTTP attempts exceeds "
            f"--max-api-calls={max_calls}"
        )
    return (
        f"Pilot plan: {limit} questions x {len(strategies)} strategies x "
        f"{repeats} repeats = {runs} runs; provider={provider}; model={model}; "
        f"{stages} HTTP stage(s) per run; up to {requested} HTTP attempts and "
        f"{searches} web-search tool calls. Paid model/search usage may occur."
    )


def case_view(case: dict[str, Any]) -> dict[str, Any]:
    return {
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


def latest_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest = {
        (record["case"]["id"], record["strategy"]): record
        for record in records
    }
    return list(latest.values())


def scheduled_strategies(
    strategies: tuple[str, ...],
    case_index: int,
    repeat_index: int,
) -> tuple[str, ...]:
    """Cyclically counterbalance treatment order across questions/repeats."""

    if len(strategies) < 2:
        return strategies
    offset = (case_index + repeat_index - 2) % len(strategies)
    return strategies[offset:] + strategies[:offset]


def build_protocol(
    *,
    provider: str,
    client_base_url: str,
    config: RequestConfig,
    cases: list[dict[str, Any]],
    strategies: tuple[str, ...],
    repeats: int,
) -> dict[str, Any]:
    prompt_hashes = [
        {
            "case_id": case["id"],
            "strategy": strategy,
            "prompt_sha256": prompt_sha256(build_prompt(case, strategy)),
        }
        for case in cases
        for strategy in strategies
    ]
    context_control = (
        config.search_context_size
        if provider != ANTHROPIC_PROVIDER
        else "provider-managed (Anthropic has no search_context_size parameter)"
    )
    return {
        "protocol_version": "live-study-3.0",
        "atlas_method_version": ATLAS_METHOD_VERSION,
        "provider": provider,
        "base_url": client_base_url,
        "requested_model": config.model,
        "request_config": request_config_view(config),
        "fixed_controls": {
            "reasoning_effort": config.reasoning_effort,
            "search_context": context_control,
            "max_web_search_calls_per_run": config.max_tool_calls,
            "forced_web_search": config.force_search,
            "structured_output": config.structured_output,
            "raw_response_capture": config.save_raw_response,
            "same_question_batch": True,
            "schedule": "cyclic counterbalancing by question and repeat",
        },
        "treatment_variable": "strategy",
        "strategies": list(strategies),
        "repeats": repeats,
        "case_ids": [case["id"] for case in cases],
        "case_batch_sha256": canonical_sha256(
            [case_view(case) for case in cases]
        ),
        "prompt_set_sha256": canonical_sha256(prompt_hashes),
        "prompt_hashes": prompt_hashes,
        "implementation_sha256": implementation_sha256(),
        "secret_storage": "environment only; credentials are never serialized",
    }


def _repeat_dir(output_dir: Path, repeat_index: int, repeats: int) -> Path:
    if repeats == 1:
        return output_dir
    return output_dir / f"repeat_{repeat_index:02d}"


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    provider = args.provider
    model = args.model or default_model_for_provider(provider)
    strategies = tuple(args.strategies)
    unknown = sorted(set(strategies) - set(STRATEGIES))
    if unknown:
        raise ValueError(f"Unknown strategies: {', '.join(unknown)}")

    config = RequestConfig(
        model=model,
        reasoning_effort=args.reasoning_effort,
        search_context_size=args.search_context_size,
        max_tool_calls=args.max_tool_calls,
        max_output_tokens=args.max_output_tokens,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
        force_search=True,
        structured_output=True,
        save_raw_response=True,
    )
    print(
        plan(
            args.limit,
            strategies,
            model,
            args.max_api_calls,
            args.max_retries,
            provider,
            args.repeats,
            config.structured_output,
            config.max_tool_calls,
        )
    )

    client = create_client(
        provider,
        config,
        base_url=str(args.base_url) if args.base_url else None,
    )
    if not args.confirm_live:
        print("Plan only: no API request was sent.")
        print(f"To run live, {credential_help(provider)} Then add --confirm-live.")
        return []
    if not credential_is_available(provider):
        raise RuntimeError(
            f"No {provider} credential is available. {credential_help(provider)} "
            "Do not put credentials in a tracked settings file."
        )

    cases = load_cases(args.limit)
    output_dir = Path(args.output_dir)
    protocol = build_protocol(
        provider=provider,
        client_base_url=client.base_url,
        config=config,
        cases=cases,
        strategies=strategies,
        repeats=args.repeats,
    )
    protocol_hash = canonical_sha256(protocol)
    study_manifest_path = output_dir / "study_manifest.json"
    existing_manifest = (
        json.loads(study_manifest_path.read_text(encoding="utf-8"))
        if args.resume and study_manifest_path.exists()
        else None
    )
    if existing_manifest and existing_manifest.get("protocol_sha256") != protocol_hash:
        raise RuntimeError(
            "Existing output uses a different research protocol. Choose a new "
            "--output-dir or use --no-resume."
        )

    study_started_at = (
        existing_manifest.get("started_at")
        if existing_manifest
        else utc_now()
    )
    study_manifest: dict[str, Any] = {
        "status": "running",
        "started_at": study_started_at,
        "completed_at": None,
        "protocol_sha256": protocol_hash,
        "protocol": protocol,
        "environment": {"git_at_start": git_state()},
        "planned_http_attempts": (
            args.limit
            * len(strategies)
            * args.repeats
            * http_calls_per_run(provider, True)
            * (args.max_retries + 1)
        ),
        "planned_max_web_search_calls": (
            args.limit * len(strategies) * args.repeats * args.max_tool_calls
        ),
        "repeat_manifests": [],
        "artifacts": {
            "repeat_trace": "trace.jsonl or repeat_NN/trace.jsonl",
            "repeat_metrics": "metrics.csv",
            "aggregate_metrics": (
                "aggregate_metrics.csv" if args.repeats > 1 else None
            ),
        },
    }
    write_json(study_manifest_path, study_manifest)

    all_records: list[dict[str, Any]] = []
    repeat_summaries: list[dict[str, dict[str, float]]] = []
    repeat_manifests: list[dict[str, Any]] = []
    abort_reason: str | None = None

    for repeat_index in range(1, args.repeats + 1):
        repeat_dir = _repeat_dir(output_dir, repeat_index, args.repeats)
        trace_path = repeat_dir / "trace.jsonl"
        records = load_existing(trace_path) if args.resume else []
        latest = latest_records(records)
        completed = {
            (record["case"]["id"], record["strategy"])
            for record in latest
            if not validate_live_record(record, repeat_dir)
        }
        expected = len(cases) * len(strategies)
        current = len(completed)
        repeat_started_at = utc_now()

        for case_index, case in enumerate(cases, start=1):
            order = scheduled_strategies(
                strategies, case_index, repeat_index
            )
            for strategy in order:
                key = (case["id"], strategy)
                if key in completed:
                    continue
                current += 1
                print(
                    f"[repeat {repeat_index}/{args.repeats}; "
                    f"{current}/{expected}] {case['id']} - {strategy}"
                )
                base_record = {
                    "study_protocol_sha256": protocol_hash,
                    "repeat_index": repeat_index,
                    "run_at": utc_now(),
                    "case": case_view(case),
                    "strategy": strategy,
                }
                try:
                    result = client.run(case, strategy)
                    raw_response = result.pop("raw_response", None)
                    if raw_response is not None:
                        raw_name = (
                            f"r{repeat_index:02d}_{case['id']}_{strategy}_"
                            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
                            ".json"
                        )
                        raw_path = repeat_dir / "raw_responses" / raw_name
                        write_json(raw_path, raw_response)
                        result["raw_response_path"] = str(
                            raw_path.relative_to(repeat_dir)
                        )
                        result["raw_response_sha256"] = canonical_sha256(
                            raw_response
                        )
                    record = {**base_record, "status": "ok", **result}
                    trace_errors = validate_live_record(record, repeat_dir)
                    record["trace_validation_errors"] = trace_errors
                    if trace_errors:
                        record["status"] = "invalid"
                        abort_reason = (
                            f"{case['id']}/{strategy} failed strict trace "
                            f"validation: {', '.join(trace_errors)}"
                        )
                except Exception as exc:
                    error = redact_sensitive(str(exc))
                    record = {
                        **base_record,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": error,
                        "trace_validation_errors": ["api_run_failed"],
                    }
                    abort_reason = f"{case['id']}/{strategy} failed: {error}"
                append_jsonl(trace_path, record)
                records.append(record)
                if abort_reason and not args.continue_on_error:
                    break
            if abort_reason and not args.continue_on_error:
                break

        latest = latest_records(records)
        summary = write_metrics(latest, repeat_dir)
        repeat_summaries.append(summary)
        all_records.extend(latest)
        consistency_errors = validate_protocol_consistency(latest, repeat_dir)
        successful = sum(
            not validate_live_record(record, repeat_dir) for record in latest
        )
        repeat_status = (
            "completed"
            if successful == expected and not consistency_errors
            else "incomplete"
        )
        repeat_manifest = {
            "repeat_index": repeat_index,
            "status": repeat_status,
            "started_at": repeat_started_at,
            "completed_at": utc_now(),
            "expected_runs": expected,
            "valid_runs": successful,
            "protocol_sha256": protocol_hash,
            "protocol_validation_errors": consistency_errors,
            "trace": "trace.jsonl",
            "metrics": "metrics.csv",
            "summary": "summary.md",
            "metadata_warning": (
                "Publication dates in structured evidence remain model-declared "
                "until independently fetched and verified."
            ),
        }
        write_json(repeat_dir / "manifest.json", repeat_manifest)
        repeat_manifests.append(
            {
                **repeat_manifest,
                "directory": str(repeat_dir.relative_to(output_dir) or "."),
            }
        )
        if repeat_status != "completed":
            abort_reason = abort_reason or (
                f"Repeat {repeat_index} is incomplete: "
                + ", ".join(consistency_errors or ["missing valid runs"])
            )
        if abort_reason and not args.continue_on_error:
            break

    if len(repeat_summaries) > 1:
        write_aggregate(repeat_summaries, output_dir)

    complete = (
        len(repeat_manifests) == args.repeats
        and all(item["status"] == "completed" for item in repeat_manifests)
    )
    study_manifest.update(
        {
            "status": "completed" if complete else "incomplete",
            "completed_at": utc_now(),
            "repeat_manifests": repeat_manifests,
            "failure": abort_reason,
        }
    )
    write_json(study_manifest_path, study_manifest)

    print(f"Study manifest: {study_manifest_path}")
    if complete:
        print(f"Study completed with {len(all_records)} validated records.")
    else:
        raise RuntimeError(
            "Live study stopped before research-valid completion. "
            f"See the manifest and trace. Reason: {abort_reason}"
        )
    return all_records


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Run a guarded real Web Search Agent pilot with forced search, "
            "complete sources, bounded tool use, structured output and full traces."
        )
    )
    result.add_argument("--provider", choices=PROVIDERS, default="openai")
    result.add_argument("--base-url", type=str)
    result.add_argument("--limit", type=int, default=20)
    result.add_argument(
        "--strategies",
        nargs="+",
        default=list(STRATEGIES),
        choices=list(STRATEGIES),
    )
    result.add_argument(
        "--model",
        help="Provider model ID. Defaults to the provider-specific model.",
    )
    result.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default="medium",
    )
    result.add_argument(
        "--search-context-size",
        choices=["low", "medium", "high"],
        default="medium",
        help=(
            "OpenAI web-search context size. Anthropic has no equivalent parameter; "
            "the fixed provider-managed setting is recorded in the manifest."
        ),
    )
    result.add_argument("--max-tool-calls", type=int, default=3)
    result.add_argument("--max-output-tokens", type=int, default=4800)
    result.add_argument("--timeout", type=int, default=120)
    result.add_argument("--max-retries", type=int, default=0)
    result.add_argument("--repeats", type=int, default=1)
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
        help="Start a new trace; use a new output directory to preserve prior work.",
    )
    result.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Continue after an API/trace validation failure. The study still remains "
            "incomplete and this may spend more API/search budget."
        ),
    )
    result.set_defaults(resume=True)
    return result


def main() -> None:
    args = parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
