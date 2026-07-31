from __future__ import annotations

import json
import unittest

from .live_agent import (
    OpenAIResponsesWebSearch,
    RequestConfig,
    apply_local_validation,
    build_prompt,
    extract_api_citations,
    extract_search_actions,
    normalize_agent_result,
    parse_json_object,
)
from .live_evaluate import answer_is_correct, parse_number, summarize
from .run_live_pilot import plan


CASE = {
    "id": "demo",
    "question_zh": "测试问题",
    "cutoff_date": "2025-01-02",
    "target_period": "2024",
    "required_version": "final",
    "gold_answer": "23.31",
    "canonical_unit": "percent",
}


def response_fixture(payload: dict) -> dict:
    strategy_is_strict = "strictly as of" in payload["input"]
    published_at = "2024-12-31" if strategy_is_strict else "2025-03-01"
    result = {
        "action": "answer",
        "answer_value": "23.31",
        "unit": "percent",
        "explanation": "fixture",
        "evidence": [
            {
                "url": "https://example.com/source",
                "title": "Example",
                "published_at": published_at,
                "target_period": "2024",
                "revision": "final",
                "unit": "percent",
                "evidence_text": "23.31%",
            }
        ],
    }
    return {
        "id": "resp_fixture",
        "model": payload["model"],
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "output": [
            {
                "type": "web_search_call",
                "action": {"type": "search", "query": "fixture query"},
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(result),
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.com/source",
                                "title": "Example",
                                "start_index": 0,
                                "end_index": 7,
                            }
                        ],
                    }
                ],
            },
        ],
    }


class LiveAgentTests(unittest.TestCase):
    def test_prompts_differ_by_strategy(self) -> None:
        plain = build_prompt(CASE, "plain_agent")
        temporal = build_prompt(CASE, "temporal_prompt")
        full = build_prompt(CASE, "teg_validator")
        self.assertNotIn("strictly as of", plain)
        self.assertIn("strictly as of 2025-01-02", temporal)
        self.assertIn("percent", full)

    def test_json_parser_accepts_fenced_output(self) -> None:
        parsed = parse_json_object('```json\n{"action":"abstain","evidence":[]}\n```')
        self.assertEqual(parsed["action"], "abstain")

    def test_response_trace_extractors(self) -> None:
        response = response_fixture({"input": "", "model": "fixture"})
        self.assertEqual(
            extract_api_citations(response)[0]["url"], "https://example.com/source"
        )
        self.assertEqual(extract_search_actions(response)[0]["type"], "search")

    def test_metadata_filter_rejects_future_evidence(self) -> None:
        result = normalize_agent_result(
            json.loads(
                response_fixture({"input": "", "model": "fixture"})["output"][1][
                    "content"
                ][0]["text"]
            )
        )
        filtered = apply_local_validation("metadata_filter", result, CASE)
        self.assertEqual(filtered["action"], "abstain")
        self.assertTrue(filtered["filter_triggered"])
        self.assertIn(
            "published_after_cutoff",
            filtered["evidence_audits"][0]["violations"],
        )

    def test_live_client_uses_injected_transport(self) -> None:
        client = OpenAIResponsesWebSearch(
            RequestConfig(model="fixture-model", max_retries=0),
            transport=response_fixture,
        )
        record = client.run(CASE, "teg_validator")
        self.assertEqual(record["result"]["action"], "answer")
        self.assertEqual(record["response_id"], "resp_fixture")
        self.assertEqual(len(record["api_citations"]), 1)

    def test_numeric_evaluation_and_summary(self) -> None:
        record = {
            "status": "ok",
            "strategy": "teg_validator",
            "case": CASE,
            "result": {
                "action": "answer",
                "answer_value": "23.31%",
                "unit": "percent",
                "evidence": [{"published_at": "2024-12-31"}],
                "filter_triggered": False,
            },
            "api_citations": [{"url": "https://example.com"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "search_actions": [{"type": "search"}],
            "latency_seconds": 1.0,
        }
        self.assertEqual(parse_number("391,035"), 391035.0)
        self.assertTrue(answer_is_correct(record))
        metrics = summarize([record])["teg_validator"]
        self.assertEqual(metrics["decision_accuracy"], 1.0)
        self.assertEqual(metrics["citation_coverage"], 1.0)

    def test_cost_guard_counts_retries(self) -> None:
        with self.assertRaises(ValueError):
            plan(20, ("plain_agent", "temporal_prompt"), "fixture", 40, 1)
        text = plan(20, ("plain_agent", "temporal_prompt"), "fixture", 80, 1)
        self.assertIn("80 HTTP attempts", text)


if __name__ == "__main__":
    unittest.main()
