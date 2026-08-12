from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .atlas_xbrl import (
    XBRL_LABEL,
    XBRL_METHOD_VERSION,
    XBRL_STRATEGY,
    AtlasXbrlClient,
)
from .audit_xbrl_cases import audit_cases
from .live_agent import (
    AnthropicMessagesWebSearch,
    RequestConfig,
    credential_is_available,
    redact_sensitive,
    request_config_view,
)
from .live_validate import canonical_sha256
from .pit_audit import aggregate_temporal_metrics, audit_record_temporality
from .run_live_pilot import append_jsonl, load_existing, write_json


HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "xbrl_20q_cases.json"
STRATEGIES = ("plain_agent", XBRL_STRATEGY)
LABELS = {"plain_agent": "普通搜索 Agent", XBRL_STRATEGY: XBRL_LABEL}


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
            "gold_calculation",
            "reference_program",
        )
    }


def load_cases(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("XBRL study requires a non-empty case list")
    return cases[:limit] if limit else cases


def _decimal_number(value: Any) -> Decimal | None:
    if value is None:
        return None
    match = re.search(
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)",
        str(value).replace(",", "").replace("%", ""),
    )
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def exact_two_decimal_correct(record: dict[str, Any]) -> bool:
    result = record.get("result") or {}
    case = record["case"]
    if result.get("action") != "answer" or result.get("unit") != case["canonical_unit"]:
        return False
    observed = _decimal_number(result.get("answer_value"))
    expected = _decimal_number(case.get("gold_answer"))
    return observed is not None and expected is not None and observed == expected


def prompt_is_label_free(record: dict[str, Any]) -> bool:
    if record["strategy"] == "plain_agent":
        payloads = record.get("request_payloads") or {}
        inspected = {
            "prompt": record.get("research_prompt"),
            "research_payload": payloads.get("research"),
        }
    else:
        inspected = {
            "prompt": record.get("prompt"),
            "request_payload": record.get("request_payload"),
        }
    serialized = json.dumps(inspected, ensure_ascii=False)
    return not any(
        field in serialized
        for field in ("gold_answer", "gold_calculation", "reference_program")
    )


def validate_record(record: dict[str, Any]) -> list[str]:
    if record.get("status") != "ok":
        return ["status_not_ok"]
    errors: list[str] = []
    expected_responses = 2 if record["strategy"] == "plain_agent" else 1
    if len(record.get("response_ids") or []) != expected_responses:
        errors.append("response_stage_count_mismatch")
    if not record.get("response_id"):
        errors.append("missing_response_id")
    if record.get("model") != record.get("requested_model"):
        errors.append("resolved_model_mismatch")
    if not record.get("search_actions"):
        errors.append("missing_external_tool_trace")
    if not record.get("search_sources"):
        errors.append("missing_complete_sources")
    if not record.get("api_citations"):
        errors.append("missing_citations")
    if not prompt_is_label_free(record):
        errors.append("evaluation_label_leakage")
    pit_audit = record.get("point_in_time_audit") or {}
    if not pit_audit:
        errors.append("missing_point_in_time_audit")
    result = record.get("result") or {}
    if result.get("action") not in {"answer", "abstain"}:
        errors.append("invalid_action")
    if record["strategy"] == XBRL_STRATEGY:
        if result.get("action") != "answer":
            errors.append("xbrl_did_not_answer")
        if not result.get("calculation_trace") or not record.get("sec_facts"):
            errors.append("missing_xbrl_calculation_trace")
        if len(result.get("accepted_evidence") or []) != len(record.get("sec_facts") or []):
            errors.append("xbrl_evidence_count_mismatch")
        if not pit_audit.get("final_temporally_compliant"):
            errors.append("xbrl_point_in_time_violation")
        if not pit_audit.get("final_provenance_complete"):
            errors.append("xbrl_incomplete_provenance")
    return errors


