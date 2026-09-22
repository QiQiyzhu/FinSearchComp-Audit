"""Bounded two-issuer research with explicit period comparability.

Each issuer keeps its own validated report. Cross-company arithmetic is allowed
only on identical annual windows and units, never just on matching FY labels.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re
from typing import Any, Callable

from .config import Settings
from .answers import compute_metric
from .engine import ResearchEngine, markdown_export, number
from .sources import COMPANIES, ResearchError, now


def issuer_question(question: str, tickers: list[str]) -> str:
    """Remove the selected issuer names; preserve financial and period requests."""
    result = question
    for ticker in tickers:
        names = [ticker, COMPANIES[ticker]["name"], *COMPANIES[ticker]["aliases"]]
        for name in sorted(set(names), key=len, reverse=True):
            pattern = rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])" if name.isascii() else re.escape(name)
            result = re.sub(pattern, " ", result, flags=re.I)
    year_text = re.sub(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", "", question)
    if len(set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", year_text))) <= 1:
        # Comparing two issuers is not a request for year-over-year growth.
        # Explicit '增长/growth' requests survive this normalization.
        result = re.sub(r"并列|比较|对比|\bcompar(?:e|ing|ison)\b|\bversus\b|\bvs\.?", " ", result, flags=re.I)
    return result.strip()


def namespace_report(report: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(report)
    ticker = result["ticker"]
    # Rewrite both IDs and any embedded [E01] references in text.
    identifiers = {row["id"]: f"{ticker}-{row['id']}" for key in ("evidence", "claims", "answers") for row in result.get(key, [])}

    def rewrite(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {name: rewrite(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item, key) for item in value]
        if isinstance(value, str):
            if key == "id" or key.endswith("_ids") or key in {"evidence_id", "answer_id"}:
                return identifiers.get(value, value)
            return re.sub(r"\[([A-Z]\d+)\]", lambda match: "[" + identifiers.get(match[1], match[1]) + "]", value)
        return value

    return rewrite(result)


def unrounded_metric(report: dict[str, Any], metric: dict[str, Any]) -> Decimal | None:
    if metric["unit"] == "USD":
        return Decimal(metric["value"])
    facts = {}
    references = metric.get("value_evidence_ids", metric["evidence_ids"])
    for evidence in report.get("evidence", []):
        if evidence["id"] in references and evidence["period_start"] == metric["period_start"] and evidence["period_end"] == metric["period_end"] and evidence["unit"] == "USD":
            facts[evidence["metric"]] = {"value": evidence["value"], "start": evidence["period_start"], "end": evidence["period_end"], "evidence_id": evidence["id"], "fiscal_year": evidence.get("fiscal_year")}
    result = compute_metric(metric["id"], facts)
    return result.get("raw_value") if not result.get("reason") else None


def comparison_rows(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    left, right = ({row["id"]: row for row in report["metrics"]} for report in reports)
    rows = []
    for identifier in dict.fromkeys([*left, *right]):
        a, b = left.get(identifier), right.get(identifier)
        reference = a or b
        same_window = bool(a and b and a["period_start"] == b["period_start"] and a["period_end"] == b["period_end"])
        comparable = bool(same_window and a["unit"] == b["unit"])
        row = {"metric_id": identifier, "label": reference["label"], "unit": reference["unit"],
               "left": {"ticker": reports[0]["ticker"], **a} if a else None,
               "right": {"ticker": reports[1]["ticker"], **b} if b else None,
               "comparable": comparable, "difference": None,
               "note": "相同期间与单位；差额仅描述历史财务数据。" if comparable else "财年起止日期不同，仅并列展示；不计算差额或据此排名。"}
        if not a or not b:
            row["note"] = "一家公司缺少同定义的可验证指标；缺失值不按零处理。"
        elif not comparable and same_window:
            row["note"] = "单位不一致，不计算差额。"
        if comparable:
            a_value, b_value = unrounded_metric(reports[0], a), unrounded_metric(reports[1], b)
            if a_value is None or b_value is None:
                row.update(comparable=False, note="缺少未舍入的原始操作数，不能用展示值计算精确差额。")
                rows.append(row)
                continue
            delta = a_value - b_value
            unit = "percentage_points" if a["unit"] == "%" else a["unit"]
            row["difference"] = {"value": number(delta), "unit": unit,
                                 "display_value": number(delta) + (" 个百分点" if unit == "percentage_points" else " " + unit),
                                 "formula": f"{reports[0]['ticker']} ({a.get('formula', a['value']) if unit == 'percentage_points' else a['value']}) − {reports[1]['ticker']} ({b.get('formula', b['value']) if unit == 'percentage_points' else b['value']})；仅最终差值舍入",
                                 "evidence_ids": list(dict.fromkeys(a.get("value_evidence_ids", a["evidence_ids"]) + b.get("value_evidence_ids", b["evidence_ids"])))}
        rows.append(row)
    return rows


class WorkflowEngine:
    def __init__(self, settings: Settings, base: ResearchEngine | None = None):
        self.base = base or ResearchEngine(settings)

    def run(self, request: dict[str, Any], emit: Callable | None = None) -> dict[str, Any]:
        other = request.get("compare_with")
        if not other:
            return self.base.run(request, emit)
        tickers = [request["ticker"], other]
        if other not in COMPANIES or tickers[0] == other:
            raise ResearchError("INVALID_COMPARISON", "公司对比需要两个不同的受支持发行人。")
        question = issuer_question(request["question"], tickers)
        reports, trace = [], []
        for ticker in tickers:
            def relay(entry: dict[str, Any], issuer: str = ticker) -> None:
                event = {**entry, "step": f"{issuer}:{entry['step']}", "label": f"{issuer} · {entry['label']}", "ticker": issuer}
                trace.append(event)
                if emit:
                    emit(event)
            child_request = {key: value for key, value in request.items() if key != "compare_with"}
            child_request.update(ticker=ticker, question=question)
            reports.append(namespace_report(self.base.run(child_request, relay)))
        rows = comparison_rows(reports)
        comparable_count = sum(row["comparable"] for row in rows)
        event = {"step": "compare", "label": "跨公司口径核验", "status": "completed" if comparable_count == len(rows) else "partial",
                 "detail": f"{len(rows)} 项并列指标中，{comparable_count} 项具有相同起止期间和单位；其余不计算跨公司差额。", "timestamp": now()}
        trace.append(event)
        if emit:
            emit(event)
        complete = all(report["coverage"]["question_supported"] for report in reports)
        policy = "逐项核对两个发行人的年度起止日期、单位与标签定义。财年名称相同不等于期间相同；无法对齐时只并列展示，不排名、不推断投资优劣。"
        answers = [{**answer, "ticker": report["ticker"], "label": f"{report['ticker']} · {answer['label']}"} for report in reports for answer in report.get("answers", [])]
        evidence = [row for report in reports for row in report["evidence"]]
        claims = [{**claim, "text": f"{report['ticker']}：{claim['text']}"} for report in reports for claim in report["claims"]]
        model_reports = [report["model"] for report in reports]
        if all(model["used"] for model in model_reports):
            model_status = "completed"
        elif any(model["used"] for model in model_reports):
            model_status = "partial"
        else:
            states = {model["status"] for model in model_reports}
            model_status = next((status for status in ["rejected", "unavailable", "scope_abstained", "not_configured", "not_requested"] if status in states), "not_requested")
        usage = {key: sum((model.get("usage") or {}).get(key, 0) or 0 for model in model_reports) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        summary = "\n\n".join(f"{report['ticker']}：{report['summary']}" for report in reports)
        result = {"report_type": "comparison", "title": f"{' × '.join(tickers)} · 年度财务对比", "ticker": " / ".join(tickers),
                  "company": " × ".join(report["company"] for report in reports), "question": request["question"],
                  "as_of": request["as_of"], "mode": request["mode"], "data_mode": reports[0]["data_mode"], "generated_at": now(),
                  "summary": summary, "companies": reports, "comparison": {"tickers": tickers, "period_policy": policy, "rows": rows},
                  "metrics": [], "answers": answers, "claims": claims, "evidence": evidence,
                  "strengths": [], "risk_findings": [],
                  "verdict": {"stance": "comparative_review" if complete else "insufficient_evidence", "label": "并列复核，保留期间差异" if complete else "部分问题证据不足",
                              "rationale": policy, "confidence": "未作投资胜率估计", "conditions": ["取得同一时点估值与完整业务风险后，再形成投资判断。"], "evidence_ids": [], "basis": "period-aware comparison"},
                  "risks": list(dict.fromkeys(item for report in reports for item in report["risks"])),
                  "limitations": [policy] + [f"{report['ticker']}：{item}" for report in reports for item in report["limitations"]],
                  "next_steps": list(dict.fromkeys(item for report in reports for item in report["next_steps"])), "trace": trace,
                  "model": {"provider": "DeepSeek", "used": any(model["used"] for model in model_reports), "model": model_reports[0]["model"],
                            "status": model_status,
                            "strategy": "per_issuer_verified_claim_selection", "detail": "每家公司独立验证和归纳；比较差额由程序计算。", "usage": usage, "issuer_runs": model_reports},
                  "plan": {"scope": "two-company annual fundamentals", "supported": complete, "focus": "comparison", "gaps": [gap for report in reports for gap in report["plan"]["gaps"]]},
                  "discoveries": [item for report in reports for item in report.get("discoveries", [])],
                  "coverage": {"verified_claims": len(claims), "cited_claims": sum(bool(claim["evidence_ids"]) for claim in claims), "evidence_count": len(evidence),
                               "filing_cutoff": request["as_of"], "question_supported": complete, "missing_metrics": [],
                               "missing_requested_metrics": [f"{report['ticker']}:{slot}" for report in reports for slot in report["coverage"]["missing_requested_metrics"]],
                               "answered_questions": sum(report["coverage"].get("answered_questions", 0) for report in reports),
                               "total_questions": sum(report["coverage"].get("total_questions", 0) for report in reports),
                               "data_as_of": None, "source_retrieved_at": min(report["coverage"]["source_retrieved_at"] for report in reports)},
                  "method": {"name": "Period-aware financial comparison", "version": "2.0", "checks": ["issuer-isolation", "namespaced-citations", "exact-period-comparability", "no-missing-as-zero"]}}
        return result


def export_report(report: dict[str, Any]) -> str:
    if report.get("report_type") != "comparison":
        return markdown_export(report)
    lines = [f"# {report['title']}", "", report["question"], "", report["comparison"]["period_policy"], "",
             "| 指标 | 公司 A | 公司 B | 口径 |", "| --- | --- | --- | --- |"]
    for row in report["comparison"]["rows"]:
        cells = [f"{item['ticker']} {item['display_value']} ({item['period_start']} — {item['period_end']})" if item else "证据缺失" for item in (row["left"], row["right"])]
        lines.append(f"| {row['label']} | {cells[0]} | {cells[1]} | {row['note']} |")
    return "\n".join(lines) + "\n\n" + "\n\n".join(markdown_export(child) for child in report["companies"])
