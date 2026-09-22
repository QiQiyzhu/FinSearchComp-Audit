from __future__ import annotations

from dataclasses import replace
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from research_workbench.api import create_app
from research_workbench.config import Settings
from research_workbench.engine import validate_citations
from research_workbench.engine import ResearchEngine
from research_workbench.store import AdmissionError, JobStore
from research_workbench.workflows import WorkflowEngine, comparison_rows, export_report, issuer_question
from research_workbench.sources import now


def request(**changes):
    return {"question": "比较 Microsoft 和 Apple FY2024 的收入和营业利润率。", "ticker": "MSFT", "compare_with": "AAPL", "as_of": "2024-11-01", "mode": "demo", **changes}


def ratio_company(ticker, value, numerator, denominator):
    evidence = [{"id": ticker + "-E01", "metric": "operating_income", "value": str(numerator)},
                {"id": ticker + "-E02", "metric": "revenue", "value": str(denominator)}]
    for row in evidence:
        row.update(period_start="2023-01-01", period_end="2023-12-31", unit="USD")
    return {"ticker": ticker, "evidence": evidence,
            "metrics": [{"id": "operating_margin", "label": "营业利润率", "value": value, "display_value": value + "%", "unit": "%", "period_start": "2023-01-01", "period_end": "2023-12-31", "formula": f"{numerator} / {denominator} × 100", "evidence_ids": [row["id"] for row in evidence]}]}


