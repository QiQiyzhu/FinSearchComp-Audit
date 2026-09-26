"""Frozen intent-only evaluation; independent of financial value accuracy."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/terminal"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score(case, actual):
    expected = case["expected"]
    checks = {"supported": actual.get("supported") == expected["supported"]}
    if expected["supported"]:
        checks.update(metric_ids=set(actual.get("metric_ids", [])) == set(expected["metric_ids"]),
                      fiscal_year=actual.get("fiscal_year") == expected["fiscal_year"],
                      operation=actual.get("operation") == expected["operation"])
    else:
        checks["explains_gap"] = isinstance(actual.get("gaps"), list) and bool(actual["gaps"])
    return {"id": case["id"], "split": case["split"], "question": case["question"], "expected": expected,
            "actual": actual, "checks": checks, "strict_pass": all(checks.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="development")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="development")
    parser.add_argument("--require-no-regression", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Preserve earlier result; choose a new output path.")
    manifest = read(FIXTURES / "nlq_manifest.json")
    for name, value in manifest["files"].items():
        assert sha((FIXTURES / name).read_bytes()) == value, name
    cases = read(FIXTURES / "nlq_development.json")["cases"] + read(FIXTURES / "nlq_heldout.json")["cases"]
    cases = [case for case in cases if args.split == "all" or case["split"] == args.split]
    module = ROOT / "site/terminal/query.mjs"
    worker = """import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {parseQuestion}=await import(pathToFileURL(process.argv[1]));
const cases=JSON.parse(readFileSync(process.argv[2],'utf8'));
const outputs=cases.map(c=>{try{return parseQuestion(c.question,c.context)}catch(e){return {error:e.name+': '+e.message}}});
process.stdout.write(JSON.stringify(outputs));"""
    with tempfile.TemporaryDirectory(prefix="finagent-nlq-eval-") as temp:
        requests = Path(temp) / "requests.json"
        requests.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(["node", "--input-type=module", "-e", worker, str(module), str(requests)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        if proc.returncode:
            raise SystemExit(proc.stderr)
        outputs = json.loads(proc.stdout)
    rows = [score(case, actual) for case, actual in zip(cases, outputs)]
    def summary(group):
        return {"cases": len(group), "strict_passed": sum(row["strict_pass"] for row in group)}
    result = {"suite": manifest["suite"], "label": args.label, "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "dataset_sha256": manifest["dataset_sha256"], "scope": manifest["scope"], "split": args.split,
              "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "code_sha256": {str(module.relative_to(ROOT)).replace("\\", "/"): sha(module.read_bytes())},
              "model_calls": 0, "network_calls": 0, "summary": summary(rows),
              "by_split": {split: summary([row for row in rows if row["split"] == split]) for split in sorted({row["split"] for row in rows})}, "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"summary": result["summary"], "by_split": result["by_split"], "failures": [row for row in rows if not row["strict_pass"]]}, ensure_ascii=False, indent=2))
    if args.require_no_regression:
        old = {row["id"]: row for row in read(args.require_no_regression)["results"]}
        regressions = [row["id"] for row in rows if old.get(row["id"], {}).get("strict_pass") and not row["strict_pass"]]
        if regressions:
            raise SystemExit(f"NLQ intent regressions: {regressions}")


if __name__ == "__main__":
    main()
