"""Deterministic agentic retrieval primitives used by experiments E4--E6.

The module deliberately keeps planning, retrieval and sufficiency decisions
separate.  Evaluation labels live in the experiment harness, never in these
runtime components, so the controlled benchmark cannot leak its answerability
labels into the system under test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import re
from typing import Iterable, Mapping, Sequence


COMPANIES = (
    "Northstar",
    "Helios",
    "Orion",
    "Atlas",
    "Nova",
    "Aster",
    "Lumen",
    "Vega",
)

METRIC_ALIASES: Mapping[str, tuple[str, ...]] = {
    "revenue": ("净销售额", "营收", "营业收入", "revenue", "net sales"),
    "research_and_development": (
        "研发费用",
        "研发支出",
        "research and development",
        "r&d",
    ),
    "gross_profit": ("毛利润", "gross profit"),
    "operating_income": ("营业利润", "operating income"),
    "operating_cash_flow": ("经营现金流", "operating cash flow"),
    "capital_expenditure": ("资本开支", "capital expenditure", "capex"),
    "net_income": ("净利润", "net income"),
}

METRIC_UNITS: Mapping[str, str] = {
    "revenue": "usd_million",
    "research_and_development": "usd_million",
    "gross_profit": "usd_million",
    "operating_income": "usd_million",
    "operating_cash_flow": "usd_million",
    "capital_expenditure": "usd_million",
    "net_income": "usd_million",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9_&]+|[\u4e00-\u9fff]{1,4}")


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text)}


@dataclass(frozen=True)
class AgenticQuery:
    query_id: str
    question: str
    as_of: date


@dataclass(frozen=True)
class EvidenceSlot:
    slot_id: str
    company: str
    metric: str
    period: str
    unit: str

    def retrieval_query(self) -> str:
        return f"{self.company} {self.period} {self.metric} {self.unit} final filing"


@dataclass(frozen=True)
class GapPlan:
    operation: str
    slots: tuple[EvidenceSlot, ...]
    rationale: str

    @property
    def required_slot_ids(self) -> tuple[str, ...]:
        return tuple(slot.slot_id for slot in self.slots)


@dataclass(frozen=True)
class AgenticDocument:
    doc_id: str
    company: str
    metric: str
    period: str
    value: Decimal
    unit: str
    text: str
    published_at: date
    effective_from: date
    effective_to: date | None = None
    version: str = "final"
    authority: str = "issuer_filing"
    content_hash: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "AgenticDocument":
        text = str(raw["text"])
        content_hash = str(raw.get("content_hash") or hashlib.sha256(text.encode("utf-8")).hexdigest())
        effective_to = raw.get("effective_to")
        return cls(
            doc_id=str(raw["doc_id"]),
            company=str(raw["company"]),
            metric=str(raw["metric"]),
            period=str(raw["period"]),
            value=Decimal(str(raw["value"])),
            unit=str(raw["unit"]),
            text=text,
            published_at=date.fromisoformat(str(raw["published_at"])),
            effective_from=date.fromisoformat(str(raw["effective_from"])),
            effective_to=date.fromisoformat(str(effective_to)) if effective_to else None,
            version=str(raw.get("version", "final")),
            authority=str(raw.get("authority", "issuer_filing")),
            content_hash=content_hash,
        )

    def is_visible_at(self, cutoff: date) -> bool:
        return (
            self.published_at <= cutoff
            and self.effective_from <= cutoff
            and (self.effective_to is None or cutoff < self.effective_to)
        )


@dataclass(frozen=True)
class SufficiencyDecision:
    answerable: bool
    completeness: float
    matched: Mapping[str, AgenticDocument] = field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    conflicting_slots: tuple[str, ...] = ()
    reason: str = ""


class StructuredGapPlanner:
    """Compile a constrained financial question into explicit evidence slots."""

    def plan(self, query: AgenticQuery) -> GapPlan:
        question = query.question
        company = next((name for name in COMPANIES if name.casefold() in question.casefold()), None)
        if company is None:
            raise ValueError(f"Unsupported or missing company in query: {question}")

        periods = tuple(dict.fromkeys(re.findall(r"FY\d{4}", question.upper())))
        if not periods:
            raise ValueError(f"Missing fiscal period in query: {question}")

        if "毛利率" in question:
            return self._margin_plan(company, periods, "gross_profit", "gross margin")
        if "营业利润率" in question:
            return self._margin_plan(company, periods, "operating_income", "operating margin")
        if "占" in question and ("比例" in question or "率" in question):
            numerator_text, denominator_text = question.split("占", 1)
            numerator = self._find_metric(numerator_text)
            denominator = self._find_metric(denominator_text)
            period = periods[0]
            return GapPlan(
                operation="ratio",
                slots=(
                    self._slot("numerator", company, numerator, period),
                    self._slot("denominator", company, denominator, period),
                ),
                rationale="A ratio requires independently sourced numerator and denominator evidence.",
            )

        metric = self._find_metric(question)
        if len(periods) < 2:
            raise ValueError(f"Comparison requires two fiscal periods: {question}")
        if "增长" in question or "增幅" in question:
            operation = "relative_change"
            rationale = "A growth rate requires current-period and prior-period values."
        elif "增加多少" in question or "变化多少" in question:
            operation = "difference"
            rationale = "An absolute change requires current-period and prior-period values."
        else:
            raise ValueError(f"Unsupported operation in query: {question}")
        return GapPlan(
            operation=operation,
            slots=(
                self._slot("current", company, metric, periods[0]),
                self._slot("prior", company, metric, periods[1]),
            ),
            rationale=rationale,
        )

    def _margin_plan(
        self,
        company: str,
        periods: Sequence[str],
        numerator_metric: str,
        label: str,
    ) -> GapPlan:
        if len(periods) < 2:
            raise ValueError(f"{label} change requires two fiscal periods")
        current, prior = periods[:2]
        return GapPlan(
            operation="margin_change",
            slots=(
                self._slot("current_numerator", company, numerator_metric, current),
                self._slot("current_denominator", company, "revenue", current),
                self._slot("prior_numerator", company, numerator_metric, prior),
                self._slot("prior_denominator", company, "revenue", prior),
            ),
            rationale=f"A {label} change needs numerator and revenue for both periods.",
        )

    @staticmethod
    def _slot(slot_id: str, company: str, metric: str, period: str) -> EvidenceSlot:
        return EvidenceSlot(slot_id, company, metric, period, METRIC_UNITS[metric])

    @staticmethod
    def _find_metric(text: str) -> str:
        lowered = text.casefold()
        matches: list[tuple[int, str]] = []
        for metric, aliases in METRIC_ALIASES.items():
            for alias in aliases:
                position = lowered.rfind(alias.casefold())
                if position >= 0:
                    matches.append((position, metric))
        if not matches:
            raise ValueError(f"Unsupported or missing metric in: {text}")
        return max(matches)[1]


class SufficiencyGate:
    """Decide whether evidence is complete, conflict-free and point-in-time valid."""

    def assess(
        self,
        plan: GapPlan,
        documents: Iterable[AgenticDocument],
        cutoff: date,
    ) -> SufficiencyDecision:
        documents = tuple(documents)
        matched: dict[str, AgenticDocument] = {}
        missing: list[str] = []
        conflicts: list[str] = []

        for slot in plan.slots:
            candidates = [
                document
                for document in documents
                if document.company == slot.company
                and document.metric == slot.metric
                and document.period == slot.period
                and document.unit == slot.unit
                and document.version == "final"
                and document.is_visible_at(cutoff)
            ]
            unique: dict[tuple[Decimal, str], AgenticDocument] = {}
            for document in candidates:
                unique.setdefault((document.value, document.content_hash), document)
            distinct_values = {value for value, _ in unique}
            if not candidates:
                missing.append(slot.slot_id)
            elif len(distinct_values) > 1:
                conflicts.append(slot.slot_id)
            else:
                matched[slot.slot_id] = sorted(
                    candidates,
                    key=lambda item: (
                        item.authority != "regulator_filing",
                        -item.published_at.toordinal(),
                        item.doc_id,
                    ),
                )[0]

        satisfied = len(matched)
        completeness = satisfied / len(plan.slots) if plan.slots else 0.0
        answerable = not missing and not conflicts and satisfied == len(plan.slots)
        if answerable:
            reason = "all_required_slots_supported"
        elif conflicts:
            reason = "conflicting_evidence"
        else:
            reason = "missing_required_evidence"
        return SufficiencyDecision(
            answerable=answerable,
            completeness=completeness,
            matched=matched,
            missing_slots=tuple(missing),
            conflicting_slots=tuple(conflicts),
            reason=reason,
        )


class DeterministicCalculator:
    """Calculate an answer only from a successful sufficiency decision."""

    @staticmethod
    def answer(plan: GapPlan, decision: SufficiencyDecision) -> str | None:
        if not decision.answerable:
            return None
        values = {slot_id: document.value for slot_id, document in decision.matched.items()}
        if plan.operation == "relative_change":
            result = (values["current"] - values["prior"]) / abs(values["prior"]) * 100
            unit = "%"
        elif plan.operation == "difference":
            result = values["current"] - values["prior"]
            unit = " usd_million"
        elif plan.operation == "ratio":
            result = values["numerator"] / values["denominator"] * 100
            unit = "%"
        elif plan.operation == "margin_change":
            current = values["current_numerator"] / values["current_denominator"] * 100
            prior = values["prior_numerator"] / values["prior_denominator"] * 100
            result = current - prior
            unit = " pp"
        else:
            raise ValueError(f"Unsupported operation: {plan.operation}")
        rounded = result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{rounded:.2f}{unit}"


class AgenticRetriever:
    """Small deterministic retriever exposing one-shot and slot-aware search."""

    def search_text(
        self,
        query_text: str,
        documents: Sequence[AgenticDocument],
        cutoff: date,
        limit: int,
    ) -> list[AgenticDocument]:
        query_tokens = _tokens(query_text)
        scored: list[tuple[float, str, AgenticDocument]] = []
        for document in documents:
            if not document.is_visible_at(cutoff):
                continue
            overlap = len(query_tokens & _tokens(document.text))
            final_bonus = 0.35 if document.version == "final" else 0.0
            authority_bonus = 0.15 if document.authority == "regulator_filing" else 0.0
            score = float(overlap) + final_bonus + authority_bonus
            scored.append((-score, document.doc_id, document))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    @staticmethod
    def rewrite(plan: GapPlan) -> str:
        return " ".join(slot.retrieval_query() for slot in plan.slots)

    @staticmethod
    def search_slot(
        slot: EvidenceSlot,
        documents: Sequence[AgenticDocument],
        cutoff: date,
    ) -> AgenticDocument | None:
        candidates = [
            document
            for document in documents
            if document.company == slot.company
            and document.metric == slot.metric
            and document.period == slot.period
            and document.unit == slot.unit
            and document.is_visible_at(cutoff)
        ]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda item: (
                item.version != "final",
                item.authority != "regulator_filing",
                -item.published_at.toordinal(),
                item.doc_id,
            ),
        )[0]


def slot_recall(plan: GapPlan, decision: SufficiencyDecision) -> float:
    """Return supported-slot recall; conflicts do not count as supported."""

    return len(decision.matched) / len(plan.slots) if plan.slots else 0.0
