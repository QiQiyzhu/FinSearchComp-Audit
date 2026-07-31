from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from .detector import TemporalLeakageDetector
from .policies import POLICIES, POLICY_PROFILES


HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
SUMMARY_CSV = RESULTS_DIR / "experiment_table.csv"
PER_CONDITION_CSV = RESULTS_DIR / "per_condition.csv"
PREDICTIONS_JSONL = RESULTS_DIR / "predictions.jsonl"
DETECTOR_SUMMARY_CSV = RESULTS_DIR / "detector_table.csv"
DETECTOR_PREDICTIONS_JSONL = RESULTS_DIR / "detector_predictions.jsonl"
SUMMARY_MD = RESULTS_DIR / "summary.md"


def _is_future(selected: dict[str, Any] | None, case: dict[str, Any]) -> bool:
    if selected is None:
        return False
    return date.fromisoformat(selected["published_at"]) > date.fromisoformat(
        case["cutoff_date"]
    )


def _record_prediction(
    method_key: str,
    method_label: str,
    case: dict[str, Any],
    selected: dict[str, Any] | None,
) -> dict[str, Any]:
    abstained = selected is None
    selected_value = None if abstained else selected["answer_value"]
    answer_correct = (
        case["expected_action"] == "answer"
        and not abstained
        and selected_value == case["gold_answer"]
    )
    decision_correct = (
        abstained
        if case["expected_action"] == "abstain"
        else answer_correct
    )
    return {
        "method": method_key,
        "method_label": method_label,
        "case_id": case["case_id"],
        "condition": case["evidence_condition"],
        "expected_action": case["expected_action"],
        "selected_candidate_id": None if abstained else selected["candidate_id"],
        "selected_answer": selected_value,
        "abstained": abstained,
        "answer_correct": answer_correct,
        "decision_correct": decision_correct,
        "temporal_violation": _is_future(selected, case),
        "adopted_perturbation": False if abstained else selected["is_perturbed"],
        "citation_support": False if abstained else selected["supports_gold"],
    }


def _rate(records: list[dict[str, Any]], key: str) -> float:
    return sum(bool(record[key]) for record in records) / len(records)


