from __future__ import annotations

import json
import unittest
from pathlib import Path

from .atlas_xbrl import (
    SecCompanyFactsClient,
    build_xbrl_payload,
    compile_question_plan,
    execute_program,
)
from .run_xbrl_study import exact_two_decimal_correct


HERE = Path(__file__).resolve().parent


class AtlasXbrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(
            (HERE / "xbrl_20q_cases.json").read_text(encoding="utf-8")
        )

    def test_all_twenty_questions_compile_to_frozen_reference_programs(self) -> None:
        self.assertEqual(len(self.cases), 20)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                plan, repaired = compile_question_plan(
                    case,
                    {
                        "action": "abstain",
                        "operation": "none",
                        "facts": [],
                        "return_magnitude": False,
                    },
                )
                observed = [
                    [item["ticker"], item["metric"], item["fiscal_year"]]
                    for item in plan["facts"]
                ]
                self.assertEqual(plan["operation"], case["reference_program"]["operation"])
                self.assertEqual(observed, case["reference_program"]["facts"])
                self.assertTrue(plan["return_magnitude"])
                self.assertTrue(repaired)

    def test_prompt_excludes_gold_and_reference_program(self) -> None:
        case = self.cases[0]
        payload = build_xbrl_payload(
            case, model="fixture", effort="medium", max_output_tokens=1000
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("gold_answer", serialized)
        self.assertNotIn("gold_calculation", serialized)
        self.assertNotIn("reference_program", serialized)
        self.assertNotIn(case["gold_calculation"], serialized)

    def test_sec_selector_prefers_latest_filed_annual_fact_before_cutoff(self) -> None:
        client = SecCompanyFactsClient()
        client.cache["MSFT"] = {
            "entityName": "Microsoft Fixture",
            "facts": {
                "us-gaap": {
                    "ResearchAndDevelopmentExpense": {
                        "label": "R&D",
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-07-01",
                                    "end": "2024-06-30",
                                    "val": 29_510_000_000,
                                    "filed": "2024-07-30",
                                    "form": "10-K",
                                    "accn": "fixture-current",
                                },
                                {
                                    "start": "2023-07-01",
                                    "end": "2024-06-30",
                                    "val": 99_999_000_000,
                                    "filed": "2025-07-30",
                                    "form": "10-K",
                                    "accn": "future-recast",
                                },
                            ]
                        },
                    }
                }
            },
        }
        client.response_hashes["MSFT"] = "fixture-sha"
        fact, action = client.fact(
            {
                "ticker": "MSFT",
                "metric": "research_and_development",
                "fiscal_year": 2024,
            },
            cutoff_date="2024-07-31",
        )
        self.assertEqual(fact["value"], "29510")
        self.assertEqual(fact["accession"], "fixture-current")
        self.assertTrue(action["cache_hit"])

    def test_program_execution_supports_eight_operand_change_gap(self) -> None:
        facts = [
            {"value": value}
            for value in ("40", "100", "20", "100", "30", "100", "25", "100")
        ]
        answer, formula = execute_program(
            "ratio_change_gap_pp", facts, return_magnitude=True
        )
        self.assertEqual(str(answer), "15.00")
        self.assertIn("x7", formula)

    def test_scoring_requires_exact_requested_two_decimal_value_and_unit(self) -> None:
        case = {
            "gold_answer": "3.37",
            "canonical_unit": "percentage_point",
        }
        correct = {
            "case": case,
            "result": {
                "action": "answer",
                "answer_value": "3.370",
                "unit": "percentage_point",
            },
        }
        rounded_wrong = {
            "case": case,
            "result": {
                "action": "answer",
                "answer_value": "3.38",
                "unit": "percentage_point",
            },
        }
        self.assertTrue(exact_two_decimal_correct(correct))
        self.assertFalse(exact_two_decimal_correct(rounded_wrong))


if __name__ == "__main__":
    unittest.main()
