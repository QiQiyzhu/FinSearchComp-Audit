"""Verify frozen data, first-code provenance and public score synchronization."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from evaluate_terminal import audit_fixtures

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    manifest, _, _, _, audit = audit_fixtures()
    nlq_manifest = read(ROOT / "evals/terminal/nlq_manifest.json")
    for name, digest in nlq_manifest["files"].items():
        assert sha((ROOT / "evals/terminal" / name).read_bytes()) == digest, name
    quality = read(ROOT / "site/terminal/data/quality.json")
    names = {
        "structured_first": "terminal-structured-first.json",
        "nlq_first": "terminal-nlq-first.json",
        "structured_regression": "terminal-structured-regression.json",
        "nlq_regression": "terminal-nlq-regression.json",
    }
    synchronized = []
    for key, name in names.items():
        public = quality.get(key)
        if public is None:
            continue
        receipt = read(ROOT / "docs/verification" / name)
        for field in ["summary", "by_split", "dataset_sha256", "code_revision", "evaluated_at", "scope"]:
            assert public[field] == receipt[field], f"Public score drift: {key}.{field}"
        assert receipt["model_calls"] == 0
        synchronized.append(key)
    first = read(ROOT / "docs/verification/terminal-structured-first.json")
    nlq = read(ROOT / "docs/verification/terminal-nlq-first.json")
    assert first["dataset_sha256"] == manifest["dataset_sha256"]
    assert nlq["dataset_sha256"] == nlq_manifest["dataset_sha256"]
    assert first["code_revision"] == nlq["code_revision"]
    critical = ["research_workbench/terminal_data.py", "research_workbench/sources.py", "research_workbench/config.py", "site/workbench/data/finance_cube.json"]
    critical_hashes = {}
    for path in critical:
        blob = subprocess.check_output(["git", "show", f"{first['code_revision']}:{path}"], cwd=ROOT)
        actual = sha(blob)
        assert actual == first["code_sha256"][path], f"First kernel hash mismatch: {path}"
        critical_hashes[path] = actual
    path = "site/terminal/query.mjs"
    blob = subprocess.check_output(["git", "show", f"{nlq['code_revision']}:{path}"], cwd=ROOT)
    assert sha(blob) == nlq["code_sha256"][path]
    critical_hashes[path] = sha(blob)
    replay = read(ROOT / "docs/verification/terminal-first-code-replay.json")
    assert replay["replayed_revision"] == first["code_revision"]
    assert all(check["same_summary_and_every_case"] and check["model_calls"] == 0 for check in replay["checks"])
    print(json.dumps({"verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "fixture_audit": audit, "public_scores_synchronized": synchronized,
                      "first_critical_files_match_git": list(critical_hashes),
                      "first_replay_cases": sum(check["cases"] for check in replay["checks"])}, indent=2))


if __name__ == "__main__":
    main()
