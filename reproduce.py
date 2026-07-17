"""Reproduce and validate the published FinSearchComp audit in one command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit.run_demo import write_outputs
from audit.validate_outputs import validate_output_dir, validate_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).parent / "audit" / "sample_runs.json",
        help="Recorded audit-run JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "site",
        help="Directory for generated report artifacts",
    )
    parser.add_argument(
        "--no-strict-demo",
        action="store_true",
        help="Allow a custom number of success/failure runs",
    )
    args = parser.parse_args()
    strict_demo = not args.no_strict_demo

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    summary = validate_payload(payload, strict_demo=strict_demo)
    print(
        f"[1/3] Validated {summary['runs']} recorded runs "
        f"({summary['success']} success, {summary['failure']} failure)"
    )

    write_outputs(args.input, args.output, announce=False)
    print(f"[2/3] Generated 4 report artifacts in {args.output}")

    validate_output_dir(args.output, payload, strict_demo=strict_demo)
    print("[3/3] Reproducibility checks passed")


if __name__ == "__main__":
    main()
