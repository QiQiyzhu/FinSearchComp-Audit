"""Explainable temporal-integrity checks for financial evidence candidates.

The detector is intentionally deterministic.  It is an audit layer that can be
placed after retrieval and before answer generation; it is not an LLM and does
not claim to detect hidden dates or facts that are absent from the evidence
metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable


Candidate = dict[str, Any]
Case = dict[str, Any]

CHECK_PROFILES: dict[str, tuple[str, ...]] = {
    "allow_all": (),
    "date_only": ("published_at",),
    "metadata": ("published_at", "target_period", "revision"),
    "full": ("published_at", "target_period", "revision", "unit"),
}

CHECK_LABELS = {
    "published_at": "发布时间",
    "target_period": "目标期间",
    "revision": "数据版本",
    "unit": "单位",
}

RISK_WEIGHTS = {
    "published_at": 1.00,
    "target_period": 0.75,
    "revision": 0.70,
    "unit": 0.60,
}


@dataclass(frozen=True)
class CheckResult:
    check: str
    label: str
    passed: bool
    expected: str
    observed: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditResult:
    candidate_id: str
    profile: str
    verdict: str
    risk_score: float
    violations: tuple[str, ...]
    checks: tuple[CheckResult, ...]

    @property
    def accepted(self) -> bool:
        return self.verdict == "accept"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["accepted"] = self.accepted
        return payload


@dataclass(frozen=True)
class SelectionResult:
    selected: Candidate | None
    audits: tuple[AuditResult, ...]


class TemporalLeakageDetector:
    """Audit explicit evidence metadata against a point-in-time question."""

    def __init__(self, profile: str = "full") -> None:
        if profile not in CHECK_PROFILES:
            raise ValueError(
                f"Unknown profile {profile!r}; choose from {sorted(CHECK_PROFILES)}"
            )
        self.profile = profile
        self.active_checks = CHECK_PROFILES[profile]

    def audit(self, candidate: Candidate, case: Case) -> AuditResult:
        checks = tuple(
            self._run_check(check_name, candidate, case)
            for check_name in self.active_checks
        )
        violations = tuple(check.check for check in checks if not check.passed)
        risk_score = max(
            (RISK_WEIGHTS.get(check_name, 0.80) for check_name in violations),
            default=0.0,
        )
        return AuditResult(
            candidate_id=str(candidate.get("candidate_id", "unknown")),
            profile=self.profile,
            verdict="reject" if violations else "accept",
            risk_score=risk_score,
            violations=violations,
            checks=checks,
        )

    def select(self, case: Case) -> SelectionResult:
        audits: list[AuditResult] = []
        for candidate in case.get("candidates", []):
            audit = self.audit(candidate, case)
            audits.append(audit)
            if audit.accepted:
                return SelectionResult(candidate, tuple(audits))
        return SelectionResult(None, tuple(audits))

    def audit_many(
        self, candidates: Iterable[Candidate], case: Case
    ) -> tuple[AuditResult, ...]:
        return tuple(self.audit(candidate, case) for candidate in candidates)

    def _run_check(
        self, check_name: str, candidate: Candidate, case: Case
    ) -> CheckResult:
        if check_name == "published_at":
            expected = str(case.get("cutoff_date", "missing"))
            observed = str(candidate.get("published_at", "missing"))
            try:
                passed = date.fromisoformat(observed) <= date.fromisoformat(expected)
                reason = (
                    f"证据发布于 {observed}，不晚于截止日 {expected}"
                    if passed
                    else f"证据发布于 {observed}，晚于截止日 {expected}"
                )
            except ValueError:
                passed = False
                reason = "发布日期或截止日缺失/格式无效，无法证明时间合规"
        else:
            case_field = {
                "target_period": "target_period",
                "revision": "required_version",
                "unit": "canonical_unit",
            }[check_name]
            expected = str(case.get(case_field, "missing"))
            observed = str(candidate.get(check_name, "missing"))
            passed = observed == expected and observed != "missing"
            reason = (
                f"{CHECK_LABELS[check_name]}匹配：{observed}"
                if passed
                else (
                    f"{CHECK_LABELS[check_name]}不匹配："
                    f"期望 {expected}，实际 {observed}"
                )
            )

        return CheckResult(
            check=check_name,
            label=CHECK_LABELS[check_name],
            passed=passed,
            expected=expected,
            observed=observed,
            reason=reason,
        )