def _summarize(records: list[dict[str, Any]]) -> dict[str, float]:
    answerable = [r for r in records if r["expected_action"] == "answer"]
    future_only = [r for r in records if r["condition"] == "future_only"]
    perturbed = [r for r in records if r["condition"] != "clean"]
    answered = [r for r in records if not r["abstained"]]
    clean = [r for r in records if r["condition"] == "clean"]
    challenge = [r for r in records if r["condition"] != "clean"]
    clean_accuracy = _rate(clean, "decision_correct")
    challenge_accuracy = _rate(challenge, "decision_correct")
    return {
        "decision_accuracy": _rate(records, "decision_correct"),
        "answer_accuracy": _rate(answerable, "answer_correct"),
        "correct_abstention_rate": _rate(future_only, "abstained"),
        "temporal_violation_rate": _rate(records, "temporal_violation"),
        "perturbation_adoption_rate": _rate(perturbed, "adopted_perturbation"),
        "citation_support_rate": (
            _rate(answered, "citation_support") if answered else 0.0
        ),
        "coverage": len(answered) / len(records),
        "challenge_accuracy": challenge_accuracy,
        "temporal_robustness_gap": clean_accuracy - challenge_accuracy,
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _evaluate_candidate_detection(
    cases: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]]]:
    summaries: dict[str, dict[str, float]] = {}
    records: list[dict[str, Any]] = []

    for method_key, (method_label, _) in POLICIES.items():
        profile = POLICY_PROFILES[method_key]
        detector = TemporalLeakageDetector(profile)
        method_records: list[dict[str, Any]] = []

        for case in cases:
            for candidate in case["candidates"]:
                audit = detector.audit(candidate, case)
                record = {
                    "method": method_key,
                    "method_label": method_label,
                    "profile": profile,
                    "case_id": case["case_id"],
                    "condition": case["evidence_condition"],
                    "candidate_id": candidate["candidate_id"],
                    "is_perturbed": candidate["is_perturbed"],
                    "predicted_violation": not audit.accepted,
                    "verdict": audit.verdict,
                    "risk_score": audit.risk_score,
                    "violations": list(audit.violations),
                    "checks": [check.to_dict() for check in audit.checks],
                }
                method_records.append(record)
                records.append(record)

        tp = sum(
            r["is_perturbed"] and r["predicted_violation"] for r in method_records
        )
        fp = sum(
            (not r["is_perturbed"]) and r["predicted_violation"]
            for r in method_records
        )
        fn = sum(
            r["is_perturbed"] and (not r["predicted_violation"])
            for r in method_records
        )
        tn = sum(
            (not r["is_perturbed"]) and (not r["predicted_violation"])
            for r in method_records
        )
        precision = _divide(tp, tp + fp)
        recall = _divide(tp, tp + fn)
        f1 = _divide(2 * precision * recall, precision + recall)
        future = [
            r
            for r in method_records
            if r["is_perturbed"] and r["condition"] == "future_only"
        ]
        metadata_conflicts = [
            r
            for r in method_records
            if r["is_perturbed"] and r["condition"] != "future_only"
        ]
        summaries[method_key] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "future_leakage_recall": _divide(
                sum(r["predicted_violation"] for r in future), len(future)
            ),
            "metadata_conflict_recall": _divide(
                sum(r["predicted_violation"] for r in metadata_conflicts),
                len(metadata_conflicts),
            ),
            "false_positive_rate": _divide(fp, fp + tn),
            "safe_evidence_retention_rate": _divide(tn, tn + fp),
        }

    return summaries, records


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    predictions: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for method_key, (method_label, policy) in POLICIES.items():
        for case in cases:
            record = _record_prediction(
                method_key, method_label, case, policy(case)
            )
            predictions.append(record)
            grouped[method_key].append(record)

    summaries = {key: _summarize(items) for key, items in grouped.items()}
    detector_summaries, detector_predictions = _evaluate_candidate_detection(cases)
    PREDICTIONS_JSONL.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in predictions),
        encoding="utf-8",
    )
    DETECTOR_PREDICTIONS_JSONL.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in detector_predictions
        ),
        encoding="utf-8",
    )

    fields = [
        "method",
        "decision_accuracy",
        "answer_accuracy",
        "correct_abstention_rate",
        "temporal_violation_rate",
        "perturbation_adoption_rate",
        "citation_support_rate",
        "coverage",
        "challenge_accuracy",
        "temporal_robustness_gap",
    ]
    with SUMMARY_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key, (label, _) in POLICIES.items():
            writer.writerow(
                {
                    "method": label,
                    **{name: _pct(summaries[key][name]) for name in fields[1:]},
                }
            )

    detector_fields = [
        "method",
        "precision",
        "recall",
        "f1",
        "future_leakage_recall",
        "metadata_conflict_recall",
        "false_positive_rate",
        "safe_evidence_retention_rate",
    ]
    with DETECTOR_SUMMARY_CSV.open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=detector_fields)
        writer.writeheader()
        for key, (label, _) in POLICIES.items():
            writer.writerow(
                {
                    "method": label,
                    **{
                        name: _pct(detector_summaries[key][name])
                        for name in detector_fields[1:]
                    },
                }
            )

    condition_rows: list[dict[str, str]] = []
    for key, (label, _) in POLICIES.items():
        by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in grouped[key]:
            by_condition[record["condition"]].append(record)
        for condition, items in sorted(by_condition.items()):
            condition_rows.append(
                {
                    "method": label,
                    "condition": condition,
                    "decision_accuracy": _pct(_rate(items, "decision_correct")),
                    "abstention_rate": _pct(_rate(items, "abstained")),
                    "perturbation_adoption_rate": _pct(
                        _rate(items, "adopted_perturbation")
                    ),
                }
            )
    with PER_CONDITION_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=condition_rows[0].keys())
        writer.writeheader()
        writer.writerows(condition_rows)

    lines = [
        "# FinTemporalClash Mini：初步策略回放",
        "",
        "> 这些数值来自确定性策略回放，不是 LLM API 实测。它用于验证数据结构、冲突类型和评价脚本，不能据此宣称某个模型更强。",
        "",
        "## 数据",
        "",
        f"- {len(cases)} 条受控实例：20 个真实问题 × 5 种证据条件。",
        "- 条件：干净证据、仅未来证据、错期间冲突、错单位冲突、错版本冲突。",
        "- 人工扰动证据均带 `synthetic://` URL 与 `is_perturbed=true`，不会和真实来源混淆。",
        "",
        "## 总表",
        "",
        "| 方法 | 决策准确率 | 可回答题答案准确率 | 未来证据正确拒答率 | 时间违规率 | 扰动采纳率 | 引用支持率 | 覆盖率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, (label, _) in POLICIES.items():
        metrics = summaries[key]
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    _pct(metrics["decision_accuracy"]),
                    _pct(metrics["answer_accuracy"]),
                    _pct(metrics["correct_abstention_rate"]),
                    _pct(metrics["temporal_violation_rate"]),
                    _pct(metrics["perturbation_adoption_rate"]),
                    _pct(metrics["citation_support_rate"]),
                    _pct(metrics["coverage"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Temporal Leakage Detector 候选级检测",
            "",
            "| 方法 | Precision | Recall | F1 | 未来泄露召回率 | 元数据冲突召回率 | 安全证据保留率 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, (label, _) in POLICIES.items():
        metrics = detector_summaries[key]
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    _pct(metrics["precision"]),
                    _pct(metrics["recall"]),
                    _pct(metrics["f1"]),
                    _pct(metrics["future_leakage_recall"]),
                    _pct(metrics["metadata_conflict_recall"]),
                    _pct(metrics["safe_evidence_retention_rate"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 新指标",
            "",
            "- **挑战条件准确率**：只在未来、错期间、错单位、错版本四类压力测试上计算。",
            "- **Temporal Robustness Gap (TRG)**：干净条件准确率减去挑战条件准确率；越接近 0 越稳定。",
            "- **候选级检测 F1**：把人工扰动证据视为正类，衡量检测器是否既能拦截冲突，又不误杀安全证据。",
            "",
            "## 解释",
            "",
            "- 普通 Agent 代理策略总是使用第一条证据，因此直接暴露于所有人工冲突。",
            "- 时间约束 Prompt 只执行截止日检查，能拒绝未来证据，但仍会采纳错期间、错单位和错版本。",
            "- 元数据过滤器增加期间与版本过滤，但刻意不检查单位，用于形成可解释的消融。",
            "- TEG 验证器同时检查日期、期间、版本和单位；它在这个规则完全可观测的合成环境中达到满分是结构预期，不是泛化能力证明。",
            "",
            "## 下一步",
            "",
            "先运行 20 条分层真实 Agent pilot，保留同一批实例与指标；另行记录模型、提示词、搜索引擎、运行日期、费用和完整 trace。验证协议无误后，再扩展到完整 100 条。",
        ]
    )
    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "selection": summaries,
        "detection": detector_summaries,
        "predictions": predictions,
        "detector_predictions": detector_predictions,
    }
