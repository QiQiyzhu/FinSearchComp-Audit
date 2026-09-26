"""Replay immutable first-evaluation code from its Git archive, without checkout."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Replay receipt exists; preserve it and choose another output.")
    names = [("terminal-structured-first.json", "evaluate_terminal.py"), ("terminal-nlq-first.json", "evaluate_terminal_nlq.py")]
    originals = [(name, script, read(ROOT / "docs/verification" / name)) for name, script in names]
    revisions = {original["code_revision"] for _, _, original in originals}
    assert len(revisions) == 1, "First receipt revisions differ"
    revision = next(iter(revisions))
    archive = subprocess.check_output(["git", "archive", "--format=zip", revision], cwd=ROOT)
    git_directory = subprocess.check_output(["git", "rev-parse", "--absolute-git-dir"], cwd=ROOT, text=True).strip()
    checks = []
    with tempfile.TemporaryDirectory(prefix="finagent-first-replay-") as temporary:
        temporary = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            zipped.extractall(temporary)
        for name, script, original in originals:
            output = temporary / "build" / name
            # The old scorer records repository metadata. Its executable files
            # still come exclusively from the archived first revision.
            environment = dict(os.environ, PYTHONIOENCODING="utf-8", GIT_DIR=git_directory)
            process = subprocess.run([sys.executable, str(temporary / "scripts" / script), "--split", "all", "--label", "first-code-replay", "--output", str(output)], cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=180)
            if process.returncode:
                raise SystemExit(process.stderr + process.stdout)
            replay = read(output)
            # Timestamp/Git cwd are execution metadata, never graded outputs.
            same = all(replay[key] == original[key] for key in ["dataset_sha256", "summary", "by_split", "results"])
            checks.append({"receipt": name, "same_summary_and_every_case": same, "cases": len(replay["results"]), "model_calls": replay["model_calls"]})
            assert same, f"First evaluation is not reproducible: {name}"
    receipt = {"verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "replayed_revision": revision,
               "method": "git archive into temporary directory; original frozen scorer and kernel/parser; no worktree checkout or model calls",
               "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
