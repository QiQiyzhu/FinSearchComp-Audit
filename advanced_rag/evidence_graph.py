from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from temporal_clash.detector import AuditResult

from .models import (
    ConflictEdge,
    GraphResolution,
    QuerySpec,
    RetrievalHit,
)


class TemporalEvidenceGraph:
    """Build fact-level conflict edges and perform listwise arbitration."""

    def resolve(
        self,
        query: QuerySpec,
        hits: Iterable[RetrievalHit],
        audits: dict[str, AuditResult],
    ) -> GraphResolution:
        hit_list = list(hits)
        conflicts = self._conflicts(query, hit_list)
        accepted = [
            hit
            for hit in hit_list
            if audits[hit.document.document_id].accepted
            and hit.component_scores["semantic"] >= 0.08
        ]
        rejected = [
            hit.document.document_id
            for hit in hit_list
            if hit not in accepted
        ]

        groups: dict[tuple[str, str, str, str], list[RetrievalHit]] = defaultdict(list)
        for hit in accepted:
            groups[hit.document.fact_signature].append(hit)

        scored_groups: dict[tuple[str, str, str, str], float] = {}
        for signature, members in groups.items():
            source_diversity = len({member.document.source_authority for member in members})
            score = sum(
                member.score * (0.65 + 0.35 * member.component_scores["source_utility"])
                for member in members
            )
            score += min(source_diversity - 1, 2) * 0.04
            scored_groups[signature] = score

        group_scores = {
            json_label: scored_groups[signature]
            for signature in sorted(scored_groups)
            for json_label in [" | ".join(signature)]
        }

        selected_document_id: str | None = None
        if scored_groups:
            best_signature = max(
                scored_groups,
                key=lambda signature: (scored_groups[signature], signature),
            )
            selected = max(
                groups[best_signature],
                key=lambda hit: (
                    hit.score,
                    hit.component_scores["source_utility"],
                    hit.document.document_id,
                ),
            )
            selected_document_id = selected.document.document_id

        return GraphResolution(
            selected_document_id=selected_document_id,
            accepted_document_ids=tuple(
                hit.document.document_id for hit in accepted
            ),
            rejected_document_ids=tuple(rejected),
            conflicts=tuple(conflicts),
            group_scores=group_scores,
        )

    def _conflicts(
        self, query: QuerySpec, hits: list[RetrievalHit]
    ) -> list[ConflictEdge]:
        edges: list[ConflictEdge] = []
        for index, left in enumerate(hits):
            for right in hits[index + 1 :]:
                if left.document.base_question_id != right.document.base_question_id:
                    continue
                conflict_type, details = self._pair_conflict(query, left, right)
                if conflict_type:
                    edges.append(
                        ConflictEdge(
                            left_document_id=left.document.document_id,
                            right_document_id=right.document.document_id,
                            conflict_type=conflict_type,
                            details=details,
                        )
                    )
        return edges

    @staticmethod
    def _pair_conflict(
        query: QuerySpec, left: RetrievalHit, right: RetrievalHit
    ) -> tuple[str | None, str]:
        left_doc, right_doc = left.document, right.document
        try:
            left_future = date.fromisoformat(left_doc.published_at) > date.fromisoformat(
                query.cutoff_date
            )
        except ValueError:
            left_future = True
        try:
            right_future = date.fromisoformat(right_doc.published_at) > date.fromisoformat(
                query.cutoff_date
            )
        except ValueError:
            right_future = True
        if left_future != right_future:
            return "future_version", "同一事实的一条证据在截止日后发布"
        if left_doc.target_period != right_doc.target_period:
            return "period", "候选证据对应不同目标期间"
        if left_doc.revision != right_doc.revision:
            return "version", "候选证据对应不同数据版本"
        if left_doc.unit != right_doc.unit:
            return "unit", "候选证据使用不同单位"
        if left_doc.answer_value != right_doc.answer_value:
            return "value", "相同期间、版本和单位下的数值不一致"
        return None, ""
