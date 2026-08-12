"""Reproduce and validate the published FinSearchComp audit in one command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit.run_demo import write_outputs
from audit.validate_outputs import validate_output_dir, validate_payload
from advanced_rag.evaluate import evaluate as evaluate_advanced_rag
from temporal_clash.run_experiment import run as run_temporal_experiment


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
        f"[1/5] Validated {summary['runs']} recorded runs "
        f"({summary['success']} success, {summary['failure']} failure)"
    )

    write_outputs(args.input, args.output, announce=False)
    print(f"[2/5] Generated core audit artifacts in {args.output}")

    run_temporal_experiment(check=True, site_dir=args.output)
    print("[3/5] Temporal detector benchmark and report generated")

    advanced_output = args.output / "advanced-rag"
    advanced_result = evaluate_advanced_rag(advanced_output)
    print(
        "[4/5] ATLAS-RAG evaluated: "
        f"MRR@10={advanced_result['retrieval']['temporal']['mrr_at_10']:.3f}, "
        f"selective_accuracy={advanced_result['system']['selective_accuracy']:.1%}"
    )

    validate_output_dir(args.output, payload, strict_demo=strict_demo)
    print("[5/5] All reproducibility and site-link checks passed")


if __name__ == "__main__":
    main()
