from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atlas_compute import (
    COMPUTE_LABEL,
    COMPUTE_METHOD_VERSION,
    COMPUTE_STRATEGY,
    AtlasComputeClient,
    build_compute_research_prompt,
)
from .live_agent import (
    AnthropicMessagesWebSearch,
    RequestConfig,
    credential_is_available,
    redact_sensitive,
    request_config_view,
)
from .live_evaluate import answer_is_correct
from .live_validate import canonical_sha256
from .run_live_pilot import append_jsonl, write_json


HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "compute_micro_cases.json"
STRATEGIES = ("plain_agent", COMPUTE_STRATEGY)
LABELS = {"plain_agent": "普通搜索 Agent", COMPUTE_STRATEGY: COMPUTE_LABEL}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_state() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def case_view(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case[key]
        for key in (
            "id",
            "question_zh",
            "cutoff_date",
            "target_period",
            "required_version",
            "canonical_unit",
            "gold_answer",
            "reference_sources",
            "gold_calculation",
        )
    }


def load_cases(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Micro benchmark must contain a non-empty case list")
    return cases[:limit] if limit else cases


def prompt_is_label_free(record: dict[str, Any]) -> bool:
    payloads = record.get("request_payloads") or {}
    serialized = json.dumps(
        {
            "prompt": record.get("prompt"),
            "research_payload": payloads.get("research"),
        },
        ensure_ascii=False,
    )
    forbidden_fields = ("gold_answer", "gold_calculation", "reference_sources")
    return not any(field in serialized for field in forbidden_fields)


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get("status") != "ok":
        return ["status_not_ok"]
    if not record.get("response_id") or not record.get("normalization_response_id"):
        errors.append("missing_response_ids")
    if len(record.get("response_ids") or []) != 2:
        errors.append("expected_two_http_stages")
    if not record.get("search_actions"):
        errors.append("missing_web_search")
    if not record.get("search_sources"):
        errors.append("missing_complete_sources")
    if not record.get("api_citations"):
        errors.append("missing_native_citations")
    if record.get("model") != record.get("requested_model"):
        errors.append("resolved_model_mismatch")
    if record.get("normalization_model") != record.get("requested_model"):
        errors.append("normalization_model_mismatch")
    if not prompt_is_label_free(record):
        errors.append("evaluation_label_leakage")
    result = record.get("result") or {}
    if result.get("action") not in {"answer", "abstain"}:
        errors.append("invalid_action")
    if record.get("strategy") == COMPUTE_STRATEGY:
        if not result.get("calculation_trace"):
            errors.append("missing_calculation_trace")
        if result.get("action") == "answer" and not result.get("accepted_evidence"):
            errors.append("answer_without_accepted_operands")
    return errors


def _latest(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = {
        (record["case"]["id"], record["strategy"]): record for record in records
    }
    return list(latest.values())


def _metric(records: list[dict[str, Any]], predicate) -> float:
    return sum(bool(predicate(record)) for record in records) / len(records)


def write_evaluation(
    records: list[dict[str, Any]], output_dir: Path
) -> dict[str, Any]:
    by_strategy = {
        strategy: [record for record in records if record["strategy"] == strategy]
        for strategy in STRATEGIES
    }
    metrics: dict[str, Any] = {}
    for strategy, items in by_strategy.items():
        metrics[strategy] = {
            "runs": len(items),
            "decision_accuracy": _metric(items, answer_is_correct),
            "answer_coverage": _metric(
                items, lambda record: record["result"]["action"] == "answer"
            ),
            "web_search_calls": sum(len(item["search_actions"]) for item in items),
            "http_stages": sum(int(item["http_attempts"]) for item in items),
            "search_sources": sum(len(item["search_sources"]) for item in items),
            "native_citations": sum(len(item["api_citations"]) for item in items),
            "input_tokens": sum(
                int((item.get("usage") or {}).get("input_tokens") or 0)
                for item in items
            ),
            "output_tokens": sum(
                int((item.get("usage") or {}).get("output_tokens") or 0)
                for item in items
            ),
        }

    by_case = {
        case_id: {
            record["strategy"]: record
            for record in records
            if record["case"]["id"] == case_id
        }
        for case_id in dict.fromkeys(record["case"]["id"] for record in records)
    }
    differences = [
        float(answer_is_correct(group[COMPUTE_STRATEGY]))
        - float(answer_is_correct(group["plain_agent"]))
        for group in by_case.values()
    ]
    rng = random.Random(20260813)
    boot = sorted(
        sum(differences[rng.randrange(len(differences))] for _ in differences)
        / len(differences)
        for _ in range(10_000)
    )
    comparison = {
        "paired_accuracy_difference": sum(differences) / len(differences),
        "paired_bootstrap_ci95": [boot[249], boot[9749]],
        "wins": sum(value > 0 for value in differences),
        "ties": sum(value == 0 for value in differences),
        "losses": sum(value < 0 for value in differences),
        "n_questions": len(differences),
    }
    payload = {"strategies": metrics, "comparison": comparison}
    write_json(output_dir / "metrics.json", payload)

    fields = (
        "case_id",
        "question_zh",
        "gold_answer",
        "canonical_unit",
        "plain_action",
        "plain_answer",
        "plain_correct",
        "compute_action",
        "compute_answer",
        "compute_correct",
        "compute_operation",
        "compute_operand_count",
    )
    with (output_dir / "case_outcomes.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case_id, group in by_case.items():
            plain = group["plain_agent"]
            compute = group[COMPUTE_STRATEGY]
            trace = compute["result"].get("calculation_trace") or {}
            writer.writerow(
                {
                    "case_id": case_id,
                    "question_zh": plain["case"]["question_zh"],
                    "gold_answer": plain["case"]["gold_answer"],
                    "canonical_unit": plain["case"]["canonical_unit"],
                    "plain_action": plain["result"]["action"],
                    "plain_answer": plain["result"].get("answer_value"),
                    "plain_correct": int(answer_is_correct(plain)),
                    "compute_action": compute["result"]["action"],
                    "compute_answer": compute["result"].get("answer_value"),
                    "compute_correct": int(answer_is_correct(compute)),
                    "compute_operation": trace.get("operation"),
                    "compute_operand_count": len(trace.get("operand_values") or []),
                }
            )
    return payload


def write_readme(output_dir: Path, manifest: dict[str, Any]) -> None:
    metrics = manifest["metrics"]
    plain = metrics["strategies"]["plain_agent"]
    compute = metrics["strategies"][COMPUTE_STRATEGY]
    comparison = metrics["comparison"]
    text = f"""# ATLAS-Compute：6题真实计算型微型实验

这是一个**定向微型挑战集**，不是从原20题中按结果挑选的子集。6道新题在真实运行前冻结，
专门检验普通搜索 Agent 容易混淆的“跨表取数 + 精确公式 + 单位/期间对齐”。

## 结果

| 方法 | 最终准确率 | 回答覆盖率 | Web Search | HTTP阶段 |
|---|---:|---:|---:|---:|
| 普通搜索 Agent | {plain['decision_accuracy']:.1%} | {plain['answer_coverage']:.1%} | {plain['web_search_calls']} | {plain['http_stages']} |
| **ATLAS-Compute** | **{compute['decision_accuracy']:.1%}** | **{compute['answer_coverage']:.1%}** | {compute['web_search_calls']} | {compute['http_stages']} |

配对差值为 **{comparison['paired_accuracy_difference']:+.1%}**；逐题为
{comparison['wins']}胜 / {comparison['ties']}平 / {comparison['losses']}负。

## 方法

两种方法使用同一个 `{manifest['protocol']['model']}`、相同推理强度、每题最多
{manifest['protocol']['max_web_searches_per_run']}次搜索，而且均为“检索 + 结构化”两个HTTP阶段。
普通 Agent 直接生成答案；ATLAS-Compute先输出带原生引用的原始操作数，再生成受约束的计算计划，
最后由本地 `Decimal` 按固定公式执行并保留两位小数。运行时提示词不包含gold、参考URL或参考公式。

## 研究边界

- 这是6题、单模型、单次运行的 targeted micro-benchmark，不能代表开放域总体性能；
- 问题类型在观察旧pilot的“数值推理失败”后确定，因此属于方法开发证据，不是独立最终测试；
- 题目与方法在真实测试前固定，提交为 `{manifest['preregistered_commit']}`；
- 95%配对bootstrap区间为 {comparison['paired_bootstrap_ci95'][0]:+.1%} 至 {comparison['paired_bootstrap_ci95'][1]:+.1%}，小样本不确定性很大；
- 完整逐题结果、提示词哈希、响应ID、来源、原生引用和计算trace均随仓库保存。

## 文件

- `case_outcomes.csv`：6道题的普通Agent/ATLAS-Compute配对结果；
- `metrics.json`：聚合指标和配对bootstrap；
- `trace.jsonl`：12条脱敏真实运行trace；
- `study_manifest.json`：冻结协议、题集哈希、预算和环境；
- `../../compute_micro_cases.json`：运行前冻结的题目、gold与一手来源。
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_cases(args.case_file, args.limit)
    runs = len(cases) * len(STRATEGIES)
    planned_attempts = runs * 2 * (args.max_retries + 1)
    if planned_attempts > args.max_api_calls:
        raise ValueError(
            f"Planned up to {planned_attempts} HTTP attempts exceeds "
            f"--max-api-calls={args.max_api_calls}"
        )
    print(
        f"ATLAS-Compute micro plan: {len(cases)} questions x 2 strategies = "
        f"{runs} runs, {runs * 2} successful HTTP stages, at most "
        f"{planned_attempts} attempts and {runs * args.max_tool_calls} web searches; "
        f"model={args.model}. Paid usage may occur."
    )
    if not args.confirm_live:
        print("Plan only: no request was sent. Add --confirm-live to execute.")
        return []
    if not credential_is_available("anthropic"):
        raise RuntimeError("No Anthropic credential is available")

    config = RequestConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        max_tool_calls=args.max_tool_calls,
        max_output_tokens=args.max_output_tokens,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    plain_client = AnthropicMessagesWebSearch(config, base_url=args.base_url)
    compute_client = AtlasComputeClient(config, base_url=args.base_url)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "trace.jsonl"

    current_git = git_state()
    if current_git["dirty"]:
        raise RuntimeError(
            "Formal live run requires a clean preregistered commit; commit code and cases first"
        )
    protocol = {
        "protocol_version": "atlas-compute-micro-1.0.1",
        "method_version": COMPUTE_METHOD_VERSION,
        "study_type": "targeted_numeric_reasoning_micro_benchmark",
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_output_tokens": args.max_output_tokens,
        "max_web_searches_per_run": args.max_tool_calls,
        "http_stages_per_run": 2,
        "same_model_and_budget_per_strategy": True,
        "schedule": "cyclic counterbalancing by question",
        "case_ids": [case["id"] for case in cases],
        "case_batch_sha256": canonical_sha256(cases),
        "gold_and_reference_fields_excluded_from_prompts": True,
        "calculation_execution": "Python Decimal; ROUND_HALF_UP; 2 decimals",
        "selection_disclosure": (
            "Six new calculation-heavy cases designed after diagnosing the prior "
            "pilot; not a random sample and not selected from old outcomes."
        ),
        "request_config": request_config_view(config),
    }
    protocol_hash = canonical_sha256(protocol)
    manifest_path = output_dir / "study_manifest.json"
    started_at = utc_now()
    write_json(
        manifest_path,
        {
            "status": "running",
            "started_at": started_at,
            "protocol_sha256": protocol_hash,
            "protocol": protocol,
            "preregistered_commit": current_git["commit"],
            "planned_runs": runs,
            "planned_http_attempts": planned_attempts,
        },
    )

    records: list[dict[str, Any]] = []
    abort_reason: str | None = None
    for case_index, case in enumerate(cases, start=1):
        order = STRATEGIES if case_index % 2 else tuple(reversed(STRATEGIES))
        for strategy in order:
            print(f"[{len(records) + 1}/{runs}] {case['id']} - {strategy}")
            started = time.perf_counter()
            base = {
                "status": "ok",
                "run_at": utc_now(),
                "study_protocol_sha256": protocol_hash,
                "case": case_view(case),
                "strategy": strategy,
            }
            try:
                result = (
                    plain_client.run(case, "plain_agent")
                    if strategy == "plain_agent"
                    else compute_client.run(case)
                )
                raw = result.pop("raw_response")
                raw_path = output_dir / "raw_responses" / f"{case['id']}_{strategy}.json"
                write_json(raw_path, raw)
                result["raw_response_path"] = str(raw_path.relative_to(output_dir))
                result["latency_seconds"] = round(time.perf_counter() - started, 3)
                record = {**base, **result}
                errors = validate_record(record)
                record["trace_validation_errors"] = errors
                if errors:
                    record["status"] = "invalid"
                    abort_reason = f"{case['id']}/{strategy}: {', '.join(errors)}"
            except Exception as exc:
                error = redact_sensitive(str(exc))
                record = {
                    **base,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": error,
                    "trace_validation_errors": ["api_run_failed"],
                }
                abort_reason = f"{case['id']}/{strategy}: {error}"
            append_jsonl(trace_path, record)
            records.append(record)
            if abort_reason:
                break
        if abort_reason:
            break

    latest = _latest(records)
    complete = len(latest) == runs and all(not validate_record(item) for item in latest)
    metrics = write_evaluation(latest, output_dir) if complete else None
    manifest = {
        "status": "completed" if complete else "incomplete",
        "started_at": started_at,
        "completed_at": utc_now(),
        "protocol_sha256": protocol_hash,
        "protocol": protocol,
        "preregistered_commit": current_git["commit"],
        "planned_runs": runs,
        "valid_runs": sum(not validate_record(item) for item in latest),
        "planned_http_attempts": planned_attempts,
        "successful_http_stages": sum(
            int(item.get("http_attempts") or 0)
            for item in latest
            if item.get("status") == "ok"
        ),
        "failure": abort_reason,
        "metrics": metrics,
    }
    write_json(manifest_path, manifest)
    if not complete:
        raise RuntimeError(f"Micro study incomplete: {abort_reason}")
    write_readme(output_dir, manifest)
    print(f"ATLAS-Compute micro completed with {len(latest)} validated records")
    return latest


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run the ATLAS-Compute micro study")
    value.add_argument("--case-file", type=Path, default=DEFAULT_CASES)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--base-url")
    value.add_argument("--model", default="claude-sonnet-5")
    value.add_argument(
        "--reasoning-effort", choices=("low", "medium", "high"), default="medium"
    )
    value.add_argument("--max-tool-calls", type=int, default=3)
    value.add_argument("--max-output-tokens", type=int, default=4800)
    value.add_argument("--timeout", type=int, default=300)
    value.add_argument("--max-retries", type=int, default=0)
    value.add_argument("--max-api-calls", type=int, default=24)
    value.add_argument("--limit", type=int)
    value.add_argument("--confirm-live", action="store_true")
    return value


def main() -> None:
    run(parser().parse_args())


if __name__ == "__main__":
    main()
