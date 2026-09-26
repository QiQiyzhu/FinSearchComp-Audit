"""Post-exposure parity of the browser's pure selector and Python kernel.

Node executes the same ESM data module shipped to browsers. This checks selector
semantics, not browser DOM rendering or a new heldout accuracy experiment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from evaluate_terminal import ROOT, WORKER, audit_fixtures, score_case, sha, summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Parity receipt exists; preserve it and use a new output path.")
    manifest, gold, raw, cases, audit = audit_fixtures()
    module = ROOT / "site/terminal/data.mjs"
    cube_path = ROOT / "site/workbench/data/finance_cube.json"
    javascript = """import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {resolveMetric}=await import(pathToFileURL(process.argv[1]));
const cube=JSON.parse(readFileSync(process.argv[2],'utf8'));
const cases=JSON.parse(readFileSync(process.argv[3],'utf8'));
const outputs=cases.map(c=>{const r=c.request;const metric=resolveMetric(cube,r.ticker,r.cutoff,r.fiscal_year,r.metric_id);const evidence=Object.fromEntries((metric.evidence_ids??[]).filter(id=>cube.evidence[id]).map(id=>[id,cube.evidence[id]]));return {id:c.id,metric,evidence}});
process.stdout.write(JSON.stringify(outputs));"""
    with tempfile.TemporaryDirectory(prefix="finagent-parity-") as tmp:
        requests = Path(tmp) / "requests.json"
        requests.write_text(json.dumps([{"id": case["id"], "request": case["request"]} for case in cases]), encoding="utf-8")
        py = subprocess.run([sys.executable, "-c", WORKER, str(ROOT), str(requests)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        js = subprocess.run(["node", "--input-type=module", "-e", javascript, str(module), str(cube_path), str(requests)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if py.returncode or js.returncode:
            raise SystemExit(py.stderr + js.stderr)
        python_outputs = json.loads(py.stdout)["outputs"]
        browser_outputs = json.loads(js.stdout)
    fields = ["ticker", "metric_id", "fiscal_year", "status", "value", "unit", "period_start", "period_end", "evidence_ids"]
    parity, scored = [], []
    for case, python, browser in zip(cases, python_outputs, browser_outputs):
        checks = {field: python["metric"].get(field) == browser["metric"].get(field) for field in fields}
        checks["evidence_records"] = python["evidence"] == browser["evidence"]
        parity.append({"id": case["id"], "checks": checks, "passed": all(checks.values()),
                       "python_metric": python["metric"], "browser_module_metric": browser["metric"]})
        scored.append(score_case(case, browser, gold, raw))
    receipt = {"schema_version": 1, "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "type": "post_exposure_browser_module_kernel_parity", "runtime": "Node executes the exact pure ESM selector shipped to browsers; DOM rendering is tested separately",
               "dataset_sha256": manifest["dataset_sha256"], "code_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "code_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path.read_bytes()) for path in [module, cube_path, ROOT / "research_workbench/terminal_data.py"]},
               "model_calls": 0, "network_calls": 0, "cases": len(parity), "parity_passed": sum(row["passed"] for row in parity),
               "fields_compared": fields + ["evidence_records"], "browser_module_independent_score": summarize(scored),
               "fixture_audit": audit, "limitations": "These already-exposed cases verify implementation parity and regression only; this is not another heldout score or a browser UI test.",
               "parity": parity, "independent_results": scored}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: receipt[key] for key in ["cases", "parity_passed", "browser_module_independent_score"]}, indent=2))
    if receipt["parity_passed"] != len(parity) or not all(row["strict_pass"] for row in scored):
        raise SystemExit("Browser/Python parity or independent financial checks failed; inspect saved receipt.")


if __name__ == "__main__":
    main()
