"""Apply a deterministic evidence gate to the already-exposed 36-run PIT pilot.

No retrieval, model call, new gold or model-response repair occurs. The gate
receives only the original request, requested metric and original response.
Gold is consulted *after* the decision, solely by the frozen pilot scorer.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pit_benchmark.pilot import canonical, load_fixture, parse_answer, score_answer
from pit_benchmark.run import replay

SOURCE_RECEIPT = ROOT / "docs" / "verification" / "pit-pilot-live-20260926.json"
DEFAULT_RECEIPT = ROOT / "docs" / "verification" / "pit-gate-replay-v3.json"
DEFAULT_SUMMARY = ROOT / "site" / "terminal" / "data" / "pit_gate_summary.json"
GATE_VERSION = "supplied-evidence-gate-1.0"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rounded_rational(value: Fraction) -> str:
    """Exact half-up final display, independent of product and gold calculator."""
    scaled = abs(value) * 100
    whole = (scaled.numerator * 2 + scaled.denominator) // (scaled.denominator * 2)
    return ("-" if value < 0 and whole else "") + f"{whole // 100}.{whole % 100:02d}"


def gate_answer(request: dict[str, Any], metric: str, raw_answer: dict | str) -> dict[str, Any]:
    """Never reads fixture, gold, phase labels, conditions or the finance cube."""
    definitions = {"revenue": {"inputs": ["revenue"], "unit": "USD"},
                   "operating_margin": {"inputs": ["operating_income", "revenue"], "unit": "%"}}
    definition = definitions.get(metric)
    unit = definition["unit"] if definition else None

    def reject(reason: str, state: str = "unknown", *, retained=False) -> dict:
        return {"accepted": False, "decision": "retained_abstention" if retained else "blocked_answer",
                "reason": reason, "typed_state": state, "verified_evidence_ids": [], "computed_value": None,
                "answer": {"action": "abstain", "value": None, "unit": unit, "evidence_ids": [],
                           "explanation": f"Evidence gate: {reason}."}}

    try:
        answer = parse_answer(raw_answer if isinstance(raw_answer, str) else canonical(raw_answer))
    except (ValueError, TypeError):
        return reject("invalid_response")
    if answer["action"] == "abstain":
        return reject("model_abstained", retained=True)
    if definition is None:
        return reject("unsupported_metric", "unsupported")
    try:
        cutoff = date.fromisoformat(request["as_of"])
        scope = request["evidence_scope"]
        documents = request["documents"]
        if not isinstance(documents, list):
            raise ValueError("documents")
        document_map = {item["id"]: item for item in documents}
        if len(document_map) != len(documents):
            return reject("conflicting_document_ids", "conflict")
    except (KeyError, TypeError, ValueError):
        return reject("invalid_request")
    cited = answer["evidence_ids"]
    if not cited:
        return reject("no_cited_evidence")
    if any(identifier not in document_map for identifier in cited):
        return reject("citation_not_supplied")
    selected = [document_map[identifier] for identifier in cited]
    try:
        for document in selected:
            if document["ticker"] != scope["ticker"] or document["accession"] != scope["accession"] or document["period_start"] != scope["period_start"] or document["period_end"] != scope["period_end"] or document["form"] not in {"10-K", "10-K/A"} or document["unit"] != "USD":
                return reject("document_scope_mismatch", "conflict")
            if date.fromisoformat(document["period_end"]) > date.fromisoformat(document["filing_date"]):
                return reject("invalid_period", "conflict")
            if date.fromisoformat(document["filing_date"]) + timedelta(days=1) > cutoff:
                return reject("future_or_same_day_evidence", "unavailable_as_of")
        operands = {}
        for required in definition["inputs"]:
            matching = [item for item in selected if item["metric"] == required]
            if not matching:
                return reject("incomplete_operands")
            values = {Fraction(item["value"]) for item in matching}
            if len(values) != 1:
                return reject("conflicting_operands", "conflict")
            operands[required] = next(iter(values))
        if metric == "revenue":
            expected = operands["revenue"]
            computed = str(expected.numerator) if expected.denominator == 1 else str(expected)
        else:
            if operands["revenue"] <= 0:
                return reject("nonpositive_denominator", "unsupported")
            computed = rounded_rational(operands["operating_income"] / operands["revenue"] * 100)
            expected = Fraction(computed)
        if answer["unit"] != unit or Fraction(answer["value"]) != expected:
            return reject("numeric_or_unit_mismatch", "available")
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        return reject("invalid_document")
    # Free-form explanations are not factual claims certified by this gate.
    # Publish a deterministic statement and preserve the raw prose in receipt.
    verified = {**answer, "explanation": "Verified structured value, unit, complete operands and historical availability against supplied documents."}
    return {"accepted": True, "decision": "accepted", "reason": "complete_supported_value", "typed_state": "available",
            "verified_evidence_ids": cited, "computed_value": computed, "answer": verified}


def verified_original(source_path: Path = SOURCE_RECEIPT) -> tuple[dict, dict, dict[str, dict]]:
    """First replay and verify original raw trace; never mutate the old artifacts."""
    receipt = json.loads(source_path.read_text(encoding="utf-8"))
    published = ROOT / "site" / "workbench" / "data" / "pit_pilot.json"
    if source_path == SOURCE_RECEIPT and source_path.read_bytes() != published.read_bytes():
        raise ValueError("Original published receipt changed")
    trace_path = source_path.with_name(receipt["trace_file"])
    fixture, manifest, audit = load_fixture()
    rebuilt = replay(trace_path, fixture, manifest, audit)
    fields = ("dataset_sha256", "frozen_at", "protocol", "cases", "evidence", "provenance", "fixture_audit", "execution", "started_at", "completed_at", "trace_sha256", "runs", "summary")
    for field in fields:
        if receipt[field] != rebuilt[field]:
            raise ValueError(f"Original receipt/trace mismatch: {field}")
    requests = {}
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["event"] == "request":
            request = entry["request"]
            user_messages = [item for item in request["messages"] if item["role"] == "user"]
            if len(user_messages) != 1 or entry["run_id"] in requests:
                raise ValueError("Ambiguous original request")
            requests[entry["run_id"]] = json.loads(user_messages[0]["content"])
    return receipt, fixture, requests


def aggregate(runs: list[dict], score_key: str, answer_key: str) -> dict:
    pre = [item for item in runs if item["phase"] == "pre"]
    post = [item for item in runs if item["phase"] == "post"]
    summary = {"planned": len(runs), "decision_correct": sum(item[score_key]["decision_correct"] for item in runs),
               "pre_planned": len(pre), "pre_correct_abstention": sum(item[score_key]["decision_correct"] for item in pre),
               "pre_unsupported_answers": sum(item[score_key]["unsupported_historical_answer"] for item in pre),
               "post_planned": len(post), "post_answers": sum(item[answer_key]["action"] == "answer" for item in post),
               "post_numeric_correct": sum(bool(item[score_key]["numeric_correct"]) for item in post),
               "post_correct_supported": sum(item[score_key]["joint_correct_and_supported"] for item in post),
               "future_evidence_accepted": sum(item[score_key]["accepted_future_evidence"] for item in runs)}
    return summary


def run_replay() -> dict:
    receipt, fixture, requests = verified_original()
    cases = {item["id"]: item for item in fixture["cases"]}
    outcomes = []
    for original in receipt["runs"]:
        if original["status"] != "completed" or not original.get("answer"):
            raise ValueError("This exposed-pilot replay requires all 36 original completed outputs")
        case = cases[original["case_id"]]
        # This is the only gate invocation: it receives no gold/phase/condition.
        decision = gate_answer(requests[original["id"]], case["metric"], original["answer"])
        # The untouched legacy scorer is used only after the decision.
        score = score_answer(fixture, case, original["phase"], original["condition"], decision["answer"])
        outcomes.append({"id": original["id"], "case_id": case["id"], "phase": original["phase"], "condition": original["condition"],
                         "as_of": original["as_of"], "original_answer": original["answer"], "original_score": original["score"],
                         "gate": decision, "gated_answer": decision["answer"], "gated_score": score,
                         "original_request_sha256": hashlib.sha256(canonical(requests[original["id"]]).encode("utf-8")).hexdigest()})
    before, after = aggregate(outcomes, "original_score", "original_answer"), aggregate(outcomes, "gated_score", "gated_answer")
    groups = [{"condition": condition, "before": aggregate([item for item in outcomes if item["condition"] == condition], "original_score", "original_answer"),
               "after": aggregate([item for item in outcomes if item["condition"] == condition], "gated_score", "gated_answer")}
              for condition in ["closed_book", "unfiltered", "pit_filtered"]]
    return {"schema_version": 1, "experiment_id": "pit-exposed-pilot-evidence-gate-replay-v3", "gate_version": GATE_VERSION,
            "experiment_type": "deterministic_system_intervention_on_already_exposed_responses",
            "source_dataset_sha256": receipt["dataset_sha256"], "source_receipt_sha256": file_sha(SOURCE_RECEIPT),
            "source_trace_sha256": receipt["trace_sha256"], "gate_code_sha256": file_sha(Path(__file__)),
            "network_calls": 0, "new_model_calls": 0, "retrieval_calls": 0,
            "protocol": {"decision_inputs": ["original supplied documents", "original cutoff", "requested metric and formula", "original structured response"],
                         "gold_in_gate": False, "availability": "filed_date_plus_one_calendar_day",
                         "action": "Accept only if the original response cites all required supplied operands, all are historically available and the structured value/unit equal exact recomputation. Otherwise abstain. No value repair or retrieval.",
                         "scorer": "unchanged pit_benchmark.pilot.score_answer, applied after gate",
                         "limitations": ["36 outputs reuse 6 exposed development questions and 3 shared filings; not a new heldout/model evaluation.",
                                         "This changes the system evidence policy; it does not improve or re-run the LLM.",
                                         "Memory-only correct post-filing answers are blocked, reducing answer coverage.",
                                         "The gate certifies the structured value, unit and citations only; raw model explanatory prose is replaced by a deterministic statement.",
                                         "Historical support is relative to supplied designated filings, not all public information."]},
            "summary": {"before": before, "after": after,
                        "post_answer_coverage_loss": before["post_answers"] - after["post_answers"],
                        "accepted_original_answers": sum(item["gate"]["accepted"] for item in outcomes),
                        "blocked_original_answers": sum(item["gate"]["decision"] == "blocked_answer" for item in outcomes),
                        "retained_model_abstentions": sum(item["gate"]["decision"] == "retained_abstention" for item in outcomes),
                        "gate_reason_counts": dict(Counter(item["gate"]["reason"] for item in outcomes)), "by_condition": groups},
            "runs": outcomes}


def public_summary(receipt: dict) -> dict:
    return {key: receipt[key] for key in ["schema_version", "experiment_id", "experiment_type", "gate_version", "source_dataset_sha256", "source_receipt_sha256", "source_trace_sha256", "gate_code_sha256", "network_calls", "new_model_calls", "retrieval_calls", "protocol", "summary"]} | {
        "receipt_url": "https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/verification/pit-gate-replay-v3.json",
        "methods_url": "https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/PIT_GATE_INTERVENTION.md",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--verify", action="store_true", help="Rebuild in memory and require existing receipt+summary match exactly.")
    args = parser.parse_args()
    receipt = run_replay()
    summary = public_summary(receipt)
    if args.verify:
        if json.loads(args.output.read_text(encoding="utf-8")) != receipt or json.loads(args.summary.read_text(encoding="utf-8")) != summary:
            raise ValueError("Published gate replay differs from deterministic reconstruction")
    else:
        if args.output.exists() or args.summary.exists():
            parser.error("Refuse to overwrite replay artifacts; use --verify or new output paths.")
        for path, payload in [(args.output, receipt), (args.summary, summary)]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"verified" if args.verify else "written": True, "network_calls": 0, "new_model_calls": 0, "summary": receipt["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
