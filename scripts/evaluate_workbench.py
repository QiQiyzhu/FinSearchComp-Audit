"""Offline, independently scored SEC question evaluation; no product imports in scorer.

Use --split development while implementing. Heldout results must be retained rather
than overwritten. Engine code executes in isolated subprocesses with networking denied.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from fractions import Fraction
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/workbench"
DERIVED = {
    "operating_margin": ("operating_income", "revenue"),
    "net_margin": ("net_income", "revenue"),
    "rd_ratio": ("research_and_development", "revenue"),
    "cash_conversion": ("operating_cash_flow", "net_income"),
    "free_cash_flow": ("operating_cash_flow", "capital_expenditure"),
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rounded_fraction(value):
    scaled = abs(value) * 100
    integer = (scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator)
    return Fraction(integer if value >= 0 else -integer, 100)


def independent_value(metric, values):
    if metric not in DERIVED:
        return Fraction(values[metric])
    a, b = [Fraction(values[key]) for key in DERIVED[metric]]
    return a - b if metric == "free_cash_flow" else a / b * 100


def audit_fixtures():
    """Literal gold must agree with original rows and independent rational arithmetic."""
    manifest = read_json(FIXTURES / "manifest.json")
    for name, expected in manifest["files"].items():
        assert sha((FIXTURES / name).read_bytes()) == expected, f"Frozen fixture changed: {name}"
    dataset_hash = sha(json.dumps(manifest["files"], sort_keys=True).encode())
    assert dataset_hash == manifest["dataset_sha256"], "Dataset hash mismatch"
    gold = read_json(FIXTURES / "gold_facts.json")
    raw = {}
    audited_values = 0
    for ticker, company in gold["companies"].items():
        original = gzip.decompress((ROOT / f"research_workbench/data/upstream/{ticker}.json.gz").read_bytes())
        assert sha(original) == manifest["upstream_sha256"][ticker], f"Original capture changed: {ticker}"
        raw[ticker] = json.loads(original)
        assert raw[ticker]["cik"] == company["cik"]
        for year, period in company["years"].items():
            for metric, value in period["values"].items():
                rows = [row for tag in gold["tags"][metric]
                        for row in raw[ticker]["facts"]["us-gaap"].get(tag, {}).get("units", {}).get("USD", [])
                        if row.get("start") == period["start"] and row.get("end") == period["end"]
                        and row.get("form") in {"10-K", "10-K/A"} and row.get("filed", "9999") <= "2024-11-01"
                        and str(row["val"]) == value]
                assert rows, f"Gold absent from original SEC rows: {ticker} {year} {metric}"
                assert min(row["filed"] for row in rows) == period["first_filed"], f"First filing mismatch: {ticker} {year} {metric}"
                audited_values += 1
    cases = read_json(FIXTURES / "cases.json")["cases"]
    for case in cases:
        for expected in case["values"]:
            years = gold["companies"][case["ticker"]]["years"]
            current = independent_value(expected["metric"], years[str(expected["year"])]["values"])
            if expected["operation"] != "value":
                prior = independent_value(expected["metric"], years[str(expected["prior_year"])]["values"])
                current = (current / prior - 1) * 100 if expected["operation"] == "growth_pct" else current - prior
            if expected["unit"] != "USD":
                current = rounded_fraction(current)
            assert current == Fraction(expected["value"]), f"Frozen arithmetic gold mismatch: {case['id']}"
    return manifest, gold, raw, cases, {"literal_raw_values_checked": audited_values, "frozen_questions": len(cases), "independent_arithmetic": "fractions.Fraction; half-up to 2 decimal places"}


WORKER = r'''
import json, pathlib, socket, sys
sys.stdout.reconfigure(encoding="utf-8")
implementation, requests_path = sys.argv[1:]
sys.path.insert(0, implementation)
network_attempts = []
def deny(*args, **kwargs):
    network_attempts.append("blocked")
    raise RuntimeError("Evaluation forbids network and model calls")
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
from research_workbench.config import Settings
from research_workbench.engine import ResearchEngine
engine = ResearchEngine(Settings())
results = []
for case in json.loads(pathlib.Path(requests_path).read_text(encoding="utf-8")):
    try:
        report = engine.run({key: case[key] for key in ("question", "ticker", "as_of")} | {"mode": "demo"})
        results.append({"id": case["id"], "report": report})
    except Exception as exc:
        results.append({"id": case["id"], "error": type(exc).__name__ + ": " + str(exc)})
print(json.dumps({"results": results, "network_attempts": len(network_attempts)}, ensure_ascii=False))
'''


def run_engine(implementation, cases):
    with tempfile.TemporaryDirectory(prefix="finagent-eval-") as temporary:
        requests_path = Path(temporary) / "requests.json"
        # Worker receives only requests, never expected answers or raw audit facts.
        write_json(requests_path, [{key: case[key] for key in ("id", "question", "ticker", "as_of")} for case in cases])
        env = {key: value for key, value in os.environ.items() if not any(word in key.upper() for word in ("DEEPSEEK", "TAVILY", "FINAGENT", "OPENAI", "SEC_USER_AGENT"))}
        result = subprocess.run([sys.executable, "-c", WORKER, str(implementation), str(requests_path)],
                                cwd=implementation, env=env, capture_output=True, encoding="utf-8", timeout=180)
        if result.returncode:
            raise RuntimeError("Worker failed: " + result.stderr[-2000:])
        payload = json.loads(result.stdout)
        assert payload["network_attempts"] == 0, "Network attempted in offline evaluation"
        assert not any(row.get("report", {}).get("model", {}).get("used") for row in payload["results"]), "Model call detected"
        return {row["id"]: row for row in payload["results"]}


def year_for(gold, ticker, start, end):
    for year, period in gold["companies"][ticker]["years"].items():
        if (period["start"], period["end"]) == (start, end):
            return int(year)
    return None


def audit_evidence(report, case, gold, raw, manifest):
    errors, known = [], {}
    ticker, cutoff = case["ticker"], case["as_of"]
    for item in report.get("evidence", []):
        eid = item.get("id")
        if not eid or eid in known:
            errors.append("duplicate_or_missing_evidence_id")
        known[eid] = item
        if not (item.get("period_start", "9999") < item.get("period_end", "") <= item.get("published_at", "") <= cutoff):
            errors.append(f"{eid}: temporal_cutoff")
        try:
            duration = (date.fromisoformat(item["period_end"]) - date.fromisoformat(item["period_start"])).days
            if not 330 <= duration <= 380:
                errors.append(f"{eid}: nonannual_period")
        except (KeyError, ValueError):
            errors.append(f"{eid}: invalid_period")
        if item.get("upstream_sha256") != manifest["upstream_sha256"][ticker]:
            errors.append(f"{eid}: original_capture_hash")
        tag = item.get("taxonomy_tag")
        if tag not in gold["tags"].get(item.get("metric"), []):
            errors.append(f"{eid}: metric_definition")
        rows = raw[ticker]["facts"]["us-gaap"].get(tag, {}).get("units", {}).get(item.get("unit"), [])
        matching = [row for row in rows if (row.get("start"), row.get("end"), row.get("filed"), row.get("accn"), str(row.get("val"))) ==
                    (item.get("period_start"), item.get("period_end"), item.get("published_at"), item.get("accession"), item.get("value"))
                    and row.get("form") in {"10-K", "10-K/A"}]
        if not matching:
            errors.append(f"{eid}: not_an_original_sec_row")
        cik = gold["companies"][ticker]["cik"]
        if item.get("source_url") != f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json":
            errors.append(f"{eid}: original_source_url")
        accession = item.get("accession", "")
        expected_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm"
        if item.get("url") != expected_url:
            errors.append(f"{eid}: filing_link")
    for claim in report.get("claims", []) + report.get("answers", []):
        if any(ref not in known for ref in claim.get("evidence_ids", [])):
            errors.append("unknown_citation")
    return list(dict.fromkeys(errors)), known


def normalize(report, gold, ticker):
    if "answers" in report:
        return [{"metric": row.get("metric_id"), "operation": row.get("operation"), "year": row.get("fiscal_year"),
                 "prior_year": row.get("comparison_fiscal_year"), "value": row.get("value"), "unit": row.get("unit"),
                 "evidence_ids": row.get("evidence_ids", []), "presented": bool(row.get("text")),
                 "period_start": row.get("period_start"), "period_end": row.get("period_end"),
                 "prior_start": row.get("comparison_period_start"), "prior_end": row.get("comparison_period_end")}
                for row in report["answers"] if row.get("answerability") == "answered"]
    normalized = []
    for row in report.get("metrics", []):
        year = year_for(gold, ticker, row.get("period_start"), row.get("period_end"))
        previous = next((int(y) for y, p in gold["companies"][ticker]["years"].items() if p["end"] == row.get("comparison_period_end")), None)
        for operation, field, unit in [("value", "value", row.get("unit")), ("growth_pct", "change_pct", "%")]:
            if field not in row:
                continue
            kind = "derived" if operation != "value" or row["id"] in DERIVED else "verified"
            presented = any(claim.get("metric_id") == row["id"] and claim.get("kind") == kind
                            and claim.get("text") and claim["text"] in report.get("summary", "")
                            for claim in report.get("claims", []))
            prior_period = gold["companies"][ticker]["years"].get(str(previous), {})
            normalized.append({"metric": row["id"], "operation": operation, "year": year,
                               "prior_year": previous if operation != "value" else None,
                               "value": row[field], "unit": unit, "evidence_ids": row.get("evidence_ids", []), "presented": presented,
                               "period_start": row.get("period_start"), "period_end": row.get("period_end"),
                               "prior_start": prior_period.get("start"), "prior_end": row.get("comparison_period_end")})
    return normalized


def score_case(case, output, gold, raw, manifest):
    base = {"id": case["id"], "split": case["split"], "category": case["category"], "expected": case["expect"],
            "requested_values": len(case["values"]), "correct_requested_values": 0, "present_requested_values": 0,
            "false_refusal": False, "correct_abstention": False, "evidence_valid": False, "fulfilled": False, "failures": []}
    if "error" in output:
        base["failures"] = ["engine_error: " + output["error"]]
        return base
    report = output["report"]
    evidence_errors, known = audit_evidence(report, case, gold, raw, manifest)
    base["evidence_valid"] = not evidence_errors
    base["evidence_count"] = len(known)
    base["failures"].extend(evidence_errors)
    normalized = normalize(report, gold, case["ticker"])
    supported = report.get("coverage", {}).get("question_supported") is True
    if case["expect"] == "abstain":
        explicit = not supported and bool(report.get("plan", {}).get("gaps") or report.get("coverage", {}).get("missing_requested_metrics")
                                           or any(row.get("reason") for row in report.get("answers", []))
                                           or "未覆盖" in report.get("summary", "") or "无法回答" in report.get("summary", ""))
        forbidden = any(row["metric"] in case.get("forbidden_metrics", []) or [row["metric"], row["year"]] in case.get("forbidden_metric_years", []) for row in normalized)
        base["correct_abstention"] = explicit and not forbidden and not evidence_errors
        base["fulfilled"] = base["correct_abstention"]
        if not explicit:
            base["failures"].append("missing_explicit_abstention")
        if forbidden:
            base["failures"].append("forbidden_unavailable_answer")
        return base
    base["false_refusal"] = not supported
    if not supported:
        base["failures"].append("false_refusal_of_answerable_question")
    for expected in case["values"]:
        key = (expected["metric"], expected["operation"], expected["year"], expected.get("prior_year"))
        matches = [row for row in normalized if (row["metric"], row["operation"], row["year"], row.get("prior_year")) == key]
        label = ":".join(map(str, key[:3]))
        if not matches:
            base["failures"].append(label + ": requested_answer_missing")
            continue
        base["present_requested_values"] += 1
        row = matches[0]
        correct = row["unit"] == expected["unit"] and row["value"] is not None and Fraction(str(row["value"])) == Fraction(expected["value"])
        if correct:
            base["correct_requested_values"] += 1
        else:
            base["failures"].append(label + ": wrong_value_or_unit")
        if not row["presented"]:
            base["failures"].append(label + ": omitted_from_answer_surface")
        years = [expected["year"]] + ([expected["prior_year"]] if expected.get("prior_year") else [])
        required = {(metric, year) for metric in DERIVED.get(expected["metric"], [expected["metric"]]) for year in years}
        cited = {(known[ref].get("metric"), year_for(gold, case["ticker"], known[ref].get("period_start"), known[ref].get("period_end")))
                 for ref in row["evidence_ids"] if ref in known}
        if not required <= cited:
            base["failures"].append(label + ": missing_operand_evidence")
        period = gold["companies"][case["ticker"]]["years"][str(expected["year"])]
        if (row["period_start"], row["period_end"]) != (period["start"], period["end"]):
            base["failures"].append(label + ": incorrect_answer_period")
        if expected.get("prior_year"):
            prior = gold["companies"][case["ticker"]]["years"][str(expected["prior_year"])]
            if (row.get("prior_start"), row.get("prior_end")) != (prior["start"], prior["end"]):
                base["failures"].append(label + ": incorrect_comparison_period")
    base["fulfilled"] = not base["failures"]
    return base


def aggregate(rows):
    total = len(rows)
    result = {"cases": total, "fulfilled": sum(row["fulfilled"] for row in rows),
              "requested_values": sum(row["requested_values"] for row in rows),
              "correct_requested_values": sum(row["correct_requested_values"] for row in rows),
              "present_requested_values": sum(row["present_requested_values"] for row in rows),
              "answerable_cases": sum(row["expected"] == "answer" for row in rows),
              "false_refusals": sum(row["false_refusal"] for row in rows),
              "abstain_cases": sum(row["expected"] == "abstain" for row in rows),
              "correct_abstentions": sum(row["correct_abstention"] for row in rows),
              "evidence_valid_cases": sum(row["evidence_valid"] for row in rows)}
    for field, numerator, denominator in [("fulfilled_rate", "fulfilled", "cases"), ("numeric_accuracy", "correct_requested_values", "requested_values"),
                                          ("numeric_precision", "correct_requested_values", "present_requested_values")]:
        result[field] = result[numerator] / result[denominator] if result[denominator] else None
    return result


def revision(path):
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True)
    changed = subprocess.run(["git", "status", "--porcelain", "--", "research_workbench"], cwd=path, capture_output=True, text=True, check=True)
    return result.stdout.strip() + ("+working-tree" if changed.stdout.strip() else "")


def implementation_hash(path):
    digest = hashlib.sha256()
    for file in sorted((path / "research_workbench").glob("*.py")):
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="development")
    parser.add_argument("--baseline", type=Path, default=ROOT / "build/baseline-v1")
    parser.add_argument("--current-ref", help="Evaluate an exact Git commit in an isolated ignored extraction instead of the working tree")
    parser.add_argument("--output", type=Path, default=ROOT / "build/quality/evaluation.json")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--require-current-perfect", action="store_true", help="CI regression gate, not a replacement for published first heldout scores")
    parser.add_argument("--require-no-regression", type=Path, help="Preserve every fulfilled case and aggregate requested-value count from a published receipt")
    args = parser.parse_args()
    if args.output.exists() and args.split != "development":
        parser.error("Heldout outputs are append-only: use a new output filename; preserve the first attempt.")
    if args.summary and args.summary.exists():
        parser.error("Public summary already exists; retain the published first-attempt score. Use a new regression summary path.")
    manifest, gold, raw, cases, audit = audit_fixtures()
    selected = [case for case in cases if args.split == "all" or case["split"] == args.split]
    baseline = args.baseline.resolve()
    archive = subprocess.run(["git", "archive", "--format=zip", manifest["baseline_revision"]], cwd=ROOT, capture_output=True, check=True).stdout
    if not (baseline / "research_workbench/engine.py").exists():
        baseline.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as contents:
            contents.extractall(baseline)
    with zipfile.ZipFile(io.BytesIO(archive)) as contents:
        expected_paths = [name for name in contents.namelist() if name.startswith("research_workbench/") and (name.endswith(".py") or name.endswith("demo_companyfacts.json"))]
        for name in expected_paths:
            assert (baseline / name).read_bytes() == contents.read(name), f"Baseline modified from {manifest['baseline_revision']}: {name}"
    current_path, current_revision = ROOT, None
    if args.current_ref:
        current_revision = subprocess.run(["git", "rev-parse", "--verify", args.current_ref + "^{commit}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        current_path = ROOT / "build/quality-refs" / current_revision
        current_archive = subprocess.run(["git", "archive", "--format=zip", current_revision], cwd=ROOT, capture_output=True, check=True).stdout
        current_path.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(current_archive)) as contents:
            if not (current_path / "research_workbench/engine.py").exists():
                contents.extractall(current_path)
            for name in contents.namelist():
                if name.startswith("research_workbench/") and (name.endswith(".py") or name.endswith("demo_companyfacts.json")):
                    assert (current_path / name).read_bytes() == contents.read(name), f"Extracted current ref was modified: {name}"
    # Fail rather than silently comparing a changed data universe.
    # Git archive / checkout can apply different CRLF conventions on Windows.
    # Only normalize line endings; every other byte must remain identical.
    baseline_data = (baseline / "research_workbench/data/demo_companyfacts.json").read_bytes().replace(b"\r\n", b"\n")
    current_data = (current_path / "research_workbench/data/demo_companyfacts.json").read_bytes().replace(b"\r\n", b"\n")
    assert baseline_data == current_data, "Bundled dataset differs between implementations"
    results = {"schema_version": 1, "suite": manifest["suite"], "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "split": args.split, "dataset_sha256": manifest["dataset_sha256"], "model_calls": 0, "network_attempts": 0,
               "scope": "单公司年度财务问题，SEC 冻结样本；不衡量开放研究能力或投资收益。", "fixture_audit": audit,
               "evaluator_sha256": sha(Path(__file__).read_bytes()),
               "baseline": {}, "current": {}}
    for name, path in [("baseline", baseline), ("current", current_path)]:
        before = implementation_hash(path)
        outputs = run_engine(path, selected)
        assert before == implementation_hash(path), "Implementation changed while evaluation was running"
        rows = [score_case(case, outputs[case["id"]], gold, raw, manifest) for case in selected]
        results[name] = {"revision": manifest["baseline_revision"] if name == "baseline" else current_revision or revision(ROOT),
                         "implementation_sha256": before, "overall": aggregate(rows),
                         "source_files_sha256": {file.name: sha(file.read_bytes()) for file in sorted((path / "research_workbench").glob("*.py"))},
                         "development": aggregate([row for row in rows if row["split"] == "development"]),
                         "heldout": aggregate([row for row in rows if row["split"] == "heldout"]), "cases": rows}
        write_json(ROOT / f"build/quality/{args.output.stem}-{name}-reports.json", outputs)
    results["limitations"] = [
        "40 题由项目评估者编写，含 20 道开发题和 20 道实现者未见的保留题；不是外部金融基准。",
        "原生快照只含三家公司 FY2023/FY2024 的常用年度标签；本轨道中缺失的 FY2022 应明确保留判断。",
        "后来抓取的 SEC Company Facts 按 filed 日期过滤，不是当时完整归档的数据库；时间审计为日粒度。",
        "模型调用为零；不测 DeepSeek 文本选择、网页检索质量、实时市场判断或投资收益。",
        "数值分母只计题目要求的值，缺答案计错；完整问题还要求期间、所需原始证据、回答呈现与正确拒答。",
    ]
    write_json(args.output, results)
    if args.summary:
        assert args.split == "all", "Public summary requires both development and heldout results"
        summary = {key: value for key, value in results.items() if key not in {"fixture_audit", "split"}}
        for name in ["baseline", "current"]:
            summary[name] = {key: value for key, value in results[name].items() if key != "cases"}
        summary["details_url"] = "https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/WORKBENCH_QUALITY.md"
        write_json(args.summary, summary)
    print(json.dumps({name: results[name]["overall"] for name in ("baseline", "current")}, ensure_ascii=False, indent=2))
    if args.require_current_perfect and results["current"]["overall"]["fulfilled"] != len(selected):
        raise SystemExit(1)
    if args.require_no_regression:
        reference = read_json(args.require_no_regression)
        assert reference["dataset_sha256"] == results["dataset_sha256"], "Regression gate dataset mismatch"
        current_rows = {row["id"]: row for row in results["current"]["cases"]}
        required = [row for row in reference["current"]["cases"] if row["id"] in current_rows]
        regressions = [row["id"] for row in required if row["fulfilled"] and not current_rows[row["id"]]["fulfilled"]]
        assert not regressions, "Previously fulfilled questions regressed: " + ", ".join(regressions)
        assert all(row["evidence_valid"] for row in current_rows.values()), "Original evidence or time-boundary regression"
        assert sum(row["correct_requested_values"] for row in current_rows.values()) >= sum(row["correct_requested_values"] for row in required), "Requested-value accuracy regression"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
