from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from research_workbench.api import EXAMPLES, create_app
from research_workbench.config import Settings
from research_workbench.engine import ResearchEngine, calculate, markdown_export, plan_question, synthesize, validate_citations, validate_selection
from research_workbench.sources import DATA_DIR, ResearchError, Source, SourceClient, annual_candidates, choose_fact, digest, select_facts
from research_workbench.store import AdmissionError, JobService, JobStore


def request(**changes):
    return {"question": "分析微软 FY2024 收入与现金流，计算自由现金流。", "ticker": "MSFT", "as_of": "2024-11-01", "mode": "demo", **changes}


def fact(value, *, start="2023-07-01", end="2024-06-30", filed="2024-07-30", form="10-K", **changes):
    return {"start": start, "end": end, "val": value, "filed": filed, "form": form, "accn": "0000950170-24-087843", **changes}


def payload(rows, others=None):
    return {"cik": 789019, "entityName": "Microsoft", "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rows}}, **(others or {})}}}


class FinancialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.engine = ResearchEngine(self.settings)

    def test_three_snapshots_match_independently_audited_annual_reports(self):
        expected = {
            "MSFT": {"revenue": "245122000000", "operating_income": "109433000000", "operating_cash_flow": "118548000000", "capital_expenditure": "44477000000", "free_cash_flow": "74071000000"},
            "AAPL": {"revenue": "391035000000", "operating_income": "123216000000", "operating_cash_flow": "118254000000", "capital_expenditure": "9447000000", "free_cash_flow": "108807000000"},
            "NVDA": {"revenue": "60922000000", "operating_income": "32972000000", "operating_cash_flow": "28090000000"},
        }
        for ticker, values in expected.items():
            with self.subTest(ticker=ticker):
                report = self.engine.run(request(ticker=ticker, question="分析 FY2024 年度收入与经营现金流。"))
                metrics = {row["id"]: row for row in report["metrics"]}
                for metric, value in values.items():
                    self.assertEqual(metrics[metric]["value"], value)
                self.assertTrue(validate_citations(report["claims"], report["evidence"], "2024-11-01"))
                self.assertFalse(report["model"]["used"])

    def test_decimal_growth_margin_and_fcf_are_exact_and_cited(self):
        report = self.engine.run(request())
        metrics = {row["id"]: row for row in report["metrics"]}
        self.assertEqual(metrics["revenue"]["change_pct"], "15.67")
        self.assertEqual(metrics["operating_margin"]["value"], "44.64")
        self.assertEqual(metrics["free_cash_flow"]["value"], "74071000000")
        # v2 also exposes FCF trend, whose comparison needs two additional facts.
        self.assertEqual(len(metrics["free_cash_flow"]["value_evidence_ids"]), 2)
        self.assertEqual(len(metrics["free_cash_flow"]["evidence_ids"]), 4)
        self.assertIn("74.07", report["summary"])
        self.assertIn("118548000000", metrics["free_cash_flow"]["formula"])

    def test_nvidia_capex_definition_gap_abstains(self):
        report = self.engine.run(request(ticker="NVDA", question="分析 NVIDIA FY2024 现金流与资本支出，计算自由现金流。"))
        self.assertNotIn("capital_expenditure", {metric["id"] for metric in report["metrics"]})
        self.assertNotIn("free_cash_flow", {metric["id"] for metric in report["metrics"]})
        self.assertFalse(report["coverage"]["question_supported"])
        self.assertEqual(report["verdict"]["stance"], "insufficient_evidence")
        self.assertIn("未覆盖", report["summary"])

    def test_capture_hashes_and_exact_upstream_rows(self):
        bundle = json.loads((DATA_DIR / "demo_companyfacts.json").read_text(encoding="utf-8"))
        for ticker, record in bundle["companies"].items():
            raw = gzip.decompress((DATA_DIR / "upstream" / f"{ticker}.json.gz").read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record["upstream_sha256"])
            self.assertEqual(digest(record["payload"]), record["sha256"])
            upstream = json.loads(raw)
            for tag, concept in record["payload"]["facts"]["us-gaap"].items():
                originals = upstream["facts"]["us-gaap"][tag]["units"]["USD"]
                for row in concept["units"]["USD"]:
                    self.assertIn(row, originals)
                    self.assertLessEqual(row["filed"], bundle["cutoff"])

    def test_filing_cutoff_blocks_one_day_before_release(self):
        report = self.engine.run(request(as_of="2024-07-29"))
        self.assertEqual(report["metrics"], [])
        self.assertEqual(report["evidence"], [])
        self.assertEqual(report["verdict"]["stance"], "insufficient_evidence")
        on_filing_day = self.engine.run(request(as_of="2024-07-30"))
        self.assertTrue(on_filing_day["metrics"])

    def test_filters_end_dates_duration_form_and_invalid_values(self):
        rows = [fact(100), fact(200, filed="2024-08-01"), fact(300, end="2025-06-30", filed="2024-07-30"),
                fact(400, start="2024-04-01", form="10-Q"), fact("NaN"), fact("Infinity"), fact(-1), fact(900, form="8-K")]
        accepted = annual_candidates(payload(rows), "revenue", "2024-07-30")
        self.assertEqual([row["value"] for row in accepted], ["100"])

    def test_latest_eligible_restatement_and_conflicts(self):
        rows = [fact(100), fact(110, filed="2024-08-10", form="10-K/A")]
        self.assertEqual(choose_fact(annual_candidates(payload(rows), "revenue", "2024-08-01"), ("2023-07-01", "2024-06-30"))["value"], "100")
        self.assertEqual(choose_fact(annual_candidates(payload(rows), "revenue", "2024-08-10"), ("2023-07-01", "2024-06-30"))["value"], "110")
        rows.append(fact(111, filed="2024-08-10", form="10-K/A"))
        self.assertIsNone(choose_fact(annual_candidates(payload(rows), "revenue", "2024-08-10"), ("2023-07-01", "2024-06-30")))

    def test_replacement_tag_and_exact_period_alignment(self):
        data = payload([fact(50, start="2022-07-01", end="2023-06-30", filed="2023-07-30")], {
            "Revenues": {"units": {"USD": [fact(100)]}},
            "OperatingIncomeLoss": {"units": {"USD": [fact(20, start="2023-07-02")]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [fact(-1)]}},
        })
        source = Source(data, "https://data.sec.gov/source", "2024-08-01T00:00:00Z", digest(data), "live")
        facts, _, missing = select_facts(source, "MSFT", "2024-08-01")
        self.assertEqual(facts["revenue"]["value"], "100")
        self.assertNotIn("operating_income", facts)
        self.assertIn("capital_expenditure", missing)
        self.assertNotIn("operating_margin", {metric["id"] for metric in calculate(facts)[0]})

    def test_unknown_citations_and_future_source_rejected(self):
        claim = [{"evidence_ids": ["E01"]}]
        source = [{"id": "E01", "published_at": "2024-08-01", "period_end": "2024-06-30"}]
        self.assertFalse(validate_citations(claim, source, "2024-07-31"))
        self.assertFalse(validate_citations([{"evidence_ids": ["E99"]}], source, "2024-08-01"))
        self.assertFalse(validate_citations([{"evidence_ids": []}], source, "2024-08-01"))

    def test_scope_abstention_is_question_specific(self):
        for question in ["微软明天股价目标价是多少", "微软的市盈率是否低估", "微软2024年的资产负债率是多少", "微软2024年iPhone销量是多少", "今天上海天气怎么样", "请给我写一首诗歌", "微软Q1季度收入增长多少", "比较微软和苹果的收入", "分析微软2020到2024收入变化"]:
            with self.subTest(question=question):
                report = self.engine.run(request(question=question))
                self.assertFalse(report["coverage"]["question_supported"])
                self.assertEqual(report["verdict"]["stance"], "insufficient_evidence")

    def test_iso_cutoff_in_question_is_not_fiscal_year(self):
        self.assertIsNone(plan_question("截至2024-01-01微软最新业绩", "MSFT")["fiscal_year"])
        self.assertEqual(plan_question("截至2024-11-01复核微软FY2023营收", "MSFT")["fiscal_year"], 2023)

    def test_missing_year_does_not_silently_substitute_latest(self):
        report = self.engine.run(request(question="分析微软 FY2022 经营现金流。"))
        self.assertEqual(report["metrics"], [])
        self.assertIn("2022", report["summary"])

    def test_markdown_contains_exact_evidence_and_limitations(self):
        report = self.engine.run(request())
        exported = markdown_export(report)
        self.assertIn("https://www.sec.gov/Archives/edgar/", exported)
        self.assertIn(report["evidence"][0]["sha256"], exported)
        self.assertIn("74.07", exported)
        self.assertIn("缺口", exported)


class ModelGroundingTests(unittest.TestCase):
    def test_only_existing_ids_and_allowed_keys_can_cross_boundary(self):
        claims = [{"id": "C01", "metric_id": "revenue"}]
        self.assertEqual(validate_selection({"claim_ids": ["C01"], "watch_ids": ["growth"]}, claims)["claim_ids"], ["C01"])
        for output in [{"claim_ids": ["C99"], "watch_ids": ["growth"]},
                       {"claim_ids": ["C01"], "watch_ids": ["buy_now"]},
                       {"claim_ids": ["C01"], "watch_ids": ["growth"], "forecast": "price doubles"},
                       {"claim_ids": ["C01", "C01"], "watch_ids": ["growth"]},
                       {"claim_ids": [], "watch_ids": ["growth"]}]:
            with self.assertRaises(ResearchError):
                validate_selection(output, claims)

    def test_deepseek_transport_receipt_and_grounding(self):
        claims = [{"id": "C01", "metric_id": "revenue", "text": "Verified revenue", "evidence_ids": ["E01"]}]
        plan = {"metric_slots": ["revenue"], "focus": "growth"}
        settings = Settings(deepseek_api_key="not-a-real-secret")
        response = MagicMock(status_code=200, content=b"small")
        response.json.return_value = {"id": "test-receipt", "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}, "choices": [{"message": {"content": json.dumps({"claim_ids": ["C01"], "watch_ids": ["growth"]})}}]}
        with patch("research_workbench.engine.httpx.Client") as factory:
            factory.return_value.__enter__.return_value.post.return_value = response
            result = synthesize(settings, "revenue growth", plan, claims)
            sent = factory.return_value.__enter__.return_value.post.call_args.kwargs
            self.assertEqual(sent["json"]["thinking"], {"type": "disabled"})
            self.assertEqual(result["receipt"]["usage"]["total_tokens"], 120)
            self.assertEqual(result["receipt"]["request_id"], "test-receipt")
            self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_model_cannot_omit_required_metric(self):
        claims = [{"id": "C01", "metric_id": "revenue"}, {"id": "C02", "metric_id": "free_cash_flow"}]
        response = MagicMock(status_code=200, content=b"small")
        response.json.return_value = {"choices": [{"message": {"content": '{"claim_ids":["C01"],"watch_ids":["growth"]}'}}]}
        with patch("research_workbench.engine.httpx.Client") as factory:
            factory.return_value.__enter__.return_value.post.return_value = response
            with self.assertRaises(ResearchError) as raised:
                synthesize(Settings(), "free cash flow", {"metric_slots": [], "focus": "cashflow"}, claims)
            self.assertEqual(raised.exception.code, "MODEL_GROUNDING_REJECTED")

    def test_rejected_model_does_not_fake_live_success(self):
        engine = ResearchEngine(Settings(deepseek_api_key="placeholder"))
        with patch("research_workbench.engine.synthesize", side_effect=ResearchError("MODEL_GROUNDING_REJECTED", "Rejected unsupported model facts")):
            report = engine.run(request(mode="snapshot"))
        self.assertFalse(report["model"]["used"])
        self.assertEqual(report["model"]["status"], "rejected")
        self.assertIn("74.07", report["summary"])
        self.assertTrue(any("Rejected" in gap for gap in report["limitations"]))

    def test_unsupported_scope_does_not_call_billed_model(self):
        engine = ResearchEngine(Settings(deepseek_api_key="placeholder"))
        with patch("research_workbench.engine.synthesize") as mocked:
            engine.run(request(mode="snapshot", question="微软明天目标股价是多少？"))
        mocked.assert_not_called()


class JobAndAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(database=Path(self.directory.name) / "jobs.sqlite3", cache_dir=Path(self.directory.name) / "cache")

    def tearDown(self):
        self.directory.cleanup()

    def wait_result(self, client, identifier, headers=None):
        for _ in range(150):
            response = client.get(f"/api/research/{identifier}", headers=headers)
            self.assertEqual(response.status_code, 200)
            job = response.json()
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(.02)
        self.fail("job did not complete")

    def test_async_demo_events_exports_and_persisted_read(self):
        with TestClient(create_app(self.settings)) as client:
            response = client.post("/api/research", json=request(), headers={"Idempotency-Key": "smoke"})
            self.assertEqual(response.status_code, 202)
            identifier = response.json()["id"]
            job = self.wait_result(client, identifier)
            self.assertEqual(job["status"], "completed")
            self.assertGreater(len(client.get(f"/api/research/{identifier}/events").json()["events"]), 5)
            markdown = client.get(f"/api/research/{identifier}/export?format=markdown")
            self.assertIn("74.07", markdown.text)
            self.assertIn("attachment", markdown.headers["content-disposition"])
            self.assertEqual(client.get(f"/api/research/{identifier}/export?format=json").json()["ticker"], "MSFT")
            duplicate = client.post("/api/research", json=request(), headers={"Idempotency-Key": "smoke"})
            self.assertEqual(duplicate.json()["id"], identifier)
            conflict = client.post("/api/research", json=request(question="分析 FY2023 微软经营现金流。"), headers={"Idempotency-Key": "smoke"})
            self.assertEqual(conflict.status_code, 409)
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(restarted.get(f"/api/research/{identifier}").json()["status"], "completed")

    def test_unconfigured_live_fails_before_job_creation(self):
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.post("/api/research", json=request(mode="live")).status_code, 503)
            self.assertEqual(client.post("/api/research", json=request(mode="snapshot")).status_code, 503)
            self.assertFalse(client.get("/api/config").json()["modes"]["live"]["available"])

    def test_private_modes_auth_and_no_secret_config(self):
        config = replace(self.settings, enable_live=True, api_token="test-access-secret", deepseek_api_key="test-provider-secret")
        engine = MagicMock()
        engine.run.return_value = ResearchEngine(self.settings).run(request())
        with TestClient(create_app(config, engine=engine)) as client:
            self.assertEqual(client.post("/api/research", json=request(mode="snapshot")).status_code, 401)
            self.assertEqual(client.post("/api/research", json=request(mode="snapshot"), headers={"Authorization": "Bearer wrong"}).status_code, 401)
            headers = {"Authorization": "Bearer test-access-secret"}
            accepted = client.post("/api/research", json=request(mode="snapshot"), headers=headers)
            self.assertEqual(accepted.status_code, 202)
            identifier = accepted.json()["id"]
            self.assertEqual(self.wait_result(client, identifier, headers)["status"], "completed")
            for suffix in ["", "/events", "/export?format=json"]:
                self.assertEqual(client.get(f"/api/research/{identifier}" + suffix).status_code, 401)
            exposed = client.get("/api/config").text
            self.assertNotIn("test-access-secret", exposed)
            self.assertNotIn("test-provider-secret", exposed)

    def test_input_contract_and_actual_body_bound(self):
        with TestClient(create_app(self.settings)) as client:
            for invalid in [request(question="    "), request(question="x" * 2001), request(ticker="UNKNOWN"), request(ticker="AMZN"), request(as_of=str(date.today() + timedelta(days=1))), {**request(), "api_key": "untrusted"}]:
                self.assertEqual(client.post("/api/research", json=invalid).status_code, 422)
            chunked = client.post("/api/research", content=iter([b"x" * 9000, b"x" * 9000]), headers={"Content-Type": "application/json"})
            self.assertEqual(chunked.status_code, 413)
            self.assertEqual(client.get("/api/research/not-found").status_code, 404)

    def test_bounded_capacity_idempotency_and_global_budget_are_atomic(self):
        store = JobStore(replace(self.settings, max_pending=1, live_global_per_day=1))
        job, created = store.create(request(mode="snapshot"), "client-a", "key")
        self.assertTrue(created)
        self.assertFalse(store.create(request(mode="snapshot"), "client-a", "key")[1])
        with self.assertRaises(AdmissionError) as full:
            store.create(request(), "client-b", None)
        self.assertEqual(full.exception.status, 429)
        store.fail(job["id"], "TEST", "test")
        with self.assertRaises(AdmissionError) as budget:
            store.create(request(mode="snapshot"), "different-client", None)
        self.assertEqual(budget.exception.status, 429)
        # Offline access remains available after the billed budget is spent.
        self.assertTrue(store.create(request(), "client-b", None)[1])

    def test_restart_interrupts_running_without_rebilling_and_resumes_queued(self):
        store = JobStore(self.settings)
        interrupted, _ = store.create(request(), "client", None)
        queued, _ = store.create(request(), "client", None)
        store.claim(interrupted["id"])
        service = JobService(self.settings)
        service.start()
        service.close()
        self.assertEqual(service.store.get(interrupted["id"])["error"]["code"], "PROCESS_INTERRUPTED")
        # shutdown may cancel work not yet started, preserving its queued state;
        # next startup can safely resume it without a repeated paid invocation.
        self.assertIn(service.store.get(queued["id"])["status"], {"queued", "completed"})
        if service.store.get(queued["id"])["status"] == "queued":
            with TestClient(create_app(self.settings)) as client:
                self.assertEqual(self.wait_result(client, queued["id"])["status"], "completed")

    def test_source_failure_is_a_failed_job_never_demo_fallback(self):
        engine = MagicMock()
        engine.run.side_effect = ResearchError("SEC_UNAVAILABLE", "Official source unavailable")
        config = replace(self.settings, enable_live=True, public_live=True, sec_user_agent="test identity")
        with TestClient(create_app(config, engine=engine)) as client:
            identifier = client.post("/api/research", json=request(mode="live")).json()["id"]
            result = self.wait_result(client, identifier)
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["result"])
            self.assertEqual(result["error"]["code"], "SEC_UNAVAILABLE")
            self.assertEqual(client.get(f"/api/research/{identifier}/export").status_code, 409)

    def test_cors_is_explicit(self):
        with TestClient(create_app(replace(self.settings, allowed_origins=("https://example.github.io",)))) as client:
            allowed = client.options("/api/research", headers={"Origin": "https://example.github.io", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
            self.assertEqual(allowed.headers["access-control-allow-origin"], "https://example.github.io")
            denied = client.options("/api/research", headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"})
            self.assertNotIn("access-control-allow-origin", denied.headers)


if __name__ == "__main__":
    unittest.main()
