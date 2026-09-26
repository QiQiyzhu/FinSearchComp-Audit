from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from research_workbench.api import create_app
from research_workbench.config import Settings
from research_workbench.sources import ResearchError
from research_workbench.terminal_data import query_metric
from research_workbench.terminal_engine import TerminalEngine, terminal_cube, terminal_markdown, verify_answer


def request(**changes):
    return {"question": "微软 FY2024 营业利润率和总资产是多少？", "ticker": "MSFT", "fiscal_year": 2024,
            "as_of": "2024-11-02", "mode": "demo", "metric_ids": ["operating_margin", "assets"], **changes}


class DeliveryGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cube = terminal_cube()

    def test_correct_value_requires_all_original_operands(self):
        candidate = query_metric(self.cube, "MSFT", "2024-11-02", 2024, "operating_margin")
        def check(row, **kwargs):
            return verify_answer(self.cube, row, ticker="MSFT", cutoff=kwargs.get("cutoff", "2024-11-02"), fiscal_year=2024, metric_id="operating_margin")
        self.assertTrue(check(candidate)["passed"])
        for change in [{"evidence_ids": []}, {"evidence_ids": candidate["evidence_ids"][:1]}, {"unit": "USD"},
                       {"ticker": "AAPL"}, {"period_start": "2023-01-01"}, {"value": "99.99"}, {"fiscal_year": 2023},
                       {"metric_id": "gross_margin"}, {"evidence_ids": ["invented"]}]:
            with self.subTest(change=change):
                self.assertFalse(check({**candidate, **change})["passed"])
        self.assertFalse(check(candidate, cutoff="2024-07-29")["passed"])

    def test_snapshot_without_available_evidence_skips_model(self):
        with patch("research_workbench.terminal_engine.synthesize") as model:
            report = TerminalEngine(Settings()).run(request(as_of="2024-01-01", mode="snapshot"))
        model.assert_not_called()
        self.assertEqual(report["coverage"]["available"], 0)
        self.assertEqual(report["model"]["status"], "skipped_no_verified_claims")
        self.assertEqual(report["evidence"], [])

    def test_failed_model_retains_verified_answers_and_reason(self):
        with patch("research_workbench.terminal_engine.synthesize", side_effect=ResearchError("MODEL_UNAVAILABLE", "服务暂不可用")):
            report = TerminalEngine(Settings()).run(request(mode="snapshot"))
        self.assertEqual(report["model"]["status"], "fallback")
        self.assertEqual(report["coverage"]["available"], 2)
        self.assertTrue(all(row["gate"]["passed"] for row in report["answers"]))
        self.assertIn("44.64", terminal_markdown(report))

    def test_balance_sheet_timestamp_and_comparison_period_warning(self):
        report = TerminalEngine(Settings()).run(request(compare_with="AAPL"))
        self.assertNotIn("None", report["summary"])
        self.assertTrue(any("期间不同" in note for note in report["limitations"]))


class TerminalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(database=Path(self.temp.name) / "jobs.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_async_research_export_idempotency_and_static_cube(self):
        with TestClient(create_app(self.settings)) as client:
            response = client.post("/api/terminal/research", json=request(), headers={"Idempotency-Key": "terminal-test"})
            self.assertEqual(response.status_code, 202, response.text)
            identifier = response.json()["id"]
            again = client.post("/api/terminal/research", json=request(), headers={"Idempotency-Key": "terminal-test"})
            self.assertEqual(again.json()["id"], identifier)
            for _ in range(100):
                job = client.get(f"/api/research/{identifier}").json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(job["result"]["coverage"], {"available": 2, "total": 2})
            exported = client.get(f"/api/research/{identifier}/export")
            self.assertIn("44.64", exported.text)
            self.assertEqual(client.get("/terminal/").status_code, 200)
            self.assertEqual(client.get("/workbench/data/finance_cube.json").status_code, 200)

    def test_unknown_metric_and_future_cutoff_rejected(self):
        with TestClient(create_app(self.settings)) as client:
            for changes in [{"metric_ids": ["future_price"]}, {"as_of": "2026-09-25"}, {"compare_with": "MSFT"}, {"fiscal_year": 2010}, {"metric_ids": []}, {"mode": "live"}]:
                response = client.post("/api/terminal/research", json=request(**changes))
                self.assertEqual(response.status_code, 422, response.text)

    def test_paid_request_and_saved_result_require_server_token(self):
        settings = replace(self.settings, enable_live=True, api_token="test-server-token", deepseek_api_key="not-real")
        with TestClient(create_app(settings)) as client:
            response = client.post("/api/terminal/research", json=request(mode="snapshot"))
            self.assertEqual(response.status_code, 401)
            with patch("research_workbench.terminal_engine.synthesize", side_effect=ResearchError("MODEL_UNAVAILABLE", "测试离线")):
                response = client.post("/api/terminal/research", json=request(mode="snapshot"), headers={"Authorization": "Bearer test-server-token"})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(client.get("/api/research/" + response.json()["id"]).status_code, 401)


if __name__ == "__main__":
    unittest.main()
