from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from threading import Thread
import unittest
from urllib.request import Request, urlopen

from temporal_clash.detector import TemporalLeakageDetector

from .controller import AtlasRAG
from .dataset import (
    benchmark_queries,
    build_corpus,
    load_cases,
    sanitize_evidence_text,
)
from .evidence_graph import TemporalEvidenceGraph
from .models import EvidenceDocument, QuerySpec, RetrievalHit
from .retrieval import HybridTemporalRetriever
from .router import AdaptiveRouter
from .service import AdvancedRAGService
from .server import make_handler


class AdvancedRAGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_cases()
        cls.corpus = build_corpus(cls.cases)
        cls.router = AdaptiveRouter()
        cls.retriever = HybridTemporalRetriever(cls.corpus, router=cls.router)
        cls.atlas = AtlasRAG(cls.retriever, router=cls.router)

    def test_runtime_document_does_not_expose_benchmark_labels(self) -> None:
        payload = self.corpus[0].to_dict()
        self.assertNotIn("supports_gold", payload)
        self.assertNotIn("is_perturbed", payload)
        self.assertNotIn("gold_answer", payload)
        self.assertRegex(payload["document_id"], r"^doc-[0-9a-f]{20}$")
        self.assertNotIn("synthetic://", payload["source_url"])

    def test_synthetic_banner_is_removed_before_indexing(self) -> None:
        text = "【人工扰动：截止日之后发布】按月末收盘价复算。"
        self.assertEqual(sanitize_evidence_text(text), "按月末收盘价复算。")

    def test_router_prefers_expected_financial_sources(self) -> None:
        market = QuerySpec(
            "market",
            "标普500指数最大的单月涨幅是多少？",
            "2025-05-01",
            "2010-01/2025-04",
            "final",
            "percent",
        )
        filing = QuerySpec(
            "filing",
            "Apple 2024财年的净销售额是多少？",
            "2025-01-01",
            "FY2024",
            "final",
            "usd_million",
        )
        self.assertEqual(
            self.router.plan(market).preferred_authorities[0], "market_data_api"
        )
        self.assertEqual(
            self.router.plan(filing).preferred_authorities[0], "regulator_filing"
        )

    def test_temporal_retrieval_is_deterministic(self) -> None:
        query, _ = benchmark_queries(self.cases)[0]
        plan = self.router.plan(query)
        first = self.retriever.retrieve(query, plan, limit=10, mode="temporal")
        second = self.retriever.retrieve(query, plan, limit=10, mode="temporal")
        self.assertEqual(
            [hit.document.document_id for hit in first],
            [hit.document.document_id for hit in second],
        )

    def test_document_version_uses_half_open_effective_interval(self) -> None:
        candidate = {
            "candidate_id": "versioned",
            "evidence_text_zh": "A filing version valid only during February.",
            "answer_value": "100",
            "unit": "usd_million",
            "target_period": "FY2024",
            "published_at": "2024-01-15",
            "effective_from": "2024-02-01",
            "effective_to": "2024-03-01",
            "revision": "v1",
            "source_url": "https://example.com/v1",
            "source_authority": "regulator_filing",
        }
        document = EvidenceDocument.from_candidate(
            candidate, base_question_id="versioned"
        )
        self.assertFalse(document.is_visible_at("2024-01-31"))
        self.assertTrue(document.is_visible_at("2024-02-29"))
        self.assertFalse(document.is_visible_at("2024-03-01"))
        self.assertRegex(document.content_hash, r"^[0-9a-f]{64}$")

        detector = TemporalLeakageDetector("full")
        case = {
            "cutoff_date": "2024-03-01",
            "target_period": "FY2024",
            "required_version": "v1",
            "canonical_unit": "usd_million",
        }
        audit = detector.audit(document.audit_payload(), case)
        self.assertFalse(audit.accepted)
        self.assertIn("effective_interval", audit.violations)

    def test_evidence_graph_detects_value_conflict(self) -> None:
        query, _ = benchmark_queries(self.cases)[0]
        gold = next(
            document
            for document in self.corpus
            if document.base_question_id == query.query_id
            and document.published_at <= query.cutoff_date
            and document.target_period == query.target_period
            and document.revision == query.required_version
            and document.unit == query.canonical_unit
        )
        conflicting = EvidenceDocument(
            document_id="conflict",
            base_question_id=gold.base_question_id,
            text=gold.text,
            answer_value="999",
            unit=gold.unit,
            target_period=gold.target_period,
            published_at=gold.published_at,
            revision=gold.revision,
            source_url="https://example.com/conflict",
            source_authority=gold.source_authority,
        )
        hits = [
            RetrievalHit(gold, 1, 0.9, {"semantic": 0.9, "source_utility": 1.0}),
            RetrievalHit(
                conflicting,
                2,
                0.8,
                {"semantic": 0.8, "source_utility": 1.0},
            ),
        ]
        detector = TemporalLeakageDetector("full")
        case = {
            "cutoff_date": query.cutoff_date,
            "target_period": query.target_period,
            "required_version": query.required_version,
            "canonical_unit": query.canonical_unit,
        }
        audits = {
            hit.document.document_id: detector.audit(
                hit.document.audit_payload(), case
            )
            for hit in hits
        }
        result = TemporalEvidenceGraph().resolve(query, hits, audits)
        self.assertIn("value", {edge.conflict_type for edge in result.conflicts})

    def test_atlas_returns_auditable_state_trace(self) -> None:
        query, clean_case = benchmark_queries(self.cases)[0]
        decision = self.atlas.answer(query)
        states = [item["state"] for item in decision.trace]
        self.assertEqual(states[0], "PLAN")
        self.assertIn("RETRIEVE", states)
        self.assertIn("AUDIT", states)
        self.assertIn("RESOLVE", states)
        self.assertIn(states[-1], {"ANSWER", "ABSTAIN"})
        if decision.action == "answer":
            self.assertEqual(decision.answer_value, clean_case["gold_answer"])

    def test_thread_safe_cache_returns_consistent_results(self) -> None:
        query, _ = benchmark_queries(self.cases)[0]
        service = AdvancedRAGService(self.atlas)
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(lambda _: service.answer(query), range(20)))
        decisions = [response["decision"] for response in responses]
        self.assertTrue(all(decision == decisions[0] for decision in decisions))
        self.assertGreaterEqual(service.metrics()["cache_hits_total"], 1)

    def test_http_service_contract(self) -> None:
        query, _ = benchmark_queries(self.cases)[0]
        service = AdvancedRAGService(self.atlas)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(service)
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urlopen(f"{base_url}/healthz", timeout=2) as response:
                health = json.load(response)
            self.assertEqual(health["status"], "ok")
            request = Request(
                f"{base_url}/v1/answer",
                data=json.dumps(query.to_dict(), ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Request-ID": "unit-test",
                },
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                payload = json.load(response)
            self.assertEqual(payload["request_id"], "unit-test")
            self.assertIn(payload["decision"]["action"], {"answer", "abstain"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
