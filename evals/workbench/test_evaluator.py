"""Scoring integrity checks, independent of the product implementation."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("workbench_evaluator", ROOT / "scripts/evaluate_workbench.py")
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


class EvaluatorIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.gold, cls.raw, cls.cases, cls.audit = quality.audit_fixtures()

    def example(self):
        case = copy.deepcopy(self.cases[0])
        accession = "0000950170-24-087843"
        evidence = {"id": "E01", "metric": "revenue", "taxonomy_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "period_start": "2023-07-01", "period_end": "2024-06-30", "published_at": "2024-07-30",
                    "accession": accession, "unit": "USD", "value": "245122000000",
                    "upstream_sha256": self.manifest["upstream_sha256"]["MSFT"],
                    "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
                    "url": f"https://www.sec.gov/Archives/edgar/data/789019/{accession.replace('-', '')}/{accession}-index.htm"}
        answer = {"metric_id": "revenue", "operation": "value", "fiscal_year": 2024, "comparison_fiscal_year": None,
                  "answerability": "answered", "value": "245122000000", "unit": "USD", "evidence_ids": ["E01"],
                  "period_start": "2023-07-01", "period_end": "2024-06-30", "text": "FY2024 revenue was 245122000000 USD."}
        return case, {"coverage": {"question_supported": True}, "evidence": [evidence], "answers": [answer]}

    def score(self, case, report):
        return quality.score_case(case, {"report": report}, self.gold, self.raw, self.manifest)

    def test_exact_value_and_original_evidence_pass(self):
        case, report = self.example()
        self.assertTrue(self.score(case, report)["fulfilled"])

    def test_unused_correct_metric_cannot_replace_missing_answer(self):
        case, report = self.example()
        report["metrics"] = [{"id": "revenue", "value": "245122000000", "period_end": "2024-06-30"}]
        report["answers"] = []
        result = self.score(case, report)
        self.assertEqual(result["correct_requested_values"], 0)
        self.assertFalse(result["fulfilled"])

    def test_wrong_value_retains_fixed_requested_denominator(self):
        case, report = self.example()
        report["answers"][0]["value"] = "245122000001"
        result = self.score(case, report)
        self.assertEqual(result["requested_values"], 1)
        self.assertEqual(result["correct_requested_values"], 0)

    def test_correct_number_with_unknown_citation_fails(self):
        case, report = self.example()
        report["answers"][0]["evidence_ids"] = ["INVENTED"]
        result = self.score(case, report)
        self.assertEqual(result["correct_requested_values"], 1)
        self.assertFalse(result["fulfilled"])

    def test_one_day_future_filing_fails(self):
        case, report = self.example()
        case["as_of"] = "2024-07-29"
        self.assertFalse(self.score(case, report)["evidence_valid"])

    def test_tampered_original_value_fails_raw_audit(self):
        case, report = self.example()
        report["evidence"][0]["value"] = "245122000001"
        self.assertFalse(self.score(case, report)["evidence_valid"])

    def test_refusal_cannot_get_credit_from_unrelated_correct_number(self):
        case, report = self.example()
        report["coverage"]["question_supported"] = False
        result = self.score(case, report)
        self.assertTrue(result["false_refusal"])
        self.assertFalse(result["fulfilled"])

    def test_exception_is_not_a_correct_abstention(self):
        case = next(case for case in self.cases if case["expect"] == "abstain")
        result = quality.score_case(case, {"error": "boom"}, self.gold, self.raw, self.manifest)
        self.assertFalse(result["correct_abstention"])


if __name__ == "__main__":
    unittest.main()
