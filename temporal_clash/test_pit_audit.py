from __future__ import annotations

import unittest
import json
from pathlib import Path

from .atlas_xbrl import compile_question_plan
from .pit_audit import aggregate_temporal_metrics, audit_record_temporality


class PointInTimeAuditTests(unittest.TestCase):
    def test_frozen_pit_cases_compile_without_evaluation_labels(self) -> None:
        cases = json.loads(
            (Path(__file__).with_name("pit_xbrl_20q_cases.json")).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(cases), 20)
        for case in cases:
            with self.subTest(case=case["id"]):
                plan, _ = compile_question_plan(
                    case,
                    {
                        "action": "abstain",
                        "operation": "none",
                        "facts": [],
                        "return_magnitude": False,
                    },
                )
                self.assertEqual(
                    plan["operation"], case["reference_program"]["operation"]
                )
                self.assertEqual(
                    [
                        [fact["ticker"], fact["metric"], fact["fiscal_year"]]
                        for fact in plan["facts"]
                    ],
                    case["reference_program"]["facts"],
                )

    def _record(self) -> dict:
        return {
            "run_at": "2026-08-13T00:00:00+00:00",
            "case": {"cutoff_date": "2024-07-31"},
            "search_sources": [
                {
                    "url": "https://www.sec.gov/Archives/old.htm",
                    "page_age": "July 30, 2024",
                },
                {
                    "url": "https://example.com/future",
                    "page_age": "April 18, 2026",
                },
                {"url": "https://example.com/unknown", "page_age": None},
            ],
            "api_citations": [
                {"url": "https://example.com/future", "published_at": None}
            ],
            "result": {
                "accepted_evidence": [
                    {
                        "url": "https://example.com/future",
                        "published_at": None,
                        "target_period": "FY2024",
                        "revision": "filed",
                        "unit": "USD",
                        "evidence_text": "fixture",
                    }
                ]
            },
        }

    def test_future_page_age_is_mapped_to_final_evidence(self) -> None:
        audit = audit_record_temporality(self._record())
        self.assertEqual(audit["candidate_sources"]["future"], 1)
        self.assertEqual(audit["accepted_evidence"]["future"], 1)
        self.assertTrue(audit["candidate_future_exposure"])
        self.assertTrue(audit["final_evidence_leakage"])

    def test_unknown_date_is_not_silently_counted_as_safe(self) -> None:
        audit = audit_record_temporality(self._record())
        self.assertEqual(audit["candidate_sources"]["unknown"], 1)
        self.assertAlmostEqual(
            audit["candidate_sources"]["temporal_metadata_coverage"], 2 / 3
        )

    def test_aggregate_reports_run_level_exposure(self) -> None:
        metrics = aggregate_temporal_metrics([self._record()])
        self.assertEqual(metrics["runs_with_candidate_future_exposure"], 1)
        self.assertEqual(metrics["runs_with_final_evidence_leakage"], 1)
        self.assertEqual(metrics["candidate_sec_source_rate"], 1 / 3)


if __name__ == "__main__":
    unittest.main()