def _latest_valid(
    records: list[dict[str, Any]], cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if not validate_record(record):
            latest[(record["case"]["id"], record["strategy"])] = record
    return [
        latest[(case["id"], strategy)]
        for case in cases
        for strategy in STRATEGIES
        if (case["id"], strategy) in latest
    ]


def _rate(records: list[dict[str, Any]], predicate) -> float:
    return sum(bool(predicate(record)) for record in records) / len(records)


def write_evaluation(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    by_strategy = {
        strategy: [record for record in records if record["strategy"] == strategy]
        for strategy in STRATEGIES
    }
    metrics: dict[str, Any] = {}
    for strategy, items in by_strategy.items():
        temporal = aggregate_temporal_metrics(items)
        metrics[strategy] = {
            "runs": len(items),
            "decision_accuracy": _rate(items, exact_two_decimal_correct),
            "answer_coverage": _rate(
                items, lambda item: item["result"]["action"] == "answer"
            ),
            "model_http_stages": sum(int(item.get("http_attempts") or 0) for item in items),
            "external_tool_actions": sum(len(item.get("search_actions") or []) for item in items),
            "sources": sum(len(item.get("search_sources") or []) for item in items),
            "citations": sum(len(item.get("api_citations") or []) for item in items),
            "input_tokens": sum(
                int((item.get("usage") or {}).get("input_tokens") or 0) for item in items
            ),
            "output_tokens": sum(
                int((item.get("usage") or {}).get("output_tokens") or 0) for item in items
            ),
            "sec_http_calls": sum(int(item.get("sec_api_calls") or 0) for item in items),
            "sec_cache_hits": sum(int(item.get("sec_cache_hits") or 0) for item in items),
            "compiler_repair_rate": (
                _rate(items, lambda item: bool(item["result"].get("compiler_repaired")))
                if strategy == XBRL_STRATEGY
                else 0.0
            ),
            **temporal,
            "joint_reliable_answer_rate": _rate(
                items,
                lambda item: (
                    exact_two_decimal_correct(item)
                    and item["point_in_time_audit"]["final_temporally_compliant"]
                    and item["point_in_time_audit"]["final_provenance_complete"]
                ),
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
        float(exact_two_decimal_correct(group[XBRL_STRATEGY]))
        - float(exact_two_decimal_correct(group["plain_agent"]))
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
        "metric": "exact numeric equality at the requested two-decimal answer",
    }
    joint_differences = [
        float(
            exact_two_decimal_correct(group[XBRL_STRATEGY])
            and group[XBRL_STRATEGY]["point_in_time_audit"]["final_temporally_compliant"]
            and group[XBRL_STRATEGY]["point_in_time_audit"]["final_provenance_complete"]
        )
        - float(
            exact_two_decimal_correct(group["plain_agent"])
            and group["plain_agent"]["point_in_time_audit"]["final_temporally_compliant"]
            and group["plain_agent"]["point_in_time_audit"]["final_provenance_complete"]
        )
        for group in by_case.values()
    ]
    joint_boot = sorted(
        sum(
            joint_differences[rng.randrange(len(joint_differences))]
            for _ in joint_differences
        )
        / len(joint_differences)
        for _ in range(10_000)
    )
    joint_comparison = {
        "paired_difference": sum(joint_differences) / len(joint_differences),
        "paired_bootstrap_ci95": [joint_boot[249], joint_boot[9749]],
        "wins": sum(value > 0 for value in joint_differences),
        "ties": sum(value == 0 for value in joint_differences),
        "losses": sum(value < 0 for value in joint_differences),
        "metric": "correct answer + fully dated non-future evidence + complete provenance",
    }
    result = {
        "strategies": metrics,
        "comparison": comparison,
        "joint_reliability_comparison": joint_comparison,
    }
    write_json(output_dir / "metrics.json", result)

    fields = (
        "case_id",
        "question_zh",
        "gold_answer",
        "canonical_unit",
        "plain_action",
        "plain_answer",
        "plain_correct",
        "xbrl_action",
        "xbrl_answer",
        "xbrl_correct",
        "operation",
        "operand_count",
        "compiler_repaired",
        "plain_candidate_future_sources",
        "plain_final_future_evidence",
        "plain_joint_reliable",
        "xbrl_candidate_future_sources",
        "xbrl_final_future_evidence",
        "xbrl_joint_reliable",
    )
    with (output_dir / "case_outcomes.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case_id, group in by_case.items():
            plain = group["plain_agent"]
            xbrl = group[XBRL_STRATEGY]
            trace = xbrl["result"]["calculation_trace"]
            writer.writerow(
                {
                    "case_id": case_id,
                    "question_zh": plain["case"]["question_zh"],
                    "gold_answer": plain["case"]["gold_answer"],
                    "canonical_unit": plain["case"]["canonical_unit"],
                    "plain_action": plain["result"]["action"],
                    "plain_answer": plain["result"].get("answer_value"),
                    "plain_correct": int(exact_two_decimal_correct(plain)),
                    "xbrl_action": xbrl["result"]["action"],
                    "xbrl_answer": xbrl["result"].get("answer_value"),
                    "xbrl_correct": int(exact_two_decimal_correct(xbrl)),
                    "operation": trace["operation"],
                    "operand_count": len(trace["operand_values"]),
                    "compiler_repaired": int(bool(xbrl["result"]["compiler_repaired"])),
                    "plain_candidate_future_sources": plain["point_in_time_audit"]["candidate_sources"]["future"],
                    "plain_final_future_evidence": plain["point_in_time_audit"]["accepted_evidence"]["future"],
                    "plain_joint_reliable": int(
                        exact_two_decimal_correct(plain)
                        and plain["point_in_time_audit"]["final_temporally_compliant"]
                        and plain["point_in_time_audit"]["final_provenance_complete"]
                    ),
                    "xbrl_candidate_future_sources": xbrl["point_in_time_audit"]["candidate_sources"]["future"],
                    "xbrl_final_future_evidence": xbrl["point_in_time_audit"]["accepted_evidence"]["future"],
                    "xbrl_joint_reliable": int(
                        exact_two_decimal_correct(xbrl)
                        and xbrl["point_in_time_audit"]["final_temporally_compliant"]
                        and xbrl["point_in_time_audit"]["final_provenance_complete"]
                    ),
                }
            )
    return result


def write_readme(output_dir: Path, manifest: dict[str, Any]) -> None:
    metrics = manifest["metrics"]
    plain = metrics["strategies"]["plain_agent"]
    xbrl = metrics["strategies"][XBRL_STRATEGY]
    comparison = metrics["comparison"]
    exclusions = manifest["exclusions"]
    text = f"""# ATLAS-PIT-XBRL：20题答案正确性 × 时间可靠性综合实验

这是项目当前的主实验。`claude-sonnet-5` 先把自然语言金融问题编译为受约束程序，
系统再通过 SEC Company Facts XBRL 官方接口取得未经四舍五入的10-K数值，最后用
Python `Decimal` 执行公式。普通搜索Agent使用同一模型和真实Web Search直接回答。
独立PIT审计不再把缺失日期当成安全：它同时检查候选来源、原生引用和最终采用证据是否晚于题目截止日。

## 核心结果

| 方法 | 最终准确率 | 回答覆盖率 | 候选未来来源 | 最终未来证据 | 联合可靠回答率 |
|---|---:|---:|---:|---:|---:|
| 普通搜索 Agent | {plain['decision_accuracy']:.1%} | {plain['answer_coverage']:.1%} | {plain['candidate_future_sources']}/{plain['candidate_sources']} | {plain['accepted_future_evidence']}/{plain['accepted_evidence']} | {plain['joint_reliable_answer_rate']:.1%} |
| **ATLAS-PIT-XBRL** | **{xbrl['decision_accuracy']:.1%}** | **{xbrl['answer_coverage']:.1%}** | **{xbrl['candidate_future_sources']}/{xbrl['candidate_sources']}** | **{xbrl['accepted_future_evidence']}/{xbrl['accepted_evidence']}** | **{xbrl['joint_reliable_answer_rate']:.1%}** |

配对准确率差值为 **{comparison['paired_accuracy_difference'] * 100:+.1f}个百分点**，逐题
{comparison['wins']}胜 / {comparison['ties']}平 / {comparison['losses']}负；
按题bootstrap 95% CI 为 {comparison['paired_bootstrap_ci95'][0] * 100:+.1f} 到
{comparison['paired_bootstrap_ci95'][1] * 100:+.1f}个百分点。

联合可靠回答要求“答案正确 + 最终证据全部有日期且不晚于截止日 + 来源字段完整”。
普通Agent候选来源时间元数据覆盖率为 {plain['candidate_temporal_metadata_coverage']:.1%}，
ATLAS-PIT-XBRL为 {xbrl['candidate_temporal_metadata_coverage']:.1%}；SEC来源占比分别为
{plain['candidate_sec_source_rate']:.1%} 和 {xbrl['candidate_sec_source_rate']:.1%}。

Web时间判定使用搜索供应商在本次运行返回的`page_age`并按URL映射到引用/最终证据；它是可观察的页面时间元数据，
不等同于对所有网页首次发布时间的独立取证。unknown不算确认未来，也不算确认安全。SEC日期来自官方filing记录。

## 为什么它能超过普通搜索

普通搜索必须同时完成找报表、识别口径、抄取多个数、保持方向、计算和四舍五入，任一环节都可能出错。
ATLAS-PIT-XBRL把职责拆开：Claude只编译查询；label-free语法校准操作数顺序；SEC接口提供结构化原值；
Decimal程序只执行白名单公式。因此它不是“更强Prompt”，而是一个可审计的工具增强Agent。

## Gold重新审计

20个gold在运行前由独立 `reference_program` 重新调用SEC官方Company Facts验证，题集哈希为
`{manifest['gold_audit']['case_batch_sha256']}`。运行时提示词明确排除 `gold_answer`、
`gold_calculation` 和 `reference_program`。评分采用题目要求的两位小数精确数值相等，不按任一策略输出改答案。

## 规模与异常

- 模型：`{manifest['protocol']['model']}`；有效运行：{manifest['valid_runs']}/40；
- ATLAS-XBRL计算方法版本：`{XBRL_METHOD_VERSION}`；PIT审计版本：`pit-audit-1.0`；
- 正式运行传输/无效记录：{len(exclusions['errors'])}/{len(exclusions['invalid'])}；
- 运行前开发实验曾暴露方向错误和二手来源拒答，本方法以SEC结构化工具解决，开发结果不计入本表；
- 本实验针对可映射到SEC XBRL的数值推理问题，不能外推到开放域所有问题。

## 文件

- `case_outcomes.csv`：20题逐题配对结果；
- `metrics.json`：聚合、胜平负和配对bootstrap；
- `trace.jsonl`：40条真实调用trace、模型计划、SEC字段、accession、响应哈希和公式；
- `gold_audit.json`：运行前20题gold的SEC复核；
- `study_manifest.json`：协议、提交、预算、题集哈希和排除记录；
- `study_manifest.json`中的题集哈希与case IDs：冻结题集和不进入运行时的参考程序标识。
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_cases(args.case_file, args.limit)
    strategies = tuple(args.strategies)
    unknown = set(strategies) - set(STRATEGIES)
    if unknown:
        raise ValueError(f"Unknown strategies: {sorted(unknown)}")
    model_stages = len(cases) * sum(2 if item == "plain_agent" else 1 for item in strategies)
    planned_attempts = model_stages * (args.max_retries + 1)
    if planned_attempts > args.max_api_calls:
        raise ValueError(
            f"Planned up to {planned_attempts} model HTTP attempts exceeds "
            f"--max-api-calls={args.max_api_calls}"
        )
    print(
        f"ATLAS-XBRL plan: {len(cases)} questions x {len(strategies)} strategies; "
        f"{model_stages} successful model HTTP stages, at most {planned_attempts} "
        f"attempts; model={args.model}. Paid usage may occur."
    )
    if not args.confirm_live:
        print("Plan only: no model or SEC request was sent.")
        return []
    if not credential_is_available("anthropic"):
        raise RuntimeError("No Anthropic credential is available")

    current_git = git_state()
    if current_git["dirty"]:
        raise RuntimeError("Formal XBRL run requires a clean preregistered commit")
    gold_audit = audit_cases(args.case_file)
    if gold_audit["case_count"] != len(load_cases(args.case_file)):
        raise RuntimeError("Gold audit did not verify the complete frozen case file")

    config = RequestConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        max_tool_calls=args.max_tool_calls,
        max_output_tokens=args.max_output_tokens,
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )
    plain_client = AnthropicMessagesWebSearch(config, base_url=args.base_url)
    xbrl_client = AtlasXbrlClient(config, base_url=args.base_url)
    protocol = {
        "protocol_version": "atlas-pit-xbrl-study-1.1",
        "method_version": XBRL_METHOD_VERSION,
        "study_type": "real_llm_point_in_time_sec_xbrl_numeric_reasoning",
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_output_tokens": args.max_output_tokens,
        "baseline_max_web_searches": args.max_tool_calls,
        "baseline_model_http_stages_per_run": 2,
        "xbrl_model_http_stages_per_run": 1,
        "xbrl_external_tool": "SEC Company Facts API with run-level cache",
        "schedule": "cyclic counterbalancing by question",
        "scoring": (
            "exact numeric equality at requested two decimals and exact unit; "
            "joint reliability additionally requires fully dated non-future final "
            "evidence and complete provenance"
        ),
        "temporal_audit": (
            "pit-audit-1.0 over candidates, native citations, and accepted evidence"
        ),
        "case_ids": [case["id"] for case in cases],
        "case_batch_sha256": canonical_sha256(cases),
        "gold_fields_excluded_from_prompts": True,
        "request_config": request_config_view(config),
    }
    protocol_hash = canonical_sha256(protocol)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "trace.jsonl"
    manifest_path = output_dir / "study_manifest.json"
    existing = load_existing(trace_path) if args.resume else []
    previous_manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if args.resume and manifest_path.exists()
        else None
    )
    if previous_manifest and previous_manifest.get("protocol_sha256") != protocol_hash:
        raise RuntimeError("Existing output uses a different XBRL protocol")
    started_at = previous_manifest.get("started_at") if previous_manifest else utc_now()
    completed = {
        (record["case"]["id"], record["strategy"])
        for record in _latest_valid(existing, cases)
    }
    write_json(output_dir / "gold_audit.json", gold_audit)
    write_json(
        manifest_path,
        {
            "status": "running",
            "started_at": started_at,
            "protocol_sha256": protocol_hash,
            "protocol": protocol,
            "preregistered_commit": current_git["commit"],
            "gold_audit": gold_audit,
            "planned_model_http_attempts": planned_attempts,
        },
    )

    expected = len(cases) * len(strategies)
    abort_reason: str | None = None
    for case_index, case in enumerate(cases, start=1):
        order = strategies if case_index % 2 else tuple(reversed(strategies))
        for strategy in order:
            key = (case["id"], strategy)
            if key in completed:
                continue
            print(f"[{len(completed) + 1}/{expected}] {case['id']} - {strategy}")
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
                    else xbrl_client.run(case)
                )
                if strategy == XBRL_STRATEGY:
                    result["strategy_label"] = (
                        "ATLAS-PIT-XBRL（LLM编译 + 截止日SEC事实 + Decimal）"
                    )
                    result["integrated_method_version"] = "atlas-pit-xbrl-1.1"
                raw = result.pop("raw_response")
                suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                raw_path = output_dir / "raw_responses" / f"{case['id']}_{strategy}_{suffix}.json"
                write_json(raw_path, raw)
                result["raw_response_path"] = str(raw_path.relative_to(output_dir))
                result["raw_response_sha256"] = hashlib.sha256(
                    json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                record = {**base, **result}
                record["point_in_time_audit"] = audit_record_temporality(record)
                errors = validate_record(record)
                record["trace_validation_errors"] = errors
                if errors:
                    record["status"] = "invalid"
                    abort_reason = f"{case['id']}/{strategy}: {', '.join(errors)}"
                else:
                    completed.add(key)
            except Exception as exc:
                error = redact_sensitive(str(exc))
                record = {
                    **base,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": error,
                    "trace_validation_errors": ["api_or_tool_run_failed"],
                }
                abort_reason = f"{case['id']}/{strategy}: {error}"
            append_jsonl(trace_path, record)
            existing.append(record)
            if abort_reason and not args.continue_on_error:
                break
        if abort_reason and not args.continue_on_error:
            break

    selected = _latest_valid(existing, cases)
    complete = len(selected) == expected
    metrics = write_evaluation(selected, output_dir) if complete else None
    exclusions = {
        "errors": [
            {
                "case_id": item["case"]["id"],
                "strategy": item["strategy"],
                "error_type": item.get("error_type"),
                "error": item.get("error"),
            }
            for item in existing
            if item.get("status") == "error"
        ],
        "invalid": [
            {
                "case_id": item["case"]["id"],
                "strategy": item["strategy"],
                "errors": item.get("trace_validation_errors"),
            }
            for item in existing
            if item.get("status") == "invalid"
        ],
    }
    manifest = {
        "status": "completed" if complete else "incomplete",
        "started_at": started_at,
        "completed_at": utc_now(),
        "protocol_sha256": protocol_hash,
        "protocol": protocol,
        "preregistered_commit": current_git["commit"],
        "gold_audit": gold_audit,
        "planned_runs": expected,
        "valid_runs": len(selected),
        "planned_model_http_attempts": planned_attempts,
        "metrics": metrics,
        "exclusions": exclusions,
        "failure": abort_reason,
    }
    write_json(manifest_path, manifest)
    write_json(output_dir / "exclusions.json", exclusions)
    if not complete:
        raise RuntimeError(f"XBRL study incomplete: {abort_reason}")
    write_readme(output_dir, manifest)
    print(f"ATLAS-XBRL study completed with {len(selected)} validated records")
    return selected


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run real Claude vs ATLAS-XBRL")
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
    value.add_argument("--max-api-calls", type=int, default=60)
    value.add_argument("--limit", type=int)
    value.add_argument(
        "--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES)
    )
    value.add_argument("--confirm-live", action="store_true")
    value.add_argument("--continue-on-error", action="store_true")
    value.add_argument("--no-resume", dest="resume", action="store_false")
    value.set_defaults(resume=True)
    return value


def main() -> None:
    run(parser().parse_args())


if __name__ == "__main__":
    main()
