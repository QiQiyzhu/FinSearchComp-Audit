from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from .evaluate_baselines import SUMMARY_CSV, SUMMARY_MD, evaluate
from .generate_dataset import CONDITIONS, DATASET_PATH, generate


def validate(cases: list[dict]) -> None:
    if len(cases) != 100:
        raise AssertionError(f"Expected 100 cases, got {len(cases)}")
    base_ids = {case["base_question_id"] for case in cases}
    if len(base_ids) != 20:
        raise AssertionError(f"Expected 20 base questions, got {len(base_ids)}")
    counts = Counter(case["evidence_condition"] for case in cases)
    expected = {condition: 20 for condition in CONDITIONS}
    if counts != expected:
        raise AssertionError(f"Unexpected condition counts: {counts}")
    if len({case["case_id"] for case in cases}) != len(cases):
        raise AssertionError("case_id values must be unique")

    for case in cases:
        if case["evidence_condition"] == "future_only":
            if case["expected_action"] != "abstain":
                raise AssertionError("future_only cases must require abstention")
        elif case["expected_action"] != "answer":
            raise AssertionError("non-future cases must be answerable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate dataset cardinality and deterministic policy invariants.",
    )
    args = parser.parse_args()

    cases = generate()
    validate(cases)
    summaries = evaluate(cases)

    if args.check:
        expected_decision = {
            "plain_agent": 0.20,
            "temporal_prompt": 0.40,
            "metadata_filter": 0.80,
            "teg_validator": 1.00,
        }
        for method, expected in expected_decision.items():
            actual = summaries[method]["decision_accuracy"]
            if abs(actual - expected) > 1e-9:
                raise AssertionError(
                    f"{method} decision accuracy changed: {actual} != {expected}"
                )

    print(f"Validated {len(cases)} controlled cases")
    print(f"Dataset: {Path(DATASET_PATH)}")
    print(f"Table:   {Path(SUMMARY_CSV)}")
    print(f"Report:  {Path(SUMMARY_MD)}")


if __name__ == "__main__":
    main()
