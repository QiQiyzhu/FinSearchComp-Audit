"""Independent SEC/Fraction scorer for structured terminal queries.

The scorer never imports product calculations. An isolated, network-denied worker
invokes the implementation; raw SEC rows, dates and rational gold are checked here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/terminal"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rounded(value):
    scaled = abs(value) * 100
    whole = (scaled.numerator * 2 + scaled.denominator) // (scaled.denominator * 2)
    return Fraction(whole if value >= 0 else -whole, 100)


def independent_value(case, gold):
    expected = case["expected"]
    rows = expected["operands"]
    values = {row["metric_id"]: Fraction(str(row["row"]["val"])) for row in rows}
    metric = case["request"]["metric_id"]
    if metric not in gold["formulas"]:
        return values[metric]
    operation, left, right = gold["formulas"][metric]
    a, b = values[left], values[right]
    if operation == "subtract":
        return a - b
    return rounded(a / b * (100 if operation == "ratio" else 1))


def audit_fixtures():
    manifest = read(FIXTURES / "manifest.json")
    for name, expected in manifest["files"].items():
        assert sha((FIXTURES / name).read_bytes()) == expected, f"Frozen file changed: {name}"
    assert sha(canonical(manifest["files"])) == manifest["dataset_sha256"]
    gold = read(FIXTURES / "gold.json")
    raw = {}
    for ticker, expected in gold["raw_payload_sha256"].items():
        payload = gzip.decompress((ROOT / f"research_workbench/data/terminal_upstream/{ticker}.json.gz").read_bytes())
        raw[ticker] = json.loads(payload)
        assert sha(canonical(raw[ticker])) == expected, f"Raw SEC payload changed: {ticker}"
    cases = read(FIXTURES / "development.json")["cases"] + read(FIXTURES / "heldout.json")["cases"]
    audited_rows = 0
    for case in cases:
        req, expected = case["request"], case["expected"]
        period = expected.get("period")
        if period:
            assert str(date.fromisoformat(period["first_filed"]) + timedelta(days=1)) == period["available_from"]
        for operand in expected["operands"]:
            payload = raw[operand["ticker"]]
            assert payload["cik"] == operand["cik"]
            rows = payload["facts"]["us-gaap"][operand["tag"]]["units"][operand["unit"]]
            row = operand["row"]
            assert row in rows, f"Literal gold absent from original: {case['id']}"
            assert row["filed"] < req["cutoff"], f"Gold uses future filing: {case['id']}"
            assert row["end"] == period["end"]
            if operand["metric_id"] not in gold["instant_metrics"]:
                assert row["start"] == period["start"]
            audited_rows += 1
        if expected["status"] == "available":
            assert independent_value(case, gold) == Fraction(expected["value"]), f"Arithmetic gold mismatch: {case['id']}"
        elif expected["status"] == "unavailable_as_of" and period:
            assert req["cutoff"] < period["available_from"]
    return manifest, gold, raw, cases, {"literal_rows_checked": audited_rows, "raw_issuers": len(raw), "frozen_cases": len(cases), "gold_arithmetic": "Fraction; half-up final ratios to 2dp"}


def normalized_evidence(row):
    """Product row shape is intentionally normalized without importing its code."""
    original = row.get("raw_row", row.get("row", row.get("raw", row)))
    return {
        "ticker": row.get("ticker"),
        "tag": row.get("tag", row.get("taxonomy_tag", row.get("xbrl_tag"))),
        "unit": row.get("unit", "USD"),
        "value": str(original.get("val", original.get("value", row.get("value")))),
        "start": original.get("start", row.get("period_start")),
        "end": original.get("end", row.get("period_end")),
        "filed": original.get("filed", row.get("filed")),
        "accn": original.get("accn", row.get("accession", row.get("accession_number"))),
        "form": original.get("form", row.get("form")),
    }


def evidence_in_raw(item, raw):
    if item["ticker"] not in raw or item["unit"] != "USD":
        return False
    rows = raw[item["ticker"]]["facts"]["us-gaap"].get(item["tag"], {}).get("units", {}).get("USD", [])
    for row in rows:
        if all(str(row.get(key)) == str(item[target]) for key, target in [("val", "value"), ("end", "end"), ("filed", "filed"), ("accn", "accn")]):
            if item["start"] in (None, row.get("start")) and item["form"] in (None, row.get("form")):
                return row.get("form") in {"10-K", "10-K/A"}
    return False


def score_case(case, output, gold, raw):
    req, expected = case["request"], case["expected"]
    metric = output.get("metric", {})
    evidence = output.get("evidence", {})
    refs = metric.get("evidence_ids", [])
    selected = [normalized_evidence(evidence[eid]) for eid in refs if eid in evidence]
    status_ok = metric.get("status") == expected["status"]
    dimensions = {
        "availability_status": status_ok,
        "entity_and_fiscal_year": metric.get("ticker") == req["ticker"] and metric.get("fiscal_year") == req["fiscal_year"],
    }
    if expected["status"] != "available":
        dimensions["no_unjustified_value"] = metric.get("value") is None
        # Evidence included in refusals must still be genuine and time-valid.
        dimensions["source_integrity"] = len(selected) == len(refs) and all(evidence_in_raw(row, raw) for row in selected)
        dimensions["time_compliance"] = all(row["filed"] and row["filed"] < req["cutoff"] for row in selected)
    else:
        period = expected["period"]
        instant_only = all(operand["metric_id"] in gold["instant_metrics"] for operand in expected["operands"])
        expected_start = None if instant_only else period["start"]
        dimensions["period_alignment"] = metric.get("period_start") == expected_start and metric.get("period_end") == period["end"]
        try:
            value_ok = Fraction(str(metric.get("value"))) == Fraction(expected["value"])
        except (ValueError, ZeroDivisionError):
            value_ok = False
        expected_unit = expected["unit"]
        unit_ok = metric.get("unit") == expected_unit or expected_unit == "ratio" and metric.get("unit") == "x"
        dimensions["value_and_unit"] = value_ok and unit_ok
        dimensions["source_integrity"] = bool(refs) and len(selected) == len(refs) and all(evidence_in_raw(row, raw) for row in selected)
        dimensions["time_compliance"] = bool(selected) and all(row["filed"] and row["filed"] < req["cutoff"] for row in selected)
        def matches(operand, candidate):
            row = operand["row"]
            # Equivalent allowed revenue tags are accepted; the exact accession is
            # not prescribed when an equally valid annual row proves this value.
            return (candidate["ticker"] == operand["ticker"]
                    and candidate["tag"] in gold["tags"][operand["metric_id"]]
                    and candidate["unit"] == operand["unit"]
                    and candidate["value"] == str(row["val"])
                    and candidate["end"] == row["end"]
                    and (operand["metric_id"] in gold["instant_metrics"] or candidate["start"] == row["start"]))
        dimensions["complete_operands"] = all(any(matches(operand, candidate) for candidate in selected) for operand in expected["operands"])
        dimensions["no_wrong_period_operands"] = bool(selected) and all(
            candidate["ticker"] == req["ticker"] and candidate["end"] == period["end"]
            and (candidate["start"] is None or candidate["start"] == period["start"])
            for candidate in selected)
    return {"id": case["id"], "split": case["split"], "category": case["category"], "request": req,
            "expected_status": expected["status"], "actual_status": metric.get("status"),
            "expected_value": expected.get("value"), "actual_value": metric.get("value"),
            "strict_pass": all(dimensions.values()), "dimensions": dimensions,
            "failures": [name for name, passed in dimensions.items() if not passed], "output": output}


def summarize(rows):
    dimensions = {}
    for row in rows:
        for name, passed in row["dimensions"].items():
            counts = dimensions.setdefault(name, {"passed": 0, "total": 0})
            counts["passed"] += int(passed)
            counts["total"] += 1
    numeric = [row for row in rows if row["expected_status"] == "available"]
    abstentions = [row for row in rows if row["expected_status"] != "available"]
    return {"cases": len(rows), "strict_passed": sum(row["strict_pass"] for row in rows),
            "strict_pass_pct": round(100 * sum(row["strict_pass"] for row in rows) / len(rows), 2) if rows else 0,
            "numeric_correct": sum(row["dimensions"].get("value_and_unit", False) for row in numeric),
            "numeric_total": len(numeric), "correct_abstentions": sum(row["strict_pass"] for row in abstentions),
            "abstention_total": len(abstentions), "atomic_passed": sum(v["passed"] for v in dimensions.values()),
            "atomic_total": sum(v["total"] for v in dimensions.values()), "dimensions": dimensions,
            "expected_statuses": dict(Counter(row["expected_status"] for row in rows))}


WORKER = r'''
import json, pathlib, socket, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, sys.argv[1])
network_attempts=[]
def deny(*a, **k):
    network_attempts.append("blocked")
    raise RuntimeError("Terminal evaluation forbids network/model calls")
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.create_connection=deny
from research_workbench.terminal_data import load_cube, query_metric
cube=load_cube()
cases=json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
result=[]
for case in cases:
    req=case["request"]
    try:
        metric=query_metric(cube, **req)
        ids=metric.get("evidence_ids",[])
        result.append({"id":case["id"],"metric":metric,"evidence":{i:cube["evidence"][i] for i in ids if i in cube["evidence"]}})
    except Exception as exc:
        result.append({"id":case["id"],"metric":{},"evidence":{},"error":type(exc).__name__+": "+str(exc)})
print(json.dumps({"outputs":result,"network_attempts":len(network_attempts)},ensure_ascii=False))
'''


def code_fingerprint():
    paths = [p for p in (ROOT / "research_workbench").glob("*.py")]
    paths += [ROOT / "site/workbench/data/finance_cube.json"]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path.read_bytes()) for path in paths if path.exists()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="development")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="development")
    parser.add_argument("--require-no-regression", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Evaluation receipt exists; choose a new path to preserve previous results.")
    manifest, gold, raw, cases, audit = audit_fixtures()
    selected = [case for case in cases if args.split == "all" or case["split"] == args.split]
    with tempfile.TemporaryDirectory(prefix="finagent-terminal-eval-") as tmp:
        requests = Path(tmp) / "requests.json"
        requests.write_text(json.dumps([{"id": c["id"], "request": c["request"]} for c in selected]), encoding="utf-8")
        process = subprocess.run([sys.executable, "-c", WORKER, str(ROOT), str(requests)], capture_output=True, text=True, encoding="utf-8", timeout=120)
        if process.returncode:
            raise SystemExit(process.stderr)
        actual = json.loads(process.stdout)
    rows = [score_case(case, output, gold, raw) for case, output in zip(selected, actual["outputs"])]
    assert len(rows) == len(selected)
    result = {"suite": manifest["suite"], "label": args.label, "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "dataset_sha256": manifest["dataset_sha256"], "dataset_frozen_at": manifest["frozen_at"], "split": args.split,
              "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "code_sha256": code_fingerprint(), "scope": manifest["scope"], "model_calls": 0,
              "network_attempts": actual["network_attempts"], "audit": audit, "summary": summarize(rows),
              "by_split": {split: summarize([row for row in rows if row["split"] == split]) for split in sorted({row["split"] for row in rows})}, "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"label": args.label, "summary": result["summary"], "failures": [{"id": row["id"], "failures": row["failures"]} for row in rows if not row["strict_pass"]]}, indent=2))
    if args.require_no_regression:
        baseline = read(args.require_no_regression)
        prior = {row["id"]: row for row in baseline["results"]}
        regressions = [row["id"] for row in rows if prior.get(row["id"], {}).get("strict_pass") and not row["strict_pass"]]
        if regressions:
            raise SystemExit(f"Terminal regressions: {regressions}")


if __name__ == "__main__":
    main()
