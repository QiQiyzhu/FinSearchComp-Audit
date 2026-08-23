from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from threading import RLock
import time
from typing import Any, Protocol, Sequence
from uuid import uuid4

from advanced_rag.models import QuerySpec

from .failure import classify_decision, classify_exception
from .store import RunNotFound, RunStore, TERMINAL_STATUSES, canonical_json, payload_hash


class AnswerService(Protocol):
    def answer(self, query: QuerySpec, *, request_id: str | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.02

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")


class FinAgentPlatform:
    """Persistent asynchronous job facade for query, evaluation, and replay."""

    def __init__(
        self,
        answer_service: AnswerService,
        store: RunStore,
        *,
        max_workers: int = 4,
        retry_policy: RetryPolicy | None = None,
        dataset_version: str = "controlled-corpus-v1",
        pipeline_version: str = "atlas-rag-platform-v1",
        model_version: str = "deterministic-atlas-rag-v1",
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self.answer_service = answer_service
        self.store = store
        self.retry_policy = retry_policy or RetryPolicy()
        self.config = {
            "dataset_version": dataset_version,
            "pipeline_version": pipeline_version,
            "model_version": model_version,
        }
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="finagent-job"
        )
        self._futures: dict[str, Future[Any]] = {}
        self._lock = RLock()

    def submit_query(
        self,
        query: QuerySpec,
        *,
        idempotency_key: str | None = None,
        parent_run_id: str | None = None,
        force_new: bool = False,
    ) -> dict[str, Any]:
        query.validate()
        request = query.to_dict()
        run_key = self._run_key(
            "query",
            request,
            idempotency_key=idempotency_key,
            salt=(uuid4().hex if force_new else None),
        )
        run, created = self.store.create_or_get(
            run_key=run_key,
            kind="query",
            request=request,
            config=self.config,
            parent_run_id=parent_run_id,
        )
        if created:
            self._schedule(run["run_id"], self._execute_query_run, run["run_id"])
        return self._submission(run, created)

    def submit_evaluation(
        self,
        queries: Sequence[QuerySpec],
        *,
        name: str = "evaluation",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not queries:
            raise ValueError("evaluation requires at least one query")
        for query in queries:
            query.validate()
        request = {"name": name, "queries": [query.to_dict() for query in queries]}
        run_key = self._run_key(
            "evaluation", request, idempotency_key=idempotency_key
        )
        run, created = self.store.create_or_get(
            run_key=run_key,
            kind="evaluation",
            request=request,
            config=self.config,
        )
        if created:
            self._schedule(
                run["run_id"], self._execute_evaluation_run, run["run_id"]
            )
        return self._submission(run, created)

    def replay(self, run_id: str) -> dict[str, Any]:
        original = self.store.require(run_id)
        if original["kind"] != "query":
            raise ValueError("only query runs can be replayed")
        if original["status"] != "succeeded":
            raise ValueError("only succeeded query runs can be replayed")
        query = QuerySpec(**original["request"])
        return self.submit_query(query, parent_run_id=run_id, force_new=True)

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.require(run_id)
        if run["kind"] == "evaluation":
            run["children"] = [
                self._public_run(child) for child in self.store.list_children(run_id)
            ]
        return self._public_run(run)

    def get_trace(self, run_id: str) -> dict[str, Any]:
        run = self.store.require(run_id)
        if run["trace"] is None:
            return {
                "schema_version": "finagent-run-trace-1.0",
                "run_id": run_id,
                "status": run["status"],
                "events": [],
            }
        return run["trace"]

    def wait(self, run_id: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            run = self.get_run(run_id)
            if run["status"] in TERMINAL_STATUSES:
                return run
            time.sleep(0.01)
        raise TimeoutError(f"run did not finish within {timeout_seconds}s: {run_id}")

    def health(self) -> dict[str, Any]:
        with self._lock:
            active = sum(not future.done() for future in self._futures.values())
        return {
            "status": "ok",
            "stored_runs": self.store.count(),
            "active_jobs": active,
            "versions": dict(self.config),
        }

    def failure_analytics(self, evaluation_run_id: str) -> dict[str, Any]:
        parent = self.store.require(evaluation_run_id)
        if parent["kind"] != "evaluation":
            raise ValueError("failure analytics requires an evaluation run")
        children = self.store.list_children(evaluation_run_id)
        cases = [
            {
                "run_id": child["run_id"],
                "query_id": child["request"].get("query_id"),
                "question": child["request"].get("question"),
                "status": child["status"],
                "category": child["failure_category"],
            }
            for child in children
            if child["failure_category"]
        ]
        counts = Counter(case["category"] for case in cases)
        return {
            "evaluation_run_id": evaluation_run_id,
            "total_cases": len(children),
            "classified_cases": len(cases),
            "categories": dict(sorted(counts.items())),
            "cases": cases,
        }

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _schedule(self, run_id: str, function, *args: Any) -> None:
        with self._lock:
            future = self._executor.submit(function, *args)
            self._futures[run_id] = future
            future.add_done_callback(lambda _future: self._forget_future(run_id))

    def _forget_future(self, run_id: str) -> None:
        with self._lock:
            self._futures.pop(run_id, None)

    def _execute_query_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.transition(
            run_id, from_statuses=("queued",), to_status="running"
        )
        query = QuerySpec(**run["request"])
        events: list[dict[str, Any]] = []
        started = time.perf_counter()
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                response = self.answer_service.answer(query, request_id=run_id)
                self._validate_answer_response(response)
                decision = response["decision"]
                failure_category = classify_decision(decision)
                platform_latency_ms = round((time.perf_counter() - started) * 1000, 3)
                events.append(
                    {
                        "type": "attempt_succeeded",
                        "attempt": attempt,
                        "agent_action": decision["action"],
                    }
                )
                result = {
                    **json.loads(json.dumps(response, ensure_ascii=False)),
                    "platform_latency_ms": platform_latency_ms,
                    "attempts": attempt,
                    "failure_category": failure_category,
                }
                if run["parent_run_id"]:
                    result["replay_comparison"] = self._compare_replay(
                        run["parent_run_id"], result
                    )
                trace = self._trace_payload(run, events, decision.get("trace", []))
                return self.store.transition(
                    run_id,
                    from_statuses=("running",),
                    to_status="succeeded",
                    result=result,
                    trace=trace,
                    failure_category=failure_category,
                    attempts=attempt,
                )
            except Exception as error:  # converted into persisted failure state
                retrying = attempt < self.retry_policy.max_attempts
                events.append(
                    {
                        "type": "attempt_failed",
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                        "category": classify_exception(error),
                        "retrying": retrying,
                    }
                )
                if retrying:
                    delay = self.retry_policy.base_delay_seconds * (2 ** (attempt - 1))
                    if delay:
                        time.sleep(delay)
                    continue
                category = classify_exception(error)
                trace = self._trace_payload(run, events, [])
                return self.store.transition(
                    run_id,
                    from_statuses=("running",),
                    to_status="failed",
                    result={
                        "error": "pipeline_execution_failed",
                        "error_type": type(error).__name__,
                        "attempts": attempt,
                    },
                    trace=trace,
                    failure_category=category,
                    error_type=type(error).__name__,
                    attempts=attempt,
                )
        raise AssertionError("retry loop exited unexpectedly")

    def _execute_evaluation_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.transition(
            run_id, from_statuses=("queued",), to_status="running"
        )
        child_runs: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        started = time.perf_counter()
        try:
            for index, payload in enumerate(run["request"]["queries"]):
                child_key = self._run_key(
                    "query", payload, salt=f"parent:{run_id}:index:{index}"
                )
                child, created = self.store.create_or_get(
                    run_key=child_key,
                    kind="query",
                    request=payload,
                    config=self.config,
                    parent_run_id=run_id,
                )
                if created:
                    child = self._execute_query_run(child["run_id"])
                child_runs.append(child)
                events.append(
                    {
                        "type": "case_completed",
                        "index": index,
                        "child_run_id": child["run_id"],
                        "status": child["status"],
                    }
                )
            categories = Counter(
                child["failure_category"]
                for child in child_runs
                if child["failure_category"]
            )
            completed_results = [
                child["result"] for child in child_runs if child["result"] is not None
            ]
            result = {
                "name": run["request"]["name"],
                "total": len(child_runs),
                "succeeded": sum(child["status"] == "succeeded" for child in child_runs),
                "failed": sum(child["status"] == "failed" for child in child_runs),
                "answered": sum(
                    item.get("decision", {}).get("action") == "answer"
                    for item in completed_results
                ),
                "abstained": sum(
                    item.get("decision", {}).get("action") == "abstain"
                    for item in completed_results
                ),
                "failure_categories": dict(sorted(categories.items())),
                "child_run_ids": [child["run_id"] for child in child_runs],
                "platform_latency_ms": round(
                    (time.perf_counter() - started) * 1000, 3
                ),
            }
            trace = self._trace_payload(run, events, [])
            return self.store.transition(
                run_id,
                from_statuses=("running",),
                to_status="succeeded",
                result=result,
                trace=trace,
                attempts=1,
            )
        except Exception as error:
            events.append(
                {
                    "type": "evaluation_failed",
                    "error_type": type(error).__name__,
                    "category": classify_exception(error),
                }
            )
            return self.store.transition(
                run_id,
                from_statuses=("running",),
                to_status="failed",
                result={"error": "evaluation_failed", "error_type": type(error).__name__},
                trace=self._trace_payload(run, events, []),
                failure_category=classify_exception(error),
                error_type=type(error).__name__,
                attempts=1,
            )

    def _compare_replay(
        self, original_run_id: str, replay_result: dict[str, Any]
    ) -> dict[str, Any]:
        original = self.store.require(original_run_id)
        old_result = original["result"] or {}
        old_decision = old_result.get("decision", {})
        new_decision = replay_result.get("decision", {})
        old_core = {
            key: old_decision.get(key)
            for key in ("action", "answer_value", "unit", "selected_evidence_id")
        }
        new_core = {
            key: new_decision.get(key)
            for key in ("action", "answer_value", "unit", "selected_evidence_id")
        }
        return {
            "original_run_id": original_run_id,
            "same_decision": old_core == new_core,
            "same_agent_trace": old_decision.get("trace") == new_decision.get("trace"),
            "original_result_hash": payload_hash(old_core),
            "replay_result_hash": payload_hash(new_core),
        }

    def _trace_payload(
        self,
        run: dict[str, Any],
        execution_events: list[dict[str, Any]],
        agent_trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "finagent-run-trace-1.0",
            "run_id": run["run_id"],
            "parent_run_id": run["parent_run_id"],
            "kind": run["kind"],
            "request_hash": run["request_hash"],
            "versions": dict(run["config"]),
            "execution_events": execution_events,
            "agent_trace": json.loads(json.dumps(agent_trace, ensure_ascii=False)),
        }

    def _run_key(
        self,
        kind: str,
        request: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        salt: str | None = None,
    ) -> str:
        if idempotency_key is not None:
            if not idempotency_key.strip() or len(idempotency_key) > 200:
                raise ValueError("idempotency key must contain 1..200 characters")
            material = f"client:{idempotency_key}"
        else:
            material = canonical_json(
                {"kind": kind, "request": request, "config": self.config, "salt": salt}
            )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_answer_response(response: Any) -> None:
        if not isinstance(response, dict) or not isinstance(response.get("decision"), dict):
            raise ValueError("answer service returned no decision object")
        if response["decision"].get("action") not in {"answer", "abstain"}:
            raise ValueError("answer service returned an invalid action")

    @staticmethod
    def _submission(run: dict[str, Any], created: bool) -> dict[str, Any]:
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "kind": run["kind"],
            "deduplicated": not created,
        }

    @staticmethod
    def _public_run(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in run.items()
            if key not in {"run_key", "request_hash"}
        }
