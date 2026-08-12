from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import run_live_pilot
from .live_agent import (
    AnthropicMessagesWebSearch,
    OpenAIResponsesWebSearch,
    RequestConfig,
    apply_local_validation,
    build_prompt,
    build_research_prompt,
    extract_api_citations,
    extract_search_actions,
    extract_search_sources,
    normalize_agent_result,
    parse_json_object,
    redact_sensitive,
)
from .live_atlas import atlas_initial_query, atlas_route
from .live_evaluate import (
    answer_is_correct,
    model_answer_is_correct,
    parse_number,
    summarize,
)
from .probe_live_api import extract_model_ids, models_endpoint
from .live_validate import validate_live_record, validate_protocol_consistency
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
                "id": "search_fixture",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "fixture query",
                    "sources": [
                        {
                            "type": "url",
                            "url": "https://example.com/source",
                            "title": "Example",
                        }
                    ],
                },
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


class AnthropicFixture:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> dict:
        self.payloads.append(payload)
        if payload.get("tools"):
            return {
                "id": "msg_research",
                "model": payload["model"],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "server_tool_use": {"web_search_requests": 1},
                },
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srvtool_1",
                        "name": "web_search",
                        "input": {"query": "fixture query"},
                        "caller": {"type": "direct"},
                    },
                    {
                        "type": "web_search_tool_result",
                        "tool_use_id": "srvtool_1",
                        "content": [
                            {
                                "type": "web_search_result",
                                "url": "https://example.com/source",
                                "title": "Example",
                                "page_age": "2024-12-31",
                            }
                        ],
                    },
                    {
                        "type": "text",
                        "text": "Research draft with the requested value.",
                        "citations": [
                            {
                                "type": "web_search_result_location",
                                "url": "https://example.com/source",
                                "title": "Example",
                                "cited_text": "23.31%",
                            }
                        ],
                    },
                ],
            }
        result = {
            "action": "answer",
            "answer_value": "23.31",
            "unit": "percent",
            "explanation": "fixture",
            "evidence": [
                {
                    "url": "https://example.com/source",
                    "title": "Example",
                    "published_at": "2024-12-31",
                    "target_period": "2024",
                    "revision": "final",
                    "unit": "percent",
                    "evidence_text": "23.31%",
                }
            ],
        }
        return {
            "id": "msg_normalize",
            "model": payload["model"],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 80, "output_tokens": 40},
            "content": [{"type": "text", "text": json.dumps(result)}],
        }


