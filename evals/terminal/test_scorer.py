"""Mutation checks prove that the evaluation rejects plausible wrong outputs."""
from copy import deepcopy
import unittest

from scripts.evaluate_terminal import audit_fixtures, score_case
from scripts.evaluate_terminal_nlq import score as score_nlq


class ScorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.gold, cls.raw, cls.cases, cls.audit = audit_fixtures()

    def output(self, case):
        req, exp = case["request"], case["expected"]
        instant = all(x["metric_id"] in self.gold["instant_metrics"] for x in exp["operands"])
        metric = {"ticker": req["ticker"], "fiscal_year": req["fiscal_year"], "status": exp["status"], "value": exp["value"], "unit": exp["unit"],
                  "period_start": None if instant else exp.get("period", {}).get("start"), "period_end": exp.get("period", {}).get("end"), "evidence_ids": []}
        evidence = {}
        for index, operand in enumerate(exp["operands"]):
            eid = f"E{index}"
            metric["evidence_ids"].append(eid)
            evidence[eid] = {"ticker": req["ticker"], "taxonomy_tag": operand["tag"], "unit": operand["unit"], "raw_row": operand["row"]}
        return {"metric": metric, "evidence": evidence}

    def evaluate(self, case, output):
        return score_case(case, output, self.gold, self.raw)

    def test_gold_and_raw_hashes(self):
        self.assertEqual(self.audit["frozen_cases"], 60)
        self.assertEqual(self.audit["raw_issuers"], 8)

    def test_complete_gold_proof_passes(self):
        for case in self.cases:
            self.assertTrue(self.evaluate(case, self.output(case))["strict_pass"], case["id"])

    def test_correct_number_without_citation_fails(self):
        case = self.cases[0]
        output = self.output(case)
        output["metric"]["evidence_ids"] = []
        self.assertFalse(self.evaluate(case, output)["strict_pass"])

    def test_one_operand_does_not_prove_ratio(self):
        case = self.cases[8]
        output = self.output(case)
        output["metric"]["evidence_ids"].pop()
        self.assertFalse(self.evaluate(case, output)["dimensions"]["complete_operands"])

    def test_bad_value_and_unit_fail(self):
        case = self.cases[0]
        for key, value in [("value", "1"), ("unit", "%")]:
            output = self.output(case)
            output["metric"][key] = value
            self.assertFalse(self.evaluate(case, output)["dimensions"]["value_and_unit"])

    def test_wrong_entity_or_period_fail(self):
        case = self.cases[0]
        for key, value in [("ticker", "TSLA"), ("fiscal_year", 2023), ("period_end", "2024-12-31")]:
            output = self.output(case)
            output["metric"][key] = value
            self.assertFalse(self.evaluate(case, output)["strict_pass"])

    def test_forged_date_does_not_pass_raw_validation(self):
        case = self.cases[0]
        output = deepcopy(self.output(case))
        output["evidence"]["E0"]["raw_row"]["filed"] = "2000-01-01"
        self.assertFalse(self.evaluate(case, output)["dimensions"]["source_integrity"])

    def test_actual_future_evidence_rejected(self):
        case = deepcopy(self.cases[0])
        output = self.output(case)
        case["request"]["cutoff"] = "2020-01-01"
        self.assertFalse(self.evaluate(case, output)["dimensions"]["time_compliance"])

    def test_abstention_cannot_return_value(self):
        case = deepcopy(self.cases[0])
        case["expected"].update(status="unknown", value=None)
        output = self.output(case)
        output["metric"]["value"] = "0"
        self.assertFalse(self.evaluate(case, output)["dimensions"]["no_unjustified_value"])

    def test_nlq_unsupported_requires_reason(self):
        case = {"id": "test", "split": "development", "question": "stock price", "expected": {"supported": False}}
        self.assertFalse(score_nlq(case, {"supported": False, "gaps": []})["strict_pass"])
        self.assertTrue(score_nlq(case, {"supported": False, "gaps": ["No prices"]})["strict_pass"])


if __name__ == "__main__":
    unittest.main()
