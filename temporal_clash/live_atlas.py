from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ATLAS_STRATEGY = "atlas_rag"
ATLAS_METHOD_VERSION = "live-atlas-1.0"


_MARKET_TERMS = (
    "标普",
    "纳斯达克",
    "指数",
    "回撤",
    "涨幅",
    "回报率",
    "收盘价",
    "股价",
)
_MACRO_TERMS = (
    "cpi",
    "fomc",
    "联邦基金",
    "经常账户",
    "国际收支",
    "通胀",
    "利率",
)
_FILING_TERMS = (
    "财年",
    "财报",
    "销售额",
    "研发费用",
    "现金流",
    "利润",
    "资产",
    "收入",
    "周转率",
    "资本性支出",
)
_MULTI_STEP_TERMS = (
    "同比",
    "比例",
    "平均",
    "最大",
    "最小",
    "差",
    "增长",
    "回撤",
    "回报率",
    "周转率",
    "占",
)

_PRIMARY_HOSTS = (
    "sec.gov",
    "bls.gov",
    "federalreserve.gov",
    "safe.gov.cn",
    "bea.gov",
    "fred.stlouisfed.org",
    "nvidia.com",
    "apple.com",
    "tesla.com",
    "adobe.com",
    "3m.com",
)
_STRUCTURED_HOSTS = (
    "finance.yahoo.com",
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
    "stooq.com",
    "macrotrends.net",
    "annualreports.com",
)


def atlas_route(case: dict[str, Any]) -> dict[str, Any]:
    """Create an auditable route using question text only.

    Gold answers, recorded source URLs and evidence text are intentionally not
    accepted by this function, which makes accidental label use harder.
    """

    question = str(case["question_zh"])
    normalized = question.lower()
    if any(term in normalized for term in _MARKET_TERMS):
        source_route = "market_time_series"
        source_hint = "历史行情 API 或指数提供方的日/月收盘价"
        query_suffix = "历史数据 收盘价 historical data"
    elif any(term in normalized for term in _MACRO_TERMS):
        source_route = "official_macro"
        source_hint = "政府统计机构或中央银行的原始公告"
        query_suffix = "官方 公告 official release"
    elif any(term in normalized for term in _FILING_TERMS):
        source_route = "regulatory_filing"
        source_hint = "SEC 10-K、监管申报或公司年度报告"
        query_suffix = "10-K annual report SEC filing"
    else:
        source_route = "official_web"
        source_hint = "监管机构、发行人或其他一手官方网站"
        query_suffix = "官方 来源"

    complexity = (
        "multi_step"
        if any(term in normalized for term in _MULTI_STEP_TERMS)
        or len(re.findall(r"(?:19|20)\d{2}", normalized)) >= 2
        else "single_step"
    )
    return {
        "source_route": source_route,
        "source_hint": source_hint,
        "complexity": complexity,
        "initial_query": f"{question} {query_suffix}",
    }


def atlas_initial_query(case: dict[str, Any]) -> str:
    return str(atlas_route(case)["initial_query"])


def _canonical_url(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def _parse_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    candidates = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y")
    for pattern in candidates:
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"\b((?:19|20)\d{2}-\d{2}-\d{2})\b", text)
    if match:
        try:
            return date.fromisoformat(match.group(1)).isoformat()
        except ValueError:
            return None
    return None


