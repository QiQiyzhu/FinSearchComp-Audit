from __future__ import annotations

import json
import unittest

from .atlas_compute import (
    COMPUTE_PLAN_SCHEMA,
    build_compute_normalization_payload,
    build_compute_research_prompt,
    execute_compute_plan,
    runtime_case,
)


CASE = {
    "id": "fixture",
    "question_zh": "A公司的比率比B公司高多少个百分点？",
    "cutoff_date": "2025-01-01",
    "target_period": "FY2024",
    "required_version": "filed",
    "canonical_unit": "percentage_point",
    "gold_answer": "25.00",
    "reference_sources": ["https://labels.invalid/reference"],
    "gold_calculation": "hidden",
}


def operand(label: str, value: str, url: str = "https://www.sec.gov/filing") -> dict:
    return {
        "label": label,
        "value": value,
        "unit": "USD_million",
        "target_period": "FY2024",
        "published_at": "2024-12-01",
        "url": url,
        "evidence_text": f"{label} = {value}",
    }


class AtlasComputeTests(unittest.TestCase):
    def test_operation_schema_uses_relay_compatible_string_enum(self) -> None:
        operation = COMPUTE_PLAN_SCHEMA["properties"]["operation"]
        self.assertEqual(operation["type"], "string")
        self.assertIn("none", operation["enum"])

    def test_runtime_prompt_excludes_evaluation_labels(self) -> None:
        self.assertNotIn("gold_answer", runtime_case(CASE))
        prompt = build_compute_research_prompt(CASE)
        self.assertNotIn("25.00", prompt)
        self.assertNotIn("labels.invalid", prompt)
        self.assertNotIn("gold_calculation", prompt)
        payload = build_compute_normalization_payload(
            CASE,
            "memo",
            [{"url": "https://www.sec.gov/filing"}],
            [{"url": "https://www.sec.gov/filing"}],
            model="fixture",
            effort="medium",
            max_output_tokens=1000,
        )
        serialized = json.dumps(payload)
        self.assertNotIn("gold_answer", serialized)
        self.assertNotIn("labels.invalid", serialized)

    def test_relative_change_uses_decimal_and_rounds_half_up(self) -> None:
        percent_case = {**CASE, "canonical_unit": "percent"}
        plan = {
            "action": "compute",
            "operation": "relative_change_percent",
            "operands": [operand("current", "108"), operand("prior", "100")],
            "explanation": "fixture",
        }
        result = execute_compute_plan(
            plan,
            percent_case,
            [{"url": "https://www.sec.gov/filing"}],
            [{"url": "https://www.sec.gov/filing"}],
        )
        self.assertEqual(result["action"], "answer")
        self.assertEqual(result["answer_value"], "8.00")
        self.assertEqual(result["unit"], "percent")

    def test_difference_of_ratios_preserves_operand_order(self) -> None:
        plan = {
            "action": "compute",
            "operation": "difference_of_ratios_pp",
            "operands": [
                operand("left numerator", "50"),
                operand("left denominator", "100"),
                operand("right numerator", "25"),
                operand("right denominator", "100"),
            ],
            "explanation": "fixture",
        }
        result = execute_compute_plan(
            plan,
            CASE,
            [{"url": "https://www.sec.gov/filing"}],
            [{"url": "https://www.sec.gov/filing"}],
        )
        self.assertEqual(result["answer_value"], "25.00")
        self.assertEqual(
            result["calculation_trace"]["formula"],
            "(x0 / x1 - x2 / x3) * 100",
        )

    def test_unretrieved_url_forces_abstention(self) -> None:
        plan = {
            "action": "compute",
            "operation": "relative_change_percent",
            "operands": [
                operand("current", "108", "https://invented.invalid/value"),
                operand("prior", "100"),
            ],
            "explanation": "fixture",
        }
        result = execute_compute_plan(
            plan,
            {**CASE, "canonical_unit": "percent"},
            [{"url": "https://www.sec.gov/filing"}],
            [{"url": "https://www.sec.gov/filing"}],
        )
        self.assertEqual(result["action"], "abstain")
        self.assertIn(
            "url_not_retrieved_or_cited",
            result["calculation_trace"]["violations"],
        )

    def test_post_cutoff_operand_forces_abstention(self) -> None:
        future = operand("current", "108")
        future["published_at"] = "2025-01-02"
        plan = {
            "action": "compute",
            "operation": "relative_change_percent",
            "operands": [future, operand("prior", "100")],
            "explanation": "fixture",
        }
        result = execute_compute_plan(
            plan,
            {**CASE, "canonical_unit": "percent"},
            [{"url": "https://www.sec.gov/filing"}],
            [{"url": "https://www.sec.gov/filing"}],
        )
        self.assertEqual(result["action"], "abstain")
        self.assertIn(
            "published_after_cutoff",
            result["calculation_trace"]["violations"],
        )


if __name__ == "__main__":
    unittest.main()
