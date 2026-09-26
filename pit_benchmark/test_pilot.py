"""Integrity and negative tests; simulated transport rows are never pilot results."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from pit_benchmark.pilot import (audit_fixture, canonical, evidence_for, load_fixture, parse_answer, plan_bundle,
                    request_payload, round_fraction, score_answer, sha, summarize)
from pit_benchmark.run import apply_response, code_hashes, replay, write_exclusive


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture, cls.manifest, cls.audit = load_fixture()

    def answer(self, *, action="answer", value="245122000000", unit="USD", ids=None):
        return {"action": action, "value": value, "unit": unit,
                "evidence_ids": ids or [], "explanation": "Synthetic unit-test response"}

    def test_fixture_exact_raw_rows_and_independent_gold(self):
        self.assertEqual(self.audit["literal_operands"], 6)
        self.assertEqual(self.audit["raw_sources"], 3)
        self.assertEqual([case["gold"]["post"]["value"] for case in self.fixture["cases"]],
                         ["245122000000", "44.64", "391035000000", "31.51", "60922000000", "54.12"])

    def test_tampered_value_accession_and_hash_fail(self):
        for field, value in (("value", "245122000001"), ("accession", "future-accession"),
                             ("upstream_sha256", "0" * 64)):
            fixture = deepcopy(self.fixture)
            fixture["evidence"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                audit_fixture(fixture)

    def test_wrong_gold_and_same_day_cutoff_rejected(self):
        fixture = deepcopy(self.fixture)
        fixture["cases"][1]["gold"]["post"]["value"] = "44.65"
        with self.assertRaises(ValueError):
            audit_fixture(fixture)
        fixture = deepcopy(self.fixture)
        fixture["cases"][0]["pre_as_of"] = fixture["cases"][0]["filing_date"]
        with self.assertRaises(ValueError):
            audit_fixture(fixture)

    def test_filter_changes_only_visible_evidence(self):
        case = self.fixture["cases"][1]
        self.assertEqual(len(evidence_for(self.fixture, case, "pre", "unfiltered")), 2)
        self.assertEqual(evidence_for(self.fixture, case, "pre", "pit_filtered"), [])
        self.assertEqual(evidence_for(self.fixture, case, "post", "pit_filtered"),
                         evidence_for(self.fixture, case, "post", "unfiltered"))
        self.assertEqual(evidence_for(self.fixture, case, "post", "closed_book"), [])

    def test_same_prompt_no_gold_or_condition_labels(self):
        case = self.fixture["cases"][0]
        requests = [request_payload(self.fixture, case, "pre", condition, "unit-test-model")
                    for condition in ("closed_book", "unfiltered", "pit_filtered")]
        self.assertEqual(requests[0]["messages"][0], requests[1]["messages"][0])
        users = [json.loads(request["messages"][1]["content"]) for request in requests]
        for user in users:
            user.pop("documents")
        self.assertEqual(users[0], users[1])
        self.assertEqual(users[1], users[2])
        self.assertEqual(users[0]["evidence_scope"]["accession"], case["accession"])
        self.assertNotIn("gold", canonical(requests))
        self.assertNotIn("phase", users[0])

    def test_memory_answer_can_be_numerically_correct_without_support(self):
        case = self.fixture["cases"][0]
        score = score_answer(self.fixture, case, "post", "closed_book", self.answer())
        self.assertTrue(score["decision_correct"])
        self.assertTrue(score["numeric_correct"])
        self.assertFalse(score["evidence_support_complete"])

    def test_pre_number_is_corpus_unsupported_even_when_numerically_exact(self):
        score = score_answer(self.fixture, self.fixture["cases"][0], "pre", "closed_book", self.answer())
        self.assertFalse(score["decision_correct"])
        self.assertTrue(score["unsupported_historical_answer"])
        self.assertIsNone(score["numeric_correct"])
        self.assertFalse(score["accepted_future_evidence"])

    def test_future_citation_and_unpresented_citation_are_distinct(self):
        case = self.fixture["cases"][0]
        answer = self.answer(ids=case["evidence_ids"])
        exposed = score_answer(self.fixture, case, "pre", "unfiltered", answer)
        blocked = score_answer(self.fixture, case, "pre", "pit_filtered", answer)
        self.assertTrue(exposed["accepted_future_evidence"])
        self.assertFalse(exposed["unsupported_citations"])
        self.assertTrue(blocked["unsupported_citations"])
        self.assertFalse(blocked["accepted_future_evidence"])
        self.assertFalse(blocked["evidence_support_complete"])

    def test_unknown_citations_never_count_as_grounding(self):
        case = self.fixture["cases"][0]
        score = score_answer(self.fixture, case, "post", "unfiltered", self.answer(ids=["MADE_UP"]))
        self.assertTrue(score["numeric_correct"])
        self.assertTrue(score["unsupported_citations"])
        self.assertFalse(score["joint_correct_and_supported"])

    def test_margin_requires_both_operands_and_correct_unit(self):
        case = self.fixture["cases"][1]
        answer = self.answer(value="44.64", unit="%", ids=case["evidence_ids"])
        self.assertTrue(score_answer(self.fixture, case, "post", "unfiltered", answer)["joint_correct_and_supported"])
        answer["evidence_ids"] = case["evidence_ids"][:1]
        self.assertFalse(score_answer(self.fixture, case, "post", "unfiltered", answer)["evidence_support_complete"])
        answer["unit"] = "USD"
        self.assertFalse(score_answer(self.fixture, case, "post", "unfiltered", answer)["numeric_correct"])

    def test_fraction_rounding_is_exact_including_half_and_negative(self):
        self.assertEqual(round_fraction(Fraction(1005, 1000)), "1.01")
        self.assertEqual(round_fraction(Fraction(-1005, 1000)), "-1.01")
        self.assertEqual(round_fraction(Fraction(109433, 245122) * 100), "44.64")

    def test_invalid_output_types_and_nonfinite_values_fail(self):
        for changes in ({"value": "NaN"}, {"value": "Infinity"}, {"value": 12},
                        {"action": []}, {"unit": {}}, {"evidence_ids": "fake"},
                        {"evidence_ids": [1]}, {"explanation": []},
                        {"action": "abstain", "value": "12"},
                        {"action": "abstain", "value": None, "evidence_ids": ["x"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_answer(canonical({**self.answer(), **changes}))

    def test_offline_plan_has_zero_attempts_not_fabricated_results(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No networking allowed")):
            fixture, manifest, audit = load_fixture()
            bundle = plan_bundle(fixture, manifest, audit)
        self.assertEqual(bundle["summary"]["planned_runs"], 36)
        self.assertEqual(bundle["summary"]["attempted_runs"], 0)
        self.assertTrue(all(run["score"] is None for run in bundle["runs"]))
        groups = {row["condition"]: row for row in bundle["summary"]["by_condition"]}
        self.assertEqual(groups["unfiltered"]["future_exposed_planned"], 6)
        self.assertEqual(groups["pit_filtered"]["future_exposed_planned"], 0)
        self.assertEqual(groups["unfiltered"]["future_exposed_scored"], 0)

    def test_error_and_invalid_denominators_stay_visible(self):
        bundle = plan_bundle(self.fixture, self.manifest, self.audit)
        bundle["runs"][0]["status"] = "error"
        bundle["runs"][1]["status"] = "invalid"
        summary = summarize(bundle["runs"])
        self.assertEqual(summary["planned_runs"], 36)
        self.assertEqual(summary["attempted_runs"], 2)
        self.assertEqual(summary["scored_runs"], 0)
        self.assertEqual(summary["error_runs"], 1)
        self.assertEqual(summary["invalid_runs"], 1)

    def mock_events(self):
        bundle = plan_bundle(self.fixture, self.manifest, self.audit)
        run = bundle["runs"][0]
        request = request_payload(self.fixture, self.fixture["cases"][0], "pre", "closed_book", "synthetic-unit-test")
        answer = self.answer(action="abstain", value=None)
        response = {"id": "synthetic-test-only", "model": "synthetic-unit-test", "usage": {"total_tokens": 3},
                    "choices": [{"finish_reason": "stop", "message": {"content": canonical(answer)}}]}
        text = canonical(response)
        event = {"event": "response", "run_id": run["id"], "evaluated_at": "2026-09-26T00:00:00Z",
                 "latency_ms": 1, "provider": {"requested_model": "synthetic-unit-test"},
                 "response_text": text, "response_sha256": sha(text.encode()), "error": None}
        apply_response(bundle, self.fixture, run, event)
        return [
            {"event": "header", "recorded_at": "2026-09-26T00:00:00Z", "dataset_sha256": self.manifest["dataset_sha256"],
             "protocol": self.fixture["protocol"], "execution": {"max_calls": 1, "requested_model": "synthetic-unit-test", "code_sha256": code_hashes()}},
            {"event": "request", "run_id": run["id"], "request": request, "request_sha256": sha(canonical(request).encode())},
            event,
            {"event": "footer", "completed_at": "2026-09-26T00:00:01Z", "summary": summarize(bundle["runs"])}]

    def replay_events(self, events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-test.trace.jsonl"
            path.write_text("".join(canonical(event) + "\n" for event in events), encoding="utf-8")
            return replay(path, self.fixture, self.manifest, self.audit)

    def test_replay_reconstructs_score_without_network(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No networking allowed")):
            bundle = self.replay_events(self.mock_events())
        self.assertEqual(bundle["summary"]["scored_runs"], 1)
        self.assertEqual(bundle["summary"]["total_tokens"], 3)
        self.assertTrue(bundle["runs"][0]["score"]["decision_correct"])

    def test_replay_rejects_changed_request_response_summary(self):
        for target in ("request", "response", "summary"):
            events = self.mock_events()
            if target == "request":
                events[1]["request"]["messages"][1]["content"] = "tampered question"
            elif target == "response":
                events[2]["response_text"] += " "
            else:
                events[-1]["summary"]["scored_runs"] = 36
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.replay_events(events)

    def test_interrupted_request_is_error_not_abstention(self):
        bundle = self.replay_events(self.mock_events()[:2])
        self.assertEqual(bundle["summary"]["attempted_runs"], 1)
        self.assertEqual(bundle["summary"]["error_runs"], 1)
        self.assertEqual(bundle["summary"]["scored_runs"], 0)

    def test_output_never_overwrites_existing_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            write_exclusive(path, {"original": True})
            with self.assertRaises(FileExistsError):
                write_exclusive(path, {"original": False})
            self.assertEqual(json.loads(path.read_text()), {"original": True})


if __name__ == "__main__":
    unittest.main()
