from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest

from fastapi.testclient import TestClient

from advanced_rag.models import QuerySpec
from advanced_rag.server import build_service

from .api import create_app
from .platform import FinAgentPlatform, RetryPolicy
from .store import IdempotencyConflict, InvalidTransition, RunStore


def query_spec(query_id: str = "platform-case", question: str = "Apple FY2024 revenue?") -> QuerySpec:
    return QuerySpec(
        query_id=query_id,
        question=question,
        cutoff_date="2025-01-01",
        target_period="FY2024",
        required_version="final",
        canonical_unit="usd_million",
    )


def answer_payload(*, action: str = "answer") -> dict:
    if action == "answer":
        decision = {
            "action": "answer",
            "answer_value": "100",
            "unit": "usd_million",
            "selected_evidence_id": "doc-safe",
            "rejection_reasons": [],
            "trace": [{"state": "ANSWER", "selected_evidence_id": "doc-safe"}],
        }
    else:
        decision = {
            "action": "abstain",
            "answer_value": None,
            "unit": None,
            "selected_evidence_id": None,
            "rejection_reasons": ["检索未返回候选证据"],
            "trace": [{"state": "RETRIEVE", "returned": 0}, {"state": "ABSTAIN"}],
        }
    return {"request_id": None, "cached": False, "latency_ms": 1.0, "decision": decision}


class ConfigurableAnswerService:
    def __init__(self, *, failures_before_success: int = 0, always_fail: bool = False) -> None:
        self.failures_before_success = failures_before_success
        self.always_fail = always_fail
        self.calls = 0

    def answer(self, query: QuerySpec, *, request_id: str | None = None) -> dict:
        self.calls += 1
        if self.always_fail or self.calls <= self.failures_before_success:
            raise TimeoutError("injected upstream timeout")
        payload = answer_payload(action="abstain" if "missing" in query.question else "answer")
        payload["request_id"] = request_id
        return payload


class RunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = RunStore(f"{self.directory.name}/runs.sqlite3")

    def test_concurrent_create_or_get_produces_one_run(self) -> None:
        def create(_index: int):
            return self.store.create_or_get(
                run_key="same-key",
                kind="query",
                request={"question": "same"},
                config={"pipeline_version": "v1"},
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(16)))
        self.assertEqual(len({run["run_id"] for run, _created in results}), 1)
        self.assertEqual(sum(created for _run, created in results), 1)
        self.assertEqual(self.store.count(), 1)

    def test_idempotency_key_conflict_is_detected(self) -> None:
        self.store.create_or_get(
            run_key="client-key",
            kind="query",
            request={"question": "first"},
            config={"pipeline_version": "v1"},
        )
        with self.assertRaises(IdempotencyConflict):
            self.store.create_or_get(
                run_key="client-key",
                kind="query",
                request={"question": "changed"},
                config={"pipeline_version": "v1"},
            )

    def test_invalid_state_transition_fails_without_overwrite(self) -> None:
        run, _ = self.store.create_or_get(
            run_key="transition",
            kind="query",
            request={"question": "x"},
            config={"pipeline_version": "v1"},
        )
        with self.assertRaises(InvalidTransition):
            self.store.transition(
                run["run_id"], from_statuses=("running",), to_status="succeeded"
            )
        self.assertEqual(self.store.require(run["run_id"])["status"], "queued")


