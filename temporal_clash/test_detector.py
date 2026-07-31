from __future__ import annotations

import unittest

from .detector import TemporalLeakageDetector
from .generate_dataset import generate


class TemporalLeakageDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = generate()

    def case(self, condition: str) -> dict:
        return next(
            case for case in self.cases if case["evidence_condition"] == condition
        )

    def test_full_profile_accepts_clean_evidence(self) -> None:
        case = self.case("clean")
        result = TemporalLeakageDetector("full").audit(case["candidates"][0], case)
        self.assertTrue(result.accepted)
        self.assertEqual(result.risk_score, 0.0)
        self.assertFalse(result.violations)

    def test_full_profile_rejects_each_perturbation_type(self) -> None:
        expected_check = {
            "future_only": "published_at",
            "period_conflict": "target_period",
            "unit_conflict": "unit",
            "version_conflict": "revision",
        }
        detector = TemporalLeakageDetector("full")
        for condition, check_name in expected_check.items():
            with self.subTest(condition=condition):
                case = self.case(condition)
                result = detector.audit(case["candidates"][0], case)
                self.assertFalse(result.accepted)
                self.assertIn(check_name, result.violations)

    def test_full_profile_falls_back_to_safe_candidate(self) -> None:
        detector = TemporalLeakageDetector("full")
        for condition in ("period_conflict", "unit_conflict", "version_conflict"):
            with self.subTest(condition=condition):
                case = self.case(condition)
                selection = detector.select(case)
                self.assertIsNotNone(selection.selected)
                self.assertTrue(selection.selected["supports_gold"])
                self.assertEqual(len(selection.audits), 2)

    def test_future_only_case_abstains(self) -> None:
        case = self.case("future_only")
        selection = TemporalLeakageDetector("full").select(case)
        self.assertIsNone(selection.selected)
        self.assertEqual(selection.audits[0].violations, ("published_at",))

    def test_date_only_profile_does_not_claim_metadata_checks(self) -> None:
        case = self.case("unit_conflict")
        result = TemporalLeakageDetector("date_only").audit(
            case["candidates"][0], case
        )
        self.assertTrue(result.accepted)
        self.assertEqual(tuple(check.check for check in result.checks), ("published_at",))

    def test_candidate_level_ground_truth_is_balanced(self) -> None:
        candidates = [
            candidate for case in self.cases for candidate in case["candidates"]
        ]
        self.assertEqual(len(candidates), 160)
        self.assertEqual(sum(item["is_perturbed"] for item in candidates), 80)
        full = TemporalLeakageDetector("full")
        predicted = [
            not full.audit(candidate, case).accepted
            for case in self.cases
            for candidate in case["candidates"]
        ]
        self.assertEqual(sum(predicted), 80)


if __name__ == "__main__":
    unittest.main()
