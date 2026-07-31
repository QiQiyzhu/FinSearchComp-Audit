from __future__ import annotations

from typing import Any, Callable

from .detector import TemporalLeakageDetector


Candidate = dict[str, Any]
Case = dict[str, Any]
Policy = Callable[[Case], Candidate | None]


def plain_agent(case: Case) -> Candidate | None:
    """Proxy for an agent that trusts the first retrieved evidence item."""
    return TemporalLeakageDetector("allow_all").select(case).selected


def temporal_prompt(case: Case) -> Candidate | None:
    """Proxy for a prompt that checks dates but not structured metadata."""
    return TemporalLeakageDetector("date_only").select(case).selected


def metadata_filter(case: Case) -> Candidate | None:
    """Filter by date, target period and requested revision."""
    return TemporalLeakageDetector("metadata").select(case).selected


def teg_validator(case: Case) -> Candidate | None:
    """Temporal Evidence Graph proxy with date, period, version and unit checks."""
    return TemporalLeakageDetector("full").select(case).selected


POLICIES: dict[str, tuple[str, Policy]] = {
    "plain_agent": ("普通 Agent", plain_agent),
    "temporal_prompt": ("时间约束 Prompt", temporal_prompt),
    "metadata_filter": ("元数据过滤器", metadata_filter),
    "teg_validator": ("TEG 验证器", teg_validator),
}

POLICY_PROFILES = {
    "plain_agent": "allow_all",
    "temporal_prompt": "date_only",
    "metadata_filter": "metadata",
    "teg_validator": "full",
}
