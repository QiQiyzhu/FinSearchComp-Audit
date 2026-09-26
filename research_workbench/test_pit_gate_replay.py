"""Counterexamples for the exposed-pilot system intervention, without API calls."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest

from scripts.replay_pit_gate import DEFAULT_RECEIPT, DEFAULT_SUMMARY, gate_answer, public_summary, run_replay


def request():
    scope = {"ticker": "MSFT", "accession": "0000789019-24-000001", "period_start": "2023-07-01", "period_end": "2024-06-30"}
    documents = [{"id": "r", "metric": "revenue", "value": "100", "unit": "USD", "form": "10-K", "filing_date": "2024-07-30", **scope},
                 {"id": "o", "metric": "operating_income", "value": "25", "unit": "USD", "form": "10-K", "filing_date": "2024-07-30", **scope}]
    return {"as_of": "2024-07-31", "evidence_scope": scope, "documents": documents}


def answer(value="25.00", unit="%", ids=None):
    return {"action": "answer", "value": value, "unit": unit, "evidence_ids": ["o", "r"] if ids is None else ids, "explanation": "unverified model prose"}


class EvidenceGateTests(unittest.TestCase):
    def test_accepts_complete_exact_value_and_replaces_uncertified_prose(self):
        result = gate_answer(request(), "operating_margin", answer())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["answer"]["value"], "25.00")
        self.assertNotEqual(result["answer"]["explanation"], answer()["explanation"])

    def test_correct_memory_value_is_blocked_without_repair(self):
        result = gate_answer(request(), "operating_margin", answer(ids=[]))
        self.assertEqual(result["reason"], "no_cited_evidence")
        self.assertEqual(result["answer"]["action"], "abstain")
        self.assertIsNone(result["answer"]["value"])

    def test_missing_operand_citation_cannot_pass_on_correct_number(self):
        result = gate_answer(request(), "operating_margin", answer(ids=["r"]))
        self.assertEqual(result["reason"], "incomplete_operands")

    def test_same_day_and_future_evidence_both_block(self):
        for cutoff in ["2024-07-29", "2024-07-30"]:
            original = request()
            original["as_of"] = cutoff
            result = gate_answer(original, "operating_margin", answer())
            self.assertEqual(result["typed_state"], "unavailable_as_of")
            self.assertFalse(result["accepted"])

    def test_supplied_wrong_company_or_period_blocks(self):
        for key, value in [("ticker", "AAPL"), ("period_end", "2024-09-30"), ("accession", "different")]:
            original = request()
            original["documents"][0][key] = value
            self.assertEqual(gate_answer(original, "operating_margin", answer())["reason"], "document_scope_mismatch")

    def test_unknown_citation_and_wrong_numbers_block(self):
        self.assertEqual(gate_answer(request(), "operating_margin", answer(ids=["o", "invented"]))["reason"], "citation_not_supplied")
        self.assertEqual(gate_answer(request(), "operating_margin", answer("26.00"))["reason"], "numeric_or_unit_mismatch")
        self.assertEqual(gate_answer(request(), "operating_margin", answer(unit="USD"))["reason"], "numeric_or_unit_mismatch")

    def test_conflicting_supplied_operands_block(self):
        original = request()
        original["documents"].append({**original["documents"][0], "id": "r2", "value": "101"})
        result = gate_answer(original, "operating_margin", answer(ids=["o", "r", "r2"]))
        self.assertEqual(result["reason"], "conflicting_operands")

    def test_model_abstention_is_retained_not_repaired_from_evidence(self):
        raw = {"action": "abstain", "value": None, "unit": "%", "evidence_ids": [], "explanation": "unknown"}
        result = gate_answer(request(), "operating_margin", raw)
        self.assertEqual(result["decision"], "retained_abstention")
        self.assertEqual(result["answer"]["action"], "abstain")

    def test_gate_cannot_access_gold_and_does_not_consume_injected_gold(self):
        original = request()
        baseline = gate_answer(original, "operating_margin", answer())
        original["gold"] = {"post": {"value": "999999", "action": "abstain"}}
        original["phase"] = "pre"
        original["condition"] = "closed_book"
        self.assertEqual(gate_answer(original, "operating_margin", answer()), baseline)

    def test_published_exposed_replay_is_exact_and_coverage_loss_is_visible(self):
        rebuilt = run_replay()
        self.assertEqual(rebuilt, json.loads(DEFAULT_RECEIPT.read_text(encoding="utf-8")))
        self.assertEqual(public_summary(rebuilt), json.loads(DEFAULT_SUMMARY.read_text(encoding="utf-8")))
        self.assertEqual(rebuilt["new_model_calls"], 0)
        self.assertEqual(rebuilt["summary"]["before"]["post_numeric_correct"], 18)
        self.assertEqual(rebuilt["summary"]["after"]["post_numeric_correct"], 12)
        self.assertEqual(rebuilt["summary"]["post_answer_coverage_loss"], 6)


if __name__ == "__main__":
    unittest.main()
