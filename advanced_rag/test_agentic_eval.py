from datetime import date
from decimal import Decimal
import unittest

from advanced_rag.agentic import (
    AgenticDocument,
    AgenticQuery,
    DeterministicCalculator,
    StructuredGapPlanner,
    SufficiencyGate,
)
from advanced_rag.evaluate_agentic import (
    BUDGETS,
    load_cases,
    run_budget_sweep,
    run_gap_ablation,
    run_sufficiency_gate,
)


class AgenticEvaluationTests(unittest.TestCase):
    def test_dataset_is_frozen_and_has_eight_cases(self):
        cases = load_cases()
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({case.query.query_id for case in cases}), 8)

    def test_planner_compiles_two_slot_growth_query(self):
        plan = StructuredGapPlanner().plan(load_cases()[0].query)
        self.assertEqual(plan.operation, "relative_change")
        self.assertEqual(
            [(slot.metric, slot.period) for slot in plan.slots],
            [("revenue", "FY2024"), ("revenue", "FY2023")],
        )

    def test_planner_compiles_four_slot_margin_query(self):
        plan = StructuredGapPlanner().plan(load_cases()[2].query)
        self.assertEqual(plan.operation, "margin_change")
        self.assertEqual(
            plan.required_slot_ids,
            (
                "current_numerator",
                "current_denominator",
                "prior_numerator",
                "prior_denominator",
            ),
        )

    def test_planner_compiles_ratio_numerator_and_denominator(self):
        plan = StructuredGapPlanner().plan(load_cases()[4].query)
        self.assertEqual(plan.operation, "ratio")
        self.assertEqual(
            [slot.metric for slot in plan.slots],
            ["research_and_development", "revenue"],
        )

    def test_gate_ignores_preliminary_and_future_documents(self):
        case = load_cases()[0]
        plan = StructuredGapPlanner().plan(case.query)
        decision = SufficiencyGate().assess(plan, case.documents, case.query.as_of)
        self.assertTrue(decision.answerable)
        self.assertEqual(decision.matched["current"].doc_id, "gap-01-10-current")
        self.assertEqual(DeterministicCalculator.answer(plan, decision), case.gold_answer)

    def test_effective_interval_is_half_open(self):
        document = AgenticDocument(
            doc_id="interval",
            company="Northstar",
            metric="revenue",
            period="FY2024",
            value=Decimal("1"),
            unit="usd_million",
            text="interval test",
            published_at=date(2024, 1, 1),
            effective_from=date(2024, 1, 2),
            effective_to=date(2024, 2, 1),
        )
        self.assertFalse(document.is_visible_at(date(2024, 1, 1)))
        self.assertTrue(document.is_visible_at(date(2024, 1, 2)))
        self.assertFalse(document.is_visible_at(date(2024, 2, 1)))

    def test_gate_abstains_when_a_required_slot_is_missing(self):
        case = load_cases()[0]
        plan = StructuredGapPlanner().plan(case.query)
        only_current = [document for document in case.documents if document.period == "FY2024"]
        decision = SufficiencyGate().assess(plan, only_current, case.query.as_of)
        self.assertFalse(decision.answerable)
        self.assertEqual(decision.missing_slots, ("prior",))
        self.assertIsNone(DeterministicCalculator.answer(plan, decision))

    def test_gate_abstains_on_conflicting_final_values(self):
        case = load_cases()[0]
        plan = StructuredGapPlanner().plan(case.query)
        original = next(document for document in case.documents if document.doc_id == "gap-01-10-current")
        conflict = AgenticDocument(
            **{
                **original.__dict__,
                "doc_id": "conflict",
                "value": Decimal("1300"),
                "content_hash": "different",
            }
        )
        decision = SufficiencyGate().assess(plan, (*case.documents, conflict), case.query.as_of)
        self.assertFalse(decision.answerable)
        self.assertEqual(decision.conflicting_slots, ("current",))

    def test_e4_structured_planner_closes_one_shot_gap(self):
        aggregate, detail = run_gap_ablation(load_cases())
        by_method = {row["method"]: row for row in aggregate}
        self.assertEqual(by_method["plain_one_shot"]["answer_accuracy"], 0.75)
        self.assertEqual(by_method["query_rewrite"]["answer_accuracy"], 0.75)
        self.assertEqual(by_method["structured_gap_planner"]["answer_accuracy"], 1.0)
        self.assertEqual(len(detail), 24)

    def test_e5_gate_eliminates_unsupported_answers_without_false_abstention(self):
        aggregate, detail = run_sufficiency_gate(load_cases())
        by_method = {row["method"]: row for row in aggregate}
        baseline = by_method["must_answer_baseline"]
        gated = by_method["sufficiency_gate"]
        self.assertGreater(baseline["unsupported_answer_rate"], 0)
        self.assertEqual(gated["unsupported_answer_rate"], 0)
        self.assertEqual(gated["correct_abstention_rate"], 1)
        self.assertEqual(gated["false_abstention_rate"], 0)
        self.assertEqual(len(detail), 64)

    def test_e6_budget_sweep_reaches_plateau_and_stops_early(self):
        aggregate, detail = run_budget_sweep(load_cases())
        self.assertEqual([row["budget"] for row in aggregate], list(BUDGETS))
        self.assertEqual(aggregate[0]["answer_accuracy"], 0)
        self.assertEqual(aggregate[3]["answer_accuracy"], 1)
        self.assertEqual(aggregate[4]["answer_accuracy"], 1)
        self.assertEqual(aggregate[3]["mean_cost_proxy"], aggregate[4]["mean_cost_proxy"])
        self.assertEqual(aggregate[4]["early_stop_rate"], 1)
        self.assertEqual(len(detail), 40)

    def test_unsupported_query_fails_closed(self):
        query = AgenticQuery("unsupported", "截至 FY2024 的数字是多少？", date(2024, 1, 1))
        with self.assertRaisesRegex(ValueError, "company"):
            StructuredGapPlanner().plan(query)


if __name__ == "__main__":
    unittest.main()
