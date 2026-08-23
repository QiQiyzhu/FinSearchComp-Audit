"""Reproduce and validate the published FinSearchComp audit in one command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit.run_demo import write_outputs
from audit.validate_outputs import validate_output_dir, validate_payload
from advanced_rag.evaluate import evaluate as evaluate_advanced_rag
from advanced_rag.evaluate_agentic import evaluate as evaluate_agentic
from finagent_platform.demo import generate_demo as generate_platform_demo
from game_qa_agent.demo import run_demo as run_game_qa_demo
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
        f"[1/8] Validated {summary['runs']} recorded runs "
        f"({summary['success']} success, {summary['failure']} failure)"
    )

    write_outputs(args.input, args.output, announce=False)
    print(f"[2/8] Generated core audit artifacts in {args.output}")

    run_temporal_experiment(check=True, site_dir=args.output)
    print("[3/8] Temporal detector benchmark and report generated")

    advanced_output = args.output / "advanced-rag"
    advanced_result = evaluate_advanced_rag(advanced_output)
    print(
        "[4/8] ATLAS-RAG evaluated: "
        f"MRR@10={advanced_result['retrieval']['temporal']['mrr_at_10']:.3f}, "
        f"selective_accuracy={advanced_result['system']['selective_accuracy']:.1%}"
    )

    game_result = run_game_qa_demo(args.output / "game-qa")
    print(
        "[5/8] Match-3 Agent QA verified: "
        f"{game_result['initial_report']['analysis']['legal_move_count']} legal moves, "
        f"{game_result['applied_move']['cascades']} cascades, deterministic replay"
    )

    platform_result = generate_platform_demo(args.output / "platform")
    print(
        "[6/8] FinAgent platform verified: "
        f"{platform_result['evaluation']['result']['total']} async cases, "
        "timeout recovery, idempotency, failure analytics, replay"
    )

    agentic_result = evaluate_agentic(
        Path(__file__).parent / "advanced_rag" / "results" / "agentic",
        args.output / "agentic-eval",
    )
    planner = next(
        row
        for row in agentic_result["e4_gap_planner"]
        if row["method"] == "structured_gap_planner"
    )
    gate = next(
        row
        for row in agentic_result["e5_sufficiency_gate"]
        if row["method"] == "sufficiency_gate"
    )
    print(
        "[7/8] Agentic E4-E6 evaluated: "
        f"planner_accuracy={planner['answer_accuracy']:.1%}, "
        f"correct_abstention={gate['correct_abstention_rate']:.1%}"
    )

    validate_output_dir(args.output, payload, strict_demo=strict_demo)
    print("[8/8] All reproducibility and site-link checks passed")


if __name__ == "__main__":
    main()
