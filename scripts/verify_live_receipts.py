"""Replay saved live-research acceptance checks without network or model calls.

Usage: python scripts/verify_live_receipts.py [--json]

Only Python's standard library is used. This command reads the frozen manifest
and archived reports; it never rewrites either. It verifies recorded evidence
consistency, not factual correctness against independent gold answers. Original
HTTP body hashes are receipts: only saved selected-text and raw-XBRL-row hashes
can be recomputed from these artifacts. Semantic review is not rerun.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/verification/live-research-acceptance.json"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def report_checks(report: dict) -> dict:
    """Independent arithmetic on preserved operands; no product imports."""
    docs = {source["id"]: source for source in report["sources"]}
    facts = {fact["id"]: fact for fact in report["financial_evidence"]}
    require(len(docs) == len(report["sources"]), "Duplicate document identifiers")
    require(len(facts) == len(report["financial_evidence"]), "Duplicate fact identifiers")
    checks = []

    def check(kind: str, subject: str, passed: bool, detail: dict | None = None) -> None:
        item = {"kind": kind, "subject": subject, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        checks.append(item)

    for source in docs.values():
        next_day = (date.fromisoformat(source["published_at"][:10]) + timedelta(days=1)).isoformat()
        check("document_time", source["id"], source["available_from"] <= report["as_of"] and source["available_from"] == next_day)
        check("selected_text_hash", source["id"], sha(source["text"].encode("utf-8")) == source["text_sha256"])

    for fact in facts.values():
        row = fact["raw_row"]
        next_day = (date.fromisoformat(row["filed"]) + timedelta(days=1)).isoformat()
        check("raw_fact_time_identity_period", fact["id"],
              fact["ticker"] == report["ticker"] and fact["available_from"] <= report["as_of"]
              and fact["available_from"] == next_day and row["start"] == fact["period_start"]
              and row["end"] == fact["period_end"] and row["accn"] == fact["accession"]
              and Decimal(str(row["val"])) == Decimal(fact["value"]) and row["end"] <= row["filed"])
        fingerprint = {"taxonomy": fact["taxonomy"], "tag": fact["taxonomy_tag"], "unit": fact["unit"], "row": row}
        check("raw_fact_hash", fact["id"], sha(canonical(fingerprint)) == fact["fact_sha256"])

    def rounded(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    for answer in report["financial_answers"]:
        operands = [facts[ref] for ref in answer["evidence_ids"]]
        check("answer_period_and_accession", answer["id"],
              all(fact["fiscal_year"] == answer["fiscal_year"] and fact["period_start"] == answer["period_start"]
                  and fact["period_end"] == answer["period_end"] for fact in operands)
              and len({fact["accession"] for fact in operands}) == 1)
        values = {fact["metric_id"]: Decimal(fact["value"]) for fact in operands}
        key, unit = answer["metric_id"], "%"
        if key == "operating_margin":
            expected = rounded(values["operating_income"] / values["revenue"] * 100)
        elif key == "rd_ratio":
            expected = rounded(values["research_and_development"] / values["revenue"] * 100)
        elif key == "capex_ratio":
            expected = rounded(values["capital_expenditure"] / values["revenue"] * 100)
        elif key == "free_cash_flow_margin":
            expected = rounded((values["operating_cash_flow"] - values["capital_expenditure"]) / values["revenue"] * 100)
        elif key == "free_cash_flow":
            expected, unit = values["operating_cash_flow"] - values["capital_expenditure"], "USD"
        else:
            expected, unit = values[key], "USD"
        check("independent_decimal_recompute", answer["id"], Decimal(answer["value"]) == expected and answer["unit"] == unit,
              {"expected_value": str(expected), "expected_unit": unit, "metric_id": key})

    quote_count, literal_quotes = 0, 0
    financial_ids = {answer["id"] for answer in report["financial_answers"]}
    for claim in report["claims"]:
        check("claim_references", claim["id"], all(ref in docs or ref in financial_ids for ref in claim["evidence_ids"]))
        for index, quote in enumerate(claim["quotes"], 1):
            quote_count += 1
            text = docs[quote["source_id"]]["text"]
            literal_quotes += int(quote["quote"] in text)
            check("quote_whitespace_normalized_substring", f"{claim['id']}.quote{index}",
                  quote["source_id"] in claim["evidence_ids"] and " ".join(quote["quote"].split()) in " ".join(text.split()))
    check("delivered_count_matches", "report", report["verification"]["accepted_claims"] == len(report["claims"]))
    return {"passed": sum(item["passed"] for item in checks), "failed": sum(not item["passed"] for item in checks),
            "quotes_checked": quote_count, "quotes_literal_substring": literal_quotes, "items": checks}


def verify_manifest(manifest_path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT) -> dict:
    manifest = json.loads(manifest_path.read_bytes())
    require(manifest.get("artifact_type") == "integration_acceptance_not_accuracy_benchmark", "Unexpected manifest type")
    require(manifest.get("schema_version") == 1, "Unsupported manifest schema")
    require(bool(manifest.get("runs")), "Manifest contains no runs")
    root = root.resolve()
    check_count, attempts, completed, known_tokens = 0, 0, 0, 0
    usage_complete = True
    seen = set()
    for run in manifest["runs"]:
        prefix = run["id"] + ": "
        require(run["id"] not in seen, prefix + "duplicate run identifier")
        seen.add(run["id"])
        report_path = (root / run["report_path"]).resolve()
        require(report_path.is_relative_to(root), prefix + "report path escapes repository")
        raw = report_path.read_bytes()
        require(sha(raw) == run["report_sha256"], prefix + "archived report SHA256 mismatch")
        require(len(raw) == run["report_bytes"], prefix + "archived report size mismatch")
        report = json.loads(raw)
        require(report["generated_at"] == run["generated_at"], prefix + "generation time mismatch")
        request = {key: report[key] for key in ("question", "ticker", "as_of", "data_mode")}
        require(request == run["request_as_recorded_in_report"], prefix + "request fields mismatch")
        for field in ("plan", "searches", "model_receipts", "gaps"):
            require(report[field] == run[field], prefix + field + " mismatch")
        require(report["verification"] == run["verification_as_reported"], prefix + "verification receipt mismatch")
        for field in ("sources", "financial_answers"):
            require(len(report[field]) == len(run[field]), prefix + field + " count mismatch")
            for saved, summary in zip(report[field], run[field]):
                require(all(saved.get(key) == value for key, value in summary.items()), prefix + field + " metadata mismatch")
        companyfacts = list({(fact["source_url"], fact["retrieved_at"]): {
            key: fact[key] for key in ("source_url", "retrieved_at", "canonical_payload_sha256", "upstream_response_sha256")
        } for fact in report["financial_evidence"]}.values())
        require(companyfacts == run["companyfacts_receipts"], prefix + "CompanyFacts receipts mismatch")
        checks = report_checks(report)
        require(checks["failed"] == 0, prefix + "evidence/arithmetic check failed: " + str([item for item in checks["items"] if not item["passed"]]))
        for key, value in checks.items():
            require(run["offline_checks"][key] == value, prefix + "recorded offline check differs: " + key)
        check_count += checks["passed"]
        receipts = report["model_receipts"]
        run_completed = sum(receipt["status"] == "completed" for receipt in receipts)
        run_tokens = sum(receipt.get("usage", {}).get("total_tokens", 0) for receipt in receipts)
        run_complete = all("total_tokens" in receipt.get("usage", {}) for receipt in receipts)
        require(len(receipts) == run["model_attempts_recorded"] and run_completed == run["completed_provider_receipts"], prefix + "model attempt counts mismatch")
        require(run_tokens == run["known_total_tokens"] and run_complete == run["usage_complete_for_recorded_attempts"], prefix + "usage receipt mismatch")
        attempts += len(receipts)
        completed += run_completed
        known_tokens += run_tokens
        usage_complete = usage_complete and run_complete
    totals = {"reports": len(manifest["runs"]), "recorded_model_attempts": attempts, "completed_provider_receipts": completed,
              "known_total_tokens_lower_bound": known_tokens, "usage_complete": usage_complete}
    require(totals == manifest["totals"], "Manifest aggregate counts/usage mismatch")
    return {"status": "passed", "reports_verified": totals["reports"], "recorded_checks_replayed": check_count,
            "archive_sha256_and_metadata": "matched", "totals": totals,
            "scope": "Offline integration acceptance; not an accuracy estimate or repeated semantic review."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="Print machine-readable verification result")
    args = parser.parse_args()
    try:
        result = verify_manifest(args.manifest)
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        print(f"Live receipt verification FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Verified {result['reports_verified']} archived reports: {result['recorded_checks_replayed']} recorded checks passed; SHA256 and manifest metadata match.")
        print(result["scope"])
        if not result["totals"]["usage_complete"]:
            print(f"Known token lower bound: {result['totals']['known_total_tokens_lower_bound']}; failed-call usage is incomplete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