def _date_from_url(url: Any) -> str | None:
    if not url:
        return None
    text = str(url)
    patterns = (
        r"(?<!\d)((?:19|20)\d{2})[-_/](\d{2})[-_/](\d{2})(?!\d)",
        r"(?<!\d)((?:19|20)\d{2})[-_/](\d{2})(\d{2})(?!\d)",
        r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            continue

    # Archived BLS releases commonly use names such as cpi_01152025.htm.
    bls_match = re.search(r"_(\d{2})(\d{2})((?:19|20)\d{2})(?:\D|$)", text)
    if bls_match:
        month, day, year = map(int, bls_match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def _source_tier(url: Any) -> str:
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return "unknown"
    if any(host == item or host.endswith(f".{item}") for item in _PRIMARY_HOSTS):
        return "primary"
    if any(host == item or host.endswith(f".{item}") for item in _STRUCTURED_HOSTS):
        return "structured_archive"
    return "secondary"


def _source_metadata(
    evidence_url: Any,
    search_sources: list[dict[str, Any]],
) -> tuple[str | None, str, str]:
    canonical = _canonical_url(evidence_url)
    source = next(
        (
            item
            for item in search_sources
            if _canonical_url(item.get("url")) == canonical
        ),
        None,
    )
    if source:
        page_age = _parse_date(source.get("page_age"))
        if page_age:
            return page_age, "search_result_page_age", _source_tier(evidence_url)
    url_date = _date_from_url(evidence_url)
    if url_date:
        return url_date, "url_date", _source_tier(evidence_url)
    return None, "unavailable", _source_tier(evidence_url)


def _field_status(observed: Any, expected: Any, field: str) -> tuple[str, str | None]:
    if observed is None or not str(observed).strip():
        return "unknown", f"{field}_unknown"
    if str(observed).strip() != str(expected):
        return "conflict", f"{field}_mismatch"
    return "matched", None


def apply_atlas_validation(
    result: dict[str, Any],
    case: dict[str, Any],
    search_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply a calibrated hard-conflict gate to a model draft.

    Missing metadata is logged as uncertainty. It is not equivalent to an
    observed violation. This directly addresses the over-abstention failure in
    the first live pilot while retaining hard point-in-time checks.
    """

    sources = search_sources or []
    route = atlas_route(case)
    audits: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    cutoff = date.fromisoformat(str(case["cutoff_date"]))

    for evidence in result.get("evidence") or []:
        hard_conflicts: list[str] = []
        unknowns: list[str] = []
        source_date, source_date_provenance, source_tier = _source_metadata(
            evidence.get("url"), sources
        )
        declared_date = _parse_date(evidence.get("published_at"))
        if evidence.get("published_at") and not declared_date:
            unknowns.append("published_at_invalid")
        dates = [value for value in (source_date, declared_date) if value]
        if any(date.fromisoformat(value) > cutoff for value in dates):
            hard_conflicts.append("published_after_cutoff")
        if not dates:
            unknowns.append("published_at_unknown")

        field_states: dict[str, str] = {}
        for field, expected in (
            ("target_period", case["target_period"]),
            ("revision", case["required_version"]),
            ("unit", case["canonical_unit"]),
        ):
            state, issue = _field_status(evidence.get(field), expected, field)
            field_states[field] = state
            if issue and state == "conflict":
                hard_conflicts.append(issue)
            elif issue:
                unknowns.append(issue)

        if not evidence.get("url"):
            hard_conflicts.append("evidence_url_missing")
        if not evidence.get("evidence_text"):
            unknowns.append("evidence_text_unknown")

        usable = bool(evidence.get("url") and evidence.get("evidence_text"))
        accepted_by_gate = not hard_conflicts and usable
        audit = {
            "url": evidence.get("url"),
            "accepted": accepted_by_gate,
            "violations": hard_conflicts,
            "metadata_unknown": unknowns,
            "source_tier": source_tier,
            "source_published_at": source_date,
            "source_date_provenance": source_date_provenance,
            "model_declared_published_at": declared_date,
            "field_states": field_states,
        }
        audits.append(audit)
        if accepted_by_gate:
            accepted.append(evidence)

    answer_unit_matches = result.get("unit") == case["canonical_unit"]
    answer_value_present = bool(str(result.get("answer_value") or "").strip())
    context_sufficient = bool(
        result.get("action") == "answer"
        and answer_unit_matches
        and answer_value_present
        and accepted
    )
    final_action = result.get("action")
    if result.get("action") == "answer" and not context_sufficient:
        final_action = "abstain"

    primary_count = sum(
        audit["source_tier"] in {"primary", "structured_archive"}
        for audit in audits
        if audit["accepted"]
    )
    matched_fields = sum(
        state == "matched"
        for audit in audits
        if audit["accepted"]
        for state in audit["field_states"].values()
    )
    unknown_count = sum(
        len(audit["metadata_unknown"])
        for audit in audits
        if audit["accepted"]
    )
    confidence = 0.0
    if context_sufficient:
        confidence = min(
            0.98,
            0.42
            + 0.16 * bool(primary_count)
            + 0.08 * min(matched_fields, 3)
            + 0.08 * min(len(accepted), 2)
            - 0.025 * unknown_count,
        )

    hard_conflict_count = sum(len(audit["violations"]) for audit in audits)
    trace = [
        {
            "state": "PLAN",
            "method_version": ATLAS_METHOD_VERSION,
            **route,
        },
        {
            "state": "RETRIEVE",
            "captured_sources": len(sources),
            "candidate_evidence": len(result.get("evidence") or []),
        },
        {
            "state": "AUDIT",
            "hard_conflicts": hard_conflict_count,
            "metadata_unknown": sum(
                len(audit["metadata_unknown"]) for audit in audits
            ),
            "accepted_evidence": len(accepted),
        },
        {
            "state": "RESOLVE",
            "context_sufficient": context_sufficient,
            "confidence": round(confidence, 4),
        },
        {
            "state": "ANSWER" if final_action == "answer" else "ABSTAIN",
            "reason": (
                "evidence sufficient with no explicit metadata conflict"
                if final_action == "answer"
                else "model abstained or evidence failed calibrated sufficiency"
            ),
        },
    ]
    return {
        **result,
        "model_action": result.get("action"),
        "action": final_action,
        "accepted_evidence": accepted,
        "evidence_audits": audits,
        "local_checks": [
            "hard_date_conflict",
            "hard_target_period_conflict",
            "hard_revision_conflict",
            "hard_unit_conflict",
            "calibrated_metadata_unknown",
        ],
        "filter_triggered": result.get("action") == "answer" and final_action == "abstain",
        "context_sufficient": context_sufficient,
        "confidence": round(confidence, 4),
        "atlas_trace": trace,
        "atlas_method_version": ATLAS_METHOD_VERSION,
    }
