from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from .evaluate_baselines import (
    DETECTOR_SUMMARY_CSV,
    SUMMARY_CSV,
    SUMMARY_MD,
    evaluate,
)
from .generate_dataset import CONDITIONS, DATASET_PATH, generate
from .report import write_temporal_site


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


def run(check: bool = False, site_dir: Path | None = None) -> dict:
    cases = generate()
    validate(cases)
    bundle = evaluate(cases)

    if check:
        expected_decision = {
            "plain_agent": 0.20,
            "temporal_prompt": 0.40,
            "metadata_filter": 0.80,
            "teg_validator": 1.00,
        }
        for method, expected in expected_decision.items():
            actual = bundle["selection"][method]["decision_accuracy"]
            if abs(actual - expected) > 1e-9:
                raise AssertionError(
                    f"{method} decision accuracy changed: {actual} != {expected}"
                )
        expected_detector_f1 = {
            "plain_agent": 0.00,
            "temporal_prompt": 0.40,
            "metadata_filter": 6 / 7,
            "teg_validator": 1.00,
        }
        for method, expected in expected_detector_f1.items():
            actual = bundle["detection"][method]["f1"]
            if abs(actual - expected) > 1e-9:
                raise AssertionError(
                    f"{method} detector F1 changed: {actual} != {expected}"
                )

    if site_dir is not None:
        write_temporal_site(cases, bundle, site_dir)

    print(f"Validated {len(cases)} controlled cases")
    print(f"Dataset: {Path(DATASET_PATH)}")
    print(f"Table:   {Path(SUMMARY_CSV)}")
    print(f"Detector:{Path(DETECTOR_SUMMARY_CSV)}")
    print(f"Report:  {Path(SUMMARY_MD)}")
    if site_dir is not None:
        print(f"Site:    {site_dir / 'temporal-audit.html'}")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate dataset cardinality and deterministic policy invariants.",
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        help="Also generate the temporal-audit GitHub Pages artifacts.",
    )
    args = parser.parse_args()
    run(check=args.check, site_dir=args.site_dir)


if __name__ == "__main__":
    main()