class LiveAgentTests(unittest.TestCase):
    def test_prompts_differ_by_strategy(self) -> None:
        plain = build_prompt(CASE, "plain_agent")
        temporal = build_prompt(CASE, "temporal_prompt")
        full = build_prompt(CASE, "teg_validator")
        self.assertNotIn("strictly as of", plain)
        self.assertIn("strictly as of 2025-01-02", temporal)
        self.assertIn("percent", full)
        atlas = build_prompt(CASE, "atlas_rag")
        self.assertIn("metadata as uncertainty", atlas)

    def test_atlas_route_does_not_require_labels(self) -> None:
        market_case = {**CASE, "question_zh": "标普500指数2024年回报率是多少？"}
        label_free = {
            key: value
            for key, value in market_case.items()
            if key not in {"gold_answer", "source_url", "evidence_text_zh"}
        }
        route = atlas_route(label_free)
        self.assertEqual(route["source_route"], "market_time_series")
        self.assertIn(market_case["question_zh"], atlas_initial_query(label_free))
        changed_labels = {
            **label_free,
            "gold_answer": "999999",
            "source_url": "https://labels.invalid",
            "evidence_text_zh": "should never affect routing",
        }
        self.assertEqual(route, atlas_route(changed_labels))

    def test_json_parser_accepts_fenced_output(self) -> None:
        parsed = parse_json_object('```json\n{"action":"abstain","evidence":[]}\n```')
        self.assertEqual(parsed["action"], "abstain")

    def test_response_trace_extractors(self) -> None:
        response = response_fixture({"input": "", "model": "fixture"})
        self.assertEqual(
            extract_api_citations(response)[0]["url"], "https://example.com/source"
        )
        self.assertEqual(extract_search_actions(response)[0]["type"], "search")
        self.assertEqual(
            extract_search_sources(response)[0]["url"],
            "https://example.com/source",
        )

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

    def test_atlas_keeps_unknown_date_but_rejects_explicit_future(self) -> None:
        base_result = {
            "action": "answer",
            "answer_value": "23.31",
            "unit": "percent",
            "explanation": "fixture",
            "evidence": [
                {
                    "url": "https://query1.finance.yahoo.com/chart",
                    "title": "Historical data",
                    "published_at": None,
                    "target_period": "2024",
                    "revision": "final",
                    "unit": "percent",
                    "evidence_text": "calculated from year-end closes",
                }
            ],
        }
        kept = apply_local_validation("atlas_rag", base_result, CASE, [])
        self.assertEqual(kept["action"], "answer")
        self.assertIn(
            "published_at_unknown",
            kept["evidence_audits"][0]["metadata_unknown"],
        )
        self.assertFalse(kept["filter_triggered"])

        future_result = json.loads(json.dumps(base_result))
        future_result["evidence"][0]["published_at"] = "2025-03-01"
        rejected = apply_local_validation("atlas_rag", future_result, CASE, [])
        self.assertEqual(rejected["action"], "abstain")
        self.assertIn(
            "published_after_cutoff",
            rejected["evidence_audits"][0]["violations"],
        )

    def test_atlas_uses_provider_page_age_as_independent_date(self) -> None:
        result = {
            "action": "answer",
            "answer_value": "23.31",
            "unit": "percent",
            "explanation": "fixture",
            "evidence": [
                {
                    "url": "https://example.com/source",
                    "title": "Example",
                    "published_at": None,
                    "target_period": "2024",
                    "revision": "final",
                    "unit": "percent",
                    "evidence_text": "23.31%",
                }
            ],
        }
        rejected = apply_local_validation(
            "atlas_rag",
            result,
            CASE,
            [
                {
                    "url": "https://example.com/source",
                    "page_age": "March 1, 2025",
                }
            ],
        )
        audit = rejected["evidence_audits"][0]
        self.assertEqual(audit["source_date_provenance"], "search_result_page_age")
        self.assertEqual(rejected["action"], "abstain")

    def test_live_client_uses_injected_transport(self) -> None:
        client = OpenAIResponsesWebSearch(
            RequestConfig(model="fixture-model", max_retries=0),
            transport=response_fixture,
        )
        record = client.run(CASE, "teg_validator")
        self.assertEqual(record["result"]["action"], "answer")
        self.assertEqual(record["response_id"], "resp_fixture")
        self.assertEqual(len(record["api_citations"]), 1)
        self.assertEqual(validate_live_record({"status": "ok", **record}), [])
        payload = client.build_payload(CASE, "plain_agent")
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(
            payload["include"], ["web_search_call.action.sources"]
        )
        self.assertEqual(payload["max_tool_calls"], 3)
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")

    def test_anthropic_client_uses_two_fixed_stages(self) -> None:
        fixture = AnthropicFixture()
        client = AnthropicMessagesWebSearch(
            RequestConfig(model="claude-fixture"),
            transport=fixture,
        )
        record = client.run(CASE, "teg_validator")
        self.assertEqual(len(fixture.payloads), 2)
        research, normalization = fixture.payloads
        self.assertEqual(
            research["tool_choice"], {"type": "tool", "name": "web_search"}
        )
        self.assertEqual(research["tools"][0]["max_uses"], 3)
        self.assertNotIn("format", research["output_config"])
        self.assertNotIn(
            "Return only one JSON object",
            research["messages"][0]["content"],
        )
        self.assertEqual(
            research["messages"][0]["content"], CASE["question_zh"]
        )
        self.assertIn("Complete research task", research["system"])
        self.assertIn(CASE["cutoff_date"], research["system"])
        self.assertIn(
            "native citations",
            build_research_prompt(CASE, "teg_validator"),
        )
        self.assertNotIn("tools", normalization)
        self.assertEqual(
            normalization["output_config"]["format"]["type"], "json_schema"
        )
        validated = {"status": "ok", "case": CASE, **record}
        self.assertEqual(validate_live_record(validated), [])
        self.assertEqual(
            validate_protocol_consistency([validated]), []
        )

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
        self.assertTrue(model_answer_is_correct(record))
        metrics = summarize([record])["teg_validator"]
        self.assertEqual(metrics["decision_accuracy"], 1.0)
        self.assertEqual(metrics["model_decision_accuracy"], 1.0)
        self.assertEqual(metrics["citation_coverage"], 1.0)

    def test_final_leakage_uses_only_accepted_evidence(self) -> None:
        record = {
            "status": "ok",
            "strategy": "teg_validator",
            "case": CASE,
            "result": {
                "action": "answer",
                "model_action": "answer",
                "answer_value": "23.31",
                "unit": "percent",
                "evidence": [
                    {"published_at": "2025-03-01"},
                    {"published_at": "2024-12-31"},
                ],
                "accepted_evidence": [{"published_at": "2024-12-31"}],
                "filter_triggered": False,
            },
            "api_citations": [{"url": "https://example.com"}],
            "search_sources": [{"url": "https://example.com"}],
            "usage": {},
            "search_actions": [{"type": "search"}],
            "latency_seconds": 1.0,
        }
        metrics = summarize([record])["teg_validator"]
        self.assertEqual(metrics["declared_temporal_leakage_rate"], 0.0)
        self.assertEqual(metrics["candidate_declared_future_rate"], 1.0)

    def test_cost_guard_counts_retries(self) -> None:
        with self.assertRaises(ValueError):
            plan(20, ("plain_agent", "temporal_prompt"), "fixture", 40, 1)
        text = plan(20, ("plain_agent", "temporal_prompt"), "fixture", 80, 1)
        self.assertIn("80 HTTP attempts", text)
        with self.assertRaises(ValueError):
            plan(
                20,
                ("plain_agent", "temporal_prompt"),
                "fixture",
                80,
                0,
                "anthropic",
                2,
            )
        text = plan(
            1,
            tuple(("plain_agent", "temporal_prompt")),
            "fixture",
            4,
            0,
            "anthropic",
        )
        self.assertIn("4 HTTP attempts", text)

    def test_secret_redaction(self) -> None:
        value = "Authorization: Bearer sk-" + ("x" * 32)
        redacted = redact_sensitive(value)
        self.assertNotIn("sk-", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_model_probe_parses_relay_inventory(self) -> None:
        response = {
            "data": [
                {"id": "claude-sonnet-fixture"},
                {"name": "claude-haiku-fixture"},
            ]
        }
        self.assertEqual(
            extract_model_ids(response),
            ["claude-haiku-fixture", "claude-sonnet-fixture"],
        )
        self.assertEqual(
            models_endpoint("https://relay.example/v1"),
            "https://relay.example/v1/models",
        )

    def test_runner_writes_valid_repeats_and_aggregate(self) -> None:
        def fake_factory(provider, config, *, base_url=None):
            return OpenAIResponsesWebSearch(
                config,
                transport=response_fixture,
                base_url=base_url,
            )

        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "study"
            args = run_live_pilot.parser().parse_args(
                [
                    "--limit",
                    "1",
                    "--model",
                    "fixture-model",
                    "--repeats",
                    "2",
                    "--max-api-calls",
                    "10",
                    "--output-dir",
                    str(output_dir),
                    "--confirm-live",
                ]
            )
            with (
                patch.object(
                    run_live_pilot,
                    "credential_is_available",
                    return_value=True,
                ),
                patch.object(
                    run_live_pilot,
                    "create_client",
                    side_effect=fake_factory,
                ),
            ):
                records = run_live_pilot.run(args)

            self.assertEqual(len(records), 10)
            manifest = json.loads(
                (output_dir / "study_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "completed")
            self.assertTrue((output_dir / "aggregate_metrics.csv").exists())
            self.assertTrue(
                any((output_dir / "repeat_01" / "raw_responses").iterdir())
            )


if __name__ == "__main__":
    unittest.main()