class ComparisonTests(unittest.TestCase):
    def test_real_issuer_reports_preserve_citations_and_do_not_rank_different_windows(self):
        report = WorkflowEngine(Settings()).run(request())
        self.assertEqual(report["report_type"], "comparison")
        self.assertEqual(len(report["companies"]), 2)
        self.assertEqual(len({row["id"] for row in report["evidence"]}), len(report["evidence"]))
        self.assertTrue(validate_citations(report["claims"], report["evidence"], request()["as_of"]))
        answer_ids = {answer["id"] for answer in report["answers"]}
        self.assertTrue(all(claim["answer_id"] in answer_ids for claim in report["claims"] if claim.get("answer_id")))
        self.assertFalse(any(row["comparable"] for row in report["comparison"]["rows"]))
        self.assertTrue(all(row["difference"] is None for row in report["comparison"]["rows"]))
        revenue = next(row for row in report["comparison"]["rows"] if row["metric_id"] == "revenue")
        self.assertEqual(revenue["left"]["value"], "245122000000")
        self.assertEqual(revenue["right"]["value"], "391035000000")
        self.assertEqual(revenue["left"]["period_end"], "2024-06-30")
        self.assertEqual(revenue["right"]["period_end"], "2024-09-28")
        self.assertIn("MSFT-E01", export_report(report))
        self.assertIn("AAPL-E01", export_report(report))

    def test_same_period_margin_difference_is_percentage_points_not_percent_growth(self):
        row = comparison_rows([ratio_company("GOOGL", "27.00", 27, 100), ratio_company("META", "35.00", 35, 100)])[0]
        self.assertTrue(row["comparable"])
        self.assertEqual(row["difference"]["value"], "-8.00")
        self.assertEqual(row["difference"]["unit"], "percentage_points")
        self.assertEqual(len(row["difference"]["evidence_ids"]), 4)

    def test_ratio_difference_uses_original_operands_before_rounding(self):
        row = comparison_rows([ratio_company("A", "16.67", 1, 6), ratio_company("B", "33.33", 1, 3)])[0]
        self.assertEqual(row["difference"]["value"], "-16.67")
        self.assertIn("1 / 6", row["difference"]["formula"])
        self.assertIn("1 / 3", row["difference"]["formula"])

    def test_missing_original_ratio_operands_does_not_use_rounded_display_values(self):
        left, right = ratio_company("A", "16.67", 1, 6), ratio_company("B", "33.33", 1, 3)
        left["evidence"] = []
        row = comparison_rows([left, right])[0]
        self.assertFalse(row["comparable"])
        self.assertIsNone(row["difference"])

    def test_failed_model_calls_are_not_labeled_not_requested(self):
        class UnavailableModelEngine:
            def run(self, payload, emit):
                report = ResearchEngine(Settings()).run({**payload, "mode": "demo"}, emit)
                report["model"].update(used=False, status="unavailable")
                return report
        report = WorkflowEngine(Settings(), base=UnavailableModelEngine()).run(request())
        self.assertEqual(report["model"]["status"], "unavailable")

    def test_missing_capex_is_not_zero_in_nvidia_comparison(self):
        report = WorkflowEngine(Settings()).run(request(compare_with="NVDA", question="比较微软与英伟达 FY2024 自由现金流。"))
        row = next(row for row in report["comparison"]["rows"] if row["metric_id"] == "free_cash_flow")
        self.assertIsNone(row["right"])
        self.assertIsNone(row["difference"])
        self.assertFalse(report["coverage"]["question_supported"])

    def test_issuer_removal_preserves_other_company_and_financial_request(self):
        cleaned = issuer_question("Compare Microsoft and Apple FY2024 revenue with NVDA forecasts", ["MSFT", "AAPL"])
        self.assertIn("NVDA", cleaned)
        self.assertIn("FY2024 revenue", cleaned)
        self.assertNotIn("Microsoft", cleaned)

    def test_compare_budget_counts_two_issuer_runs_and_idempotency_does_not_rebill(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = replace(Settings(), database=Path(folder) / "test.sqlite3", live_global_per_day=3, live_requests_per_hour=3)
            store = JobStore(settings)
            first, fresh = store.create(request(mode="snapshot"), "client", "key")
            same, fresh_again = store.create(request(mode="snapshot"), "client", "key")
            self.assertTrue(fresh)
            self.assertFalse(fresh_again)
            self.assertEqual(first["id"], same["id"])
            with self.assertRaises(AdmissionError):
                store.create(request(mode="snapshot"), "client", "next")
            single = request(mode="snapshot", compare_with=None)
            store.create(single, "client", "single")
            with self.assertRaises(AdmissionError):
                store.create(single, "another-client", "daily-limit")

    def test_v1_database_migration_preserves_existing_admissions(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "v1.sqlite3"
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE research_admissions (job_id TEXT PRIMARY KEY, client_hash TEXT NOT NULL, mode TEXT NOT NULL, created_at TEXT NOT NULL)")
                db.execute("INSERT INTO research_admissions VALUES ('old-job','client','snapshot',?)", (now(),))
                db.commit()
            store = JobStore(replace(Settings(), database=database, live_requests_per_hour=2))
            with store.connection() as db:
                self.assertEqual(db.execute("SELECT units FROM research_admissions WHERE job_id='old-job'").fetchone()[0], 1)
            with self.assertRaises(AdmissionError):
                store.create(request(mode="snapshot"), "client", "new-comparison")

    def test_api_comparison_job_and_export_validate_distinct_issuers(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = replace(Settings(), database=Path(folder) / "test.sqlite3")
            with TestClient(create_app(settings)) as client:
                self.assertEqual(client.post("/api/research", json=request(compare_with="MSFT")).status_code, 422)
                self.assertEqual(client.post("/api/research", json=request(compare_with="GOOGL")).status_code, 422)
                created = client.post("/api/research", json=request())
                self.assertEqual(created.status_code, 202)
                identifier = created.json()["id"]
                for _ in range(150):
                    job = client.get("/api/research/" + identifier).json()
                    if job["status"] in {"completed", "failed"}:
                        break
                    time.sleep(.02)
                self.assertEqual(job["status"], "completed", job.get("error"))
                response = client.get("/api/research/" + identifier + "/export?format=markdown")
                self.assertEqual(response.status_code, 200)
                self.assertIn("年度财务对比", response.text)
                self.assertTrue(client.get("/api/config").json()["features"]["comparison"])


if __name__ == "__main__":
    unittest.main()