class FinAgentPlatformTests(unittest.TestCase):
    def make_platform(
        self,
        service=None,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> FinAgentPlatform:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        platform = FinAgentPlatform(
            service or ConfigurableAnswerService(),
            RunStore(f"{directory.name}/runs.sqlite3"),
            retry_policy=retry_policy,
        )
        self.addCleanup(platform.close)
        return platform

    def test_duplicate_query_returns_existing_run(self) -> None:
        platform = self.make_platform()
        first = platform.submit_query(query_spec())
        second = platform.submit_query(query_spec())
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        completed = platform.wait(first["run_id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["attempts"], 1)

    def test_trace_persists_versions_and_agent_states(self) -> None:
        platform = self.make_platform()
        run_id = platform.submit_query(query_spec())["run_id"]
        platform.wait(run_id)
        trace = platform.get_trace(run_id)
        self.assertEqual(trace["schema_version"], "finagent-run-trace-1.0")
        self.assertEqual(trace["versions"]["pipeline_version"], "atlas-rag-platform-v1")
        self.assertEqual(trace["agent_trace"][-1]["state"], "ANSWER")
        self.assertEqual(trace["execution_events"][-1]["type"], "attempt_succeeded")

    def test_timeout_recovers_with_bounded_exponential_retry(self) -> None:
        service = ConfigurableAnswerService(failures_before_success=2)
        platform = self.make_platform(
            service, retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0)
        )
        run = platform.wait(platform.submit_query(query_spec())["run_id"])
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["attempts"], 3)
        self.assertEqual(service.calls, 3)
        events = platform.get_trace(run["run_id"])["execution_events"]
        self.assertEqual([event["type"] for event in events], [
            "attempt_failed", "attempt_failed", "attempt_succeeded"
        ])

    def test_terminal_timeout_is_saved_instead_of_crashing_worker(self) -> None:
        service = ConfigurableAnswerService(always_fail=True)
        platform = self.make_platform(
            service, retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0)
        )
        run = platform.wait(platform.submit_query(query_spec())["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["failure_category"], "upstream_timeout")
        self.assertEqual(run["error_type"], "TimeoutError")
        self.assertEqual(run["result"]["attempts"], 2)

    def test_batch_evaluation_saves_child_runs_and_failure_analytics(self) -> None:
        platform = self.make_platform()
        submitted = platform.submit_evaluation(
            [query_spec("ok"), query_spec("missing", "missing evidence")],
            name="fault-mix",
        )
        evaluation = platform.wait(submitted["run_id"])
        self.assertEqual(evaluation["result"]["total"], 2)
        self.assertEqual(evaluation["result"]["answered"], 1)
        self.assertEqual(evaluation["result"]["abstained"], 1)
        self.assertEqual(len(evaluation["children"]), 2)
        analytics = platform.failure_analytics(submitted["run_id"])
        self.assertEqual(analytics["categories"], {"empty_retrieval": 1})
        self.assertEqual(analytics["cases"][0]["query_id"], "missing")

    def test_replay_creates_child_and_compares_deterministic_result(self) -> None:
        platform = self.make_platform()
        original_id = platform.submit_query(query_spec())["run_id"]
        platform.wait(original_id)
        replay_id = platform.replay(original_id)["run_id"]
        self.assertNotEqual(replay_id, original_id)
        replay = platform.wait(replay_id)
        self.assertEqual(replay["parent_run_id"], original_id)
        self.assertTrue(replay["result"]["replay_comparison"]["same_decision"])
        self.assertTrue(replay["result"]["replay_comparison"]["same_agent_trace"])

    def test_real_atlas_pipeline_runs_through_persistent_job_layer(self) -> None:
        platform = self.make_platform(build_service())
        query = QuerySpec(
            query_id="sp500_max_month",
            question="从2010年1月到2025年4月，标普500指数最大的单月涨幅是多少？",
            cutoff_date="2025-05-01",
            target_period="2010-01/2025-04",
            required_version="final",
            canonical_unit="percent",
        )
        run = platform.wait(platform.submit_query(query)["run_id"])
        self.assertEqual(run["status"], "succeeded")
        self.assertIn(run["result"]["decision"]["action"], {"answer", "abstain"})
        self.assertTrue(run["trace"]["agent_trace"])


class FastAPIContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.platform = FinAgentPlatform(
            ConfigurableAnswerService(),
            RunStore(f"{self.directory.name}/api.sqlite3"),
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
        )
        self.client = TestClient(create_app(self.platform))

    def tearDown(self) -> None:
        self.client.close()
        self.platform.close()
        self.directory.cleanup()

    @staticmethod
    def body(question: str = "Apple FY2024 revenue?") -> dict:
        return {
            "query_id": "api-case",
            "question": question,
            "as_of": "2025-01-01",
            "target_period": "FY2024",
            "required_version": "final",
            "canonical_unit": "usd_million",
        }

    def test_query_run_trace_and_replay_contract(self) -> None:
        response = self.client.post(
            "/api/v1/query",
            json=self.body(),
            headers={"Idempotency-Key": "api-idem-1"},
        )
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        self.platform.wait(run_id)
        run = self.client.get(f"/api/v1/runs/{run_id}")
        trace = self.client.get(f"/api/v1/runs/{run_id}/trace")
        replay = self.client.post(f"/api/v1/runs/{run_id}/replay")
        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.json()["status"], "succeeded")
        self.assertEqual(trace.json()["schema_version"], "finagent-run-trace-1.0")
        self.assertEqual(replay.status_code, 202)

    def test_reused_key_with_changed_payload_returns_conflict(self) -> None:
        headers = {"Idempotency-Key": "same-client-operation"}
        first = self.client.post("/api/v1/query", json=self.body(), headers=headers)
        second = self.client.post(
            "/api/v1/query", json=self.body("changed question"), headers=headers
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)

    def test_schema_rejects_unknown_fields_and_missing_runs_return_404(self) -> None:
        invalid = {**self.body(), "arbitrary_python": "print('no')"}
        self.assertEqual(self.client.post("/api/v1/query", json=invalid).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/runs/not-found").status_code, 404)

    def test_evaluation_and_failure_analytics_contract(self) -> None:
        payload = {
            "name": "api-eval",
            "queries": [self.body(), {**self.body("missing evidence"), "query_id": "missing"}],
        }
        response = self.client.post("/api/v1/evaluations", json=payload)
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        self.platform.wait(run_id)
        analytics = self.client.get(f"/api/v1/evaluations/{run_id}/failures")
        self.assertEqual(analytics.status_code, 200)
        self.assertEqual(analytics.json()["categories"], {"empty_retrieval": 1})


if __name__ == "__main__":
    unittest.main()
