from __future__ import annotations

from typing import Any

from temporal_clash.detector import AuditResult, TemporalLeakageDetector

from .evidence_graph import TemporalEvidenceGraph
from .models import (
    GraphResolution,
    QuerySpec,
    RAGDecision,
    RetrievalHit,
)
from .retrieval import HybridTemporalRetriever
from .router import AdaptiveRouter


class AtlasRAG:
    """Adaptive Temporal Listwise Arbitration and Source-aware RAG.

    State machine: PLAN -> RETRIEVE -> AUDIT -> RESOLVE -> optional CORRECT
    -> ANSWER/ABSTAIN.  Every transition is recorded in the returned trace.
    """

    def __init__(
        self,
        retriever: HybridTemporalRetriever,
        *,
        router: AdaptiveRouter | None = None,
        confidence_threshold: float = 0.54,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be in [0, 1]")
        self.retriever = retriever
        self.router = router or retriever.router
        self.detector = TemporalLeakageDetector("full")
        self.graph = TemporalEvidenceGraph()
        self.confidence_threshold = confidence_threshold

    def answer(self, query: QuerySpec) -> RAGDecision:
        query.validate()
        trace: list[dict[str, Any]] = []
        plan = self.router.plan(query)
        trace.append(
            {
                "state": "PLAN",
                "complexity": plan.complexity,
                "preferred_authorities": list(plan.preferred_authorities),
                "reasons": list(plan.reasons),
            }
        )

        hits, audits, resolution, confidence = self._run_pass(
            query, plan, limit=plan.initial_k, corrective=False, trace=trace
        )
        used_correction = False
        if resolution.selected_document_id is None or confidence < self.confidence_threshold:
            used_correction = True
            trace.append(
                {
                    "state": "CORRECT",
                    "reason": (
                        "没有合规证据"
                        if resolution.selected_document_id is None
                        else f"置信度 {confidence:.3f} 低于阈值 {self.confidence_threshold:.3f}"
                    ),
                    "new_retrieval_limit": plan.correction_k,
                }
            )
            hits, audits, resolution, confidence = self._run_pass(
                query,
                plan,
                limit=plan.correction_k,
                corrective=True,
                trace=trace,
            )

        selected = self._find_hit(hits, resolution.selected_document_id)
        if selected is not None and confidence >= self.confidence_threshold:
            trace.append(
                {
                    "state": "ANSWER",
                    "selected_evidence_id": selected.document.document_id,
                    "confidence": round(confidence, 6),
                }
            )
            return RAGDecision(
                query_id=query.query_id,
                action="answer",
                answer_value=selected.document.answer_value,
                unit=selected.document.unit,
                confidence=round(confidence, 6),
                selected_evidence_id=selected.document.document_id,
                accepted_evidence_ids=resolution.accepted_document_ids,
                used_corrective_retrieval=used_correction,
                route=plan,
                conflicts=resolution.conflicts,
                trace=tuple(trace),
            )

        reasons = self._rejection_reasons(hits, audits, confidence)
        trace.append(
            {
                "state": "ABSTAIN",
                "confidence": round(confidence, 6),
                "reasons": list(reasons),
            }
        )
        return RAGDecision(
            query_id=query.query_id,
            action="abstain",
            answer_value=None,
            unit=None,
            confidence=round(confidence, 6),
            selected_evidence_id=None,
            accepted_evidence_ids=resolution.accepted_document_ids,
            used_corrective_retrieval=used_correction,
            route=plan,
            conflicts=resolution.conflicts,
            rejection_reasons=reasons,
            trace=tuple(trace),
        )

    def _run_pass(
        self,
        query: QuerySpec,
        plan,
        *,
        limit: int,
        corrective: bool,
        trace: list[dict[str, Any]],
    ) -> tuple[
        list[RetrievalHit],
        dict[str, AuditResult],
        GraphResolution,
        float,
    ]:
        hits = self.retriever.retrieve(
            query,
            plan,
            limit=limit,
            mode="temporal",
            corrective=corrective,
        )
        trace.append(
            {
                "state": "RETRIEVE",
                "corrective": corrective,
                "returned": len(hits),
                "top_ids": [hit.document.document_id for hit in hits[:5]],
            }
        )

        case = {
            "cutoff_date": query.cutoff_date,
            "target_period": query.target_period,
            "required_version": query.required_version,
            "canonical_unit": query.canonical_unit,
        }
        audits = {
            hit.document.document_id: self.detector.audit(
                hit.document.audit_payload(), case
            )
            for hit in hits
        }
        trace.append(
            {
                "state": "AUDIT",
                "accepted": sum(audit.accepted for audit in audits.values()),
                "rejected": sum(not audit.accepted for audit in audits.values()),
                "violations": {
                    document_id: list(audit.violations)
                    for document_id, audit in audits.items()
                    if audit.violations
                },
            }
        )

        resolution = self.graph.resolve(query, hits, audits)
        confidence = self._confidence(hits, resolution)
        trace.append(
            {
                "state": "RESOLVE",
                "selected_evidence_id": resolution.selected_document_id,
                "accepted_evidence_ids": list(resolution.accepted_document_ids),
                "conflict_count": len(resolution.conflicts),
                "confidence": round(confidence, 6),
            }
        )
        return hits, audits, resolution, confidence

    @staticmethod
    def _confidence(
        hits: list[RetrievalHit], resolution: GraphResolution
    ) -> float:
        selected = AtlasRAG._find_hit(hits, resolution.selected_document_id)
        if selected is None:
            return 0.0
        accepted_scores = sorted(
            (
                hit.score
                for hit in hits
                if hit.document.document_id in resolution.accepted_document_ids
            ),
            reverse=True,
        )
        margin = (
            accepted_scores[0] - accepted_scores[1]
            if len(accepted_scores) > 1
            else accepted_scores[0]
        )
        topic_conflicts = sum(
            edge.conflict_type in {"value", "period", "version", "unit"}
            for edge in resolution.conflicts
            if selected.document.document_id
            in {edge.left_document_id, edge.right_document_id}
        )
        conflict_penalty = min(topic_conflicts * 0.03, 0.15)
        confidence = (
            0.42 * selected.score
            + 0.23 * selected.component_scores["semantic"]
            + 0.20 * selected.component_scores["source_utility"]
            + 0.15 * min(max(margin * 2.0, 0.0), 1.0)
            - conflict_penalty
        )
        return min(max(confidence, 0.0), 1.0)

    @staticmethod
    def _find_hit(
        hits: list[RetrievalHit], document_id: str | None
    ) -> RetrievalHit | None:
        if document_id is None:
            return None
        return next(
            (hit for hit in hits if hit.document.document_id == document_id),
            None,
        )

    @staticmethod
    def _rejection_reasons(
        hits: list[RetrievalHit],
        audits: dict[str, AuditResult],
        confidence: float,
    ) -> tuple[str, ...]:
        violations = sorted(
            {
                violation
                for audit in audits.values()
                for violation in audit.violations
            }
        )
        reasons: list[str] = []
        if violations:
            reasons.append("候选证据未通过字段检查：" + ", ".join(violations))
        if not any(audit.accepted for audit in audits.values()):
            reasons.append("两轮检索后仍没有时间合规证据")
        if confidence < 0.54:
            reasons.append(f"证据置信度不足：{confidence:.3f}")
        if not hits:
            reasons.append("检索未返回候选证据")
        return tuple(reasons or ("证据不足，选择拒答",))
