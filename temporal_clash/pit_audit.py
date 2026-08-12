from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse


_ABSOLUTE_DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y")


def parse_observed_date(value: Any, *, observed_at: str) -> date | None:
    """Parse provider page-age metadata without inventing missing dates."""

    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    for date_format in _ABSOLUTE_DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            pass
    relative = re.fullmatch(r"(\d+)\s+weeks?\s+ago", text, flags=re.I)
    if relative:
        anchor = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).date()
        return anchor - timedelta(weeks=int(relative.group(1)))
    return None


def _is_sec_url(url: Any) -> bool:
    if not url:
        return False
    host = (urlparse(str(url)).hostname or "").casefold()
    return host == "sec.gov" or host.endswith(".sec.gov")


def _date_status(observed: date | None, cutoff: date) -> str:
    if observed is None:
        return "unknown"
    return "future" if observed > cutoff else "eligible"


def _source_date_map(record: dict[str, Any]) -> dict[str, list[date]]:
    result: dict[str, list[date]] = {}
    for source in record.get("search_sources") or []:
        observed = parse_observed_date(
            source.get("page_age"), observed_at=str(record["run_at"])
        )
        if observed is not None and source.get("url"):
            result.setdefault(str(source["url"]), []).append(observed)
    return result


def _audit_items(
    items: list[dict[str, Any]],
    *,
    cutoff: date,
    observed_at: str,
    date_field: str,
    fallback_dates: dict[str, list[date]] | None = None,
) -> dict[str, Any]:
    statuses = {"eligible": 0, "future": 0, "unknown": 0}
    sec_count = 0
    details = []
    for item in items:
        observed = parse_observed_date(item.get(date_field), observed_at=observed_at)
        if observed is None and fallback_dates and item.get("url"):
            candidates = fallback_dates.get(str(item["url"])) or []
            if candidates:
                observed = max(candidates)
        status = _date_status(observed, cutoff)
        statuses[status] += 1
        sec_count += int(_is_sec_url(item.get("url")))
        details.append(
            {
                "url": item.get("url"),
                "observed_date": observed.isoformat() if observed else None,
                "status": status,
                "date_source": (
                    date_field
                    if parse_observed_date(
                        item.get(date_field), observed_at=observed_at
                    )
                    else "matched_search_source"
                    if observed
                    else "unknown"
                ),
            }
        )
    total = len(items)
    known = statuses["eligible"] + statuses["future"]
    return {
        "total": total,
        **statuses,
        "temporal_metadata_coverage": known / total if total else 0.0,
        "confirmed_future_rate": statuses["future"] / total if total else 0.0,
        "eligible_precision_among_dated": (
            statuses["eligible"] / known if known else None
        ),
        "sec_source_rate": sec_count / total if total else 0.0,
        "details": details,
    }


def audit_record_temporality(record: dict[str, Any]) -> dict[str, Any]:
    cutoff = date.fromisoformat(str(record["case"]["cutoff_date"]))
    observed_at = str(record["run_at"])
    source_dates = _source_date_map(record)
    sources = _audit_items(
        list(record.get("search_sources") or []),
        cutoff=cutoff,
        observed_at=observed_at,
        date_field="page_age",
    )
    citations = _audit_items(
        list(record.get("api_citations") or []),
        cutoff=cutoff,
        observed_at=observed_at,
        date_field="published_at",
        fallback_dates=source_dates,
    )
    accepted_items = list((record.get("result") or {}).get("accepted_evidence") or [])
    accepted = _audit_items(
        accepted_items,
        cutoff=cutoff,
        observed_at=observed_at,
        date_field="published_at",
        fallback_dates=source_dates,
    )
    provenance_fields = (
        "url",
        "published_at",
        "target_period",
        "revision",
        "unit",
        "evidence_text",
    )
    complete = sum(
        all(item.get(field) not in (None, "") for field in provenance_fields)
        for item in accepted_items
    )
    accepted["provenance_complete"] = complete
    accepted["provenance_completeness"] = (
        complete / len(accepted_items) if accepted_items else 0.0
    )
    final_temporally_compliant = bool(accepted_items) and not (
        accepted["future"] or accepted["unknown"]
    )
    return {
        "audit_version": "pit-audit-1.0",
        "cutoff_date": cutoff.isoformat(),
        "candidate_sources": sources,
        "native_citations": citations,
        "accepted_evidence": accepted,
        "candidate_future_exposure": sources["future"] > 0,
        "final_evidence_leakage": accepted["future"] > 0,
        "final_temporally_compliant": final_temporally_compliant,
        "final_provenance_complete": bool(accepted_items) and complete == len(accepted_items),
    }


def aggregate_temporal_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [record.get("point_in_time_audit") or audit_record_temporality(record) for record in records]

    def total(section: str, field: str) -> int:
        return sum(int(audit[section][field]) for audit in audits)

    source_total = total("candidate_sources", "total")
    source_known = total("candidate_sources", "eligible") + total(
        "candidate_sources", "future"
    )
    evidence_total = total("accepted_evidence", "total")
    evidence_known = total("accepted_evidence", "eligible") + total(
        "accepted_evidence", "future"
    )
    return {
        "candidate_sources": source_total,
        "candidate_future_sources": total("candidate_sources", "future"),
        "candidate_unknown_date_sources": total("candidate_sources", "unknown"),
        "candidate_future_source_rate": (
            total("candidate_sources", "future") / source_total if source_total else 0.0
        ),
        "candidate_temporal_metadata_coverage": (
            source_known / source_total if source_total else 0.0
        ),
        "candidate_sec_source_rate": (
            sum(audit["candidate_sources"]["sec_source_rate"] * audit["candidate_sources"]["total"] for audit in audits)
            / source_total
            if source_total
            else 0.0
        ),
        "runs_with_candidate_future_exposure": sum(
            bool(audit["candidate_future_exposure"]) for audit in audits
        ),
        "accepted_evidence": evidence_total,
        "accepted_future_evidence": total("accepted_evidence", "future"),
        "accepted_unknown_date_evidence": total("accepted_evidence", "unknown"),
        "final_evidence_leakage_rate": (
            total("accepted_evidence", "future") / evidence_total
            if evidence_total
            else 0.0
        ),
        "final_evidence_temporal_metadata_coverage": (
            evidence_known / evidence_total if evidence_total else 0.0
        ),
        "final_evidence_provenance_completeness": (
            total("accepted_evidence", "provenance_complete") / evidence_total
            if evidence_total
            else 0.0
        ),
        "runs_with_final_evidence_leakage": sum(
            bool(audit["final_evidence_leakage"]) for audit in audits
        ),
        "temporally_compliant_run_rate": (
            sum(bool(audit["final_temporally_compliant"]) for audit in audits)
            / len(audits)
            if audits
            else 0.0
        ),
    }
