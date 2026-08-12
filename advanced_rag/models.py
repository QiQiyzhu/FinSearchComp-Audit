from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    question: str
    cutoff_date: str
    target_period: str
    required_version: str
    canonical_unit: str

    @classmethod
    def from_case(cls, case: dict[str, Any]) -> "QuerySpec":
        query = cls(
            query_id=str(case.get("base_question_id") or case.get("id") or case["case_id"]),
            question=str(case.get("question_zh") or case["question"]),
            cutoff_date=str(case["cutoff_date"]),
            target_period=str(case["target_period"]),
            required_version=str(case["required_version"]),
            canonical_unit=str(case["canonical_unit"]),
        )
        query.validate()
        return query

    def validate(self) -> None:
        if not self.question.strip():
            raise ValueError("question must not be empty")
        date.fromisoformat(self.cutoff_date)
        for name in ("target_period", "required_version", "canonical_unit"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceDocument:
    document_id: str
    base_question_id: str
    text: str
    answer_value: str
    unit: str
    target_period: str
    published_at: str
    revision: str
    source_url: str
    source_authority: str

    @classmethod
    def from_candidate(
        cls,
        candidate: dict[str, Any],
        *,
        base_question_id: str,
        document_id: str | None = None,
        text: str | None = None,
        source_url: str | None = None,
        source_authority: str | None = None,
    ) -> "EvidenceDocument":
        return cls(
            document_id=document_id or str(candidate["candidate_id"]),
            base_question_id=base_question_id,
            text=text if text is not None else str(candidate.get("evidence_text_zh", "")),
            answer_value=str(candidate.get("answer_value", "")),
            unit=str(candidate.get("unit", "missing")),
            target_period=str(candidate.get("target_period", "missing")),
            published_at=str(candidate.get("published_at", "missing")),
            revision=str(candidate.get("revision", "missing")),
            source_url=(
                source_url
                if source_url is not None
                else str(candidate.get("source_url", ""))
            ),
            source_authority=(
                source_authority
                if source_authority is not None
                else str(candidate.get("source_authority", "unknown"))
            ),
        )

    def audit_payload(self) -> dict[str, Any]:
        """Return only fields available to the runtime audit layer.

        Benchmark-only labels such as ``supports_gold`` and ``is_perturbed``
        are deliberately absent, preventing evaluation-label leakage.
        """

        return {
            "candidate_id": self.document_id,
            "answer_value": self.answer_value,
            "unit": self.unit,
            "target_period": self.target_period,
            "published_at": self.published_at,
            "revision": self.revision,
            "source_url": self.source_url,
            "source_authority": self.source_authority,
            "evidence_text_zh": self.text,
        }

    @property
    def fact_signature(self) -> tuple[str, str, str, str]:
        return (
            self.answer_value,
            self.unit,
            self.target_period,
            self.revision,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoutePlan:
    complexity: str
    preferred_authorities: tuple[str, ...]
    query_variants: tuple[str, ...]
    initial_k: int
    correction_k: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalHit:
    document: EvidenceDocument
    rank: int
    score: float
    component_scores: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "rank": self.rank,
            "score": self.score,
            "component_scores": dict(self.component_scores),
        }


@dataclass(frozen=True)
class ConflictEdge:
    left_document_id: str
    right_document_id: str
    conflict_type: str
    details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphResolution:
    selected_document_id: str | None
    accepted_document_ids: tuple[str, ...]
    rejected_document_ids: tuple[str, ...]
    conflicts: tuple[ConflictEdge, ...]
    group_scores: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_document_id": self.selected_document_id,
            "accepted_document_ids": list(self.accepted_document_ids),
            "rejected_document_ids": list(self.rejected_document_ids),
            "conflicts": [edge.to_dict() for edge in self.conflicts],
            "group_scores": dict(self.group_scores),
        }


@dataclass(frozen=True)
class RAGDecision:
    query_id: str
    action: str
    answer_value: str | None
    unit: str | None
    confidence: float
    selected_evidence_id: str | None
    accepted_evidence_ids: tuple[str, ...]
    used_corrective_retrieval: bool
    route: RoutePlan
    conflicts: tuple[ConflictEdge, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    trace: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "action": self.action,
            "answer_value": self.answer_value,
            "unit": self.unit,
            "confidence": self.confidence,
            "selected_evidence_id": self.selected_evidence_id,
            "accepted_evidence_ids": list(self.accepted_evidence_ids),
            "used_corrective_retrieval": self.used_corrective_retrieval,
            "route": self.route.to_dict(),
            "conflicts": [edge.to_dict() for edge in self.conflicts],
            "rejection_reasons": list(self.rejection_reasons),
            "trace": list(self.trace),
        }
