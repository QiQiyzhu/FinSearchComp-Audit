from __future__ import annotations

from datetime import date
from typing import Any, Callable


Candidate = dict[str, Any]
Case = dict[str, Any]
Policy = Callable[[Case], Candidate | None]


def _published_by_cutoff(candidate: Candidate, case: Case) -> bool:
    return date.fromisoformat(candidate["published_at"]) <= date.fromisoformat(
        case["cutoff_date"]
    )


def plain_agent(case: Case) -> Candidate | None:
    """Proxy for an agent that trusts the first retrieved evidence item."""
    return case["candidates"][0] if case["candidates"] else None


def temporal_prompt(case: Case) -> Candidate | None:
    """Proxy for a prompt that checks dates but not structured metadata."""
    return next(
        (item for item in case["candidates"] if _published_by_cutoff(item, case)),
        None,
    )


def metadata_filter(case: Case) -> Candidate | None:
    """Filter by date, target period and requested revision."""
    return next(
        (
            item
            for item in case["candidates"]
            if _published_by_cutoff(item, case)
            and item["target_period"] == case["target_period"]
            and item["revision"] == case["required_version"]
        ),
        None,
    )


def teg_validator(case: Case) -> Candidate | None:
    """Temporal Evidence Graph proxy with date, period, version and unit checks."""
    return next(
        (
            item
            for item in case["candidates"]
            if _published_by_cutoff(item, case)
            and item["target_period"] == case["target_period"]
            and item["revision"] == case["required_version"]
            and item["unit"] == case["canonical_unit"]
        ),
        None,
    )


POLICIES: dict[str, tuple[str, Policy]] = {
    "plain_agent": ("普通 Agent", plain_agent),
    "temporal_prompt": ("时间约束 Prompt", temporal_prompt),
    "metadata_filter": ("元数据过滤器", metadata_filter),
    "teg_validator": ("TEG 验证器", teg_validator),
}
