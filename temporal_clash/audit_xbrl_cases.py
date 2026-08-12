from __future__ import annotations

import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .atlas_xbrl import SecCompanyFactsClient, execute_program
from .live_validate import canonical_sha256


HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "xbrl_20q_cases.json"


def audit_cases(case_file: Path = DEFAULT_CASES) -> dict:
    cases = json.loads(case_file.read_text(encoding="utf-8"))
    client = SecCompanyFactsClient()
    rows = []
    for case in cases:
        reference = case["reference_program"]
        requests = [
            {"ticker": ticker, "metric": metric, "fiscal_year": fiscal_year}
            for ticker, metric, fiscal_year in reference["facts"]
        ]
        facts = [
            client.fact(request, cutoff_date=case["cutoff_date"])[0]
            for request in requests
        ]
        value, formula = execute_program(
            reference["operation"], facts, return_magnitude=True
        )
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        observed = format(rounded, "f")
        if observed != str(case["gold_answer"]):
            raise RuntimeError(
                f"Gold mismatch for {case['id']}: file={case['gold_answer']} SEC={observed}"
            )
        rows.append(
            {
                "case_id": case["id"],
                "gold_answer": observed,
                "unit": case["canonical_unit"],
                "formula": formula,
                "operands": [fact["value"] for fact in facts],
                "accessions": sorted({fact["accession"] for fact in facts}),
                "sec_response_hashes": sorted(
                    {fact["sec_response_sha256"] for fact in facts}
                ),
            }
        )
    return {
        "status": "verified",
        "case_count": len(rows),
        "case_batch_sha256": canonical_sha256(cases),
        "method": (
            "Independent reference_program execution over official SEC Company "
            "Facts; Decimal ROUND_HALF_UP to two decimals"
        ),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify frozen XBRL benchmark gold")
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_cases(args.case_file)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(
        f"Verified {result['case_count']} XBRL gold answers; "
        f"batch={result['case_batch_sha256']}"
    )


if __name__ == "__main__":
    main()
