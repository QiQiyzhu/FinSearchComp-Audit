"""Financial terminal orchestration with a mandatory evidence delivery gate.

The model can rank verified claims, never supply financial values. Selection,
periods, arithmetic and abstention remain independently inspectable.
"""
from __future__ import annotations

from fractions import Fraction
from functools import lru_cache
from typing import Any

from .config import Settings
from .engine import WATCH, synthesize
from .sources import ResearchError, now
from .terminal_data import evidence_gate, load_cube, query_metric, select_state
from .workflows import WorkflowEngine


@lru_cache(maxsize=1)
def terminal_cube():
    return load_cube()


def verify_answer(cube: dict, candidate: dict, *, ticker: str, cutoff: str,
                  fiscal_year: int, metric_id: str) -> dict:
    """Reject correct-looking but unsupported, incomplete or misbound values.

    Re-query the trusted financial kernel. This verifies the delivery boundary,
    not the kernel's correctness; the latter has a separate independent oracle.
    """
    expected = query_metric(cube, ticker=ticker, cutoff=cutoff, fiscal_year=fiscal_year, metric_id=metric_id)
    checks = {"available": expected["status"] == "available" and candidate.get("status") == "available"}
    checks["entity"] = candidate.get("ticker") == ticker
    checks["metric"] = candidate.get("metric_id") == metric_id
    checks["period"] = (candidate.get("fiscal_year") == fiscal_year and
                        all(candidate.get(key) == expected.get(key) for key in ("period_start", "period_end", "annual_id")))
    checks["unit"] = candidate.get("unit") == expected.get("unit")
    try:
        checks["value"] = Fraction(str(candidate.get("value"))) == Fraction(str(expected.get("value")))
    except (ValueError, ZeroDivisionError):
        checks["value"] = False
    refs = candidate.get("evidence_ids", [])
    checks["complete_operands"] = bool(refs) and set(refs) == set(expected.get("evidence_ids", []))
    provenance = evidence_gate(cube, refs, cutoff, ticker=ticker)
    checks["source_time"] = provenance["passed"]
    return {"passed": all(checks.values()), "checks": checks, "provenance": provenance}


class TerminalEngine:
    def __init__(self, settings: Settings, cube: dict | None = None):
        self.settings = settings
        self.cube = cube

    def run(self, request: dict[str, Any], emit=None) -> dict:
        cube = self.cube if self.cube is not None else terminal_cube()
        trace = []

        def event(step, label, detail, status="completed"):
            row = {"step": step, "label": label, "detail": detail, "status": status, "timestamp": now()}
            trace.append(row)
            if emit:
                emit(row)

        ticker, cutoff, year = request["ticker"], request["as_of"], request["fiscal_year"]
        tickers = [ticker] + ([request["compare_with"]] if request.get("compare_with") else [])
        metric_ids = list(dict.fromkeys(request["metric_ids"]))
        event("plan", "研究计划", f"{len(tickers)} 家公司 × {len(metric_ids)} 个指标；FY{year}；截止 {cutoff}。")
        answers, evidence_ids, sources, gaps = [], set(), [], []
        for entity in tickers:
            state = select_state(cube, entity, cutoff)
            selected = state.get("event")
            sources.append({"ticker": entity, "status": state["status"], "selected_available_from": selected["available_from"] if selected else None})
            for metric_id in metric_ids:
                result = query_metric(cube, ticker=entity, cutoff=cutoff, fiscal_year=year, metric_id=metric_id)
                answer = {**result, "id": f"{entity}-{year}-{metric_id}", "ticker": entity,
                          "metric_id": metric_id, "label": cube["metric_catalog"][metric_id]["label"]}
                if result["status"] == "available":
                    gate = verify_answer(cube, answer, ticker=entity, cutoff=cutoff, fiscal_year=year, metric_id=metric_id)
                    answer["gate"] = gate
                    if not gate["passed"]:
                        answer.update(status="verification_failed", value=None, display_value="—", reason="交付核验未通过；该数值已被隔离。")
                    else:
                        evidence_ids.update(answer["evidence_ids"])
                if answer["status"] != "available":
                    gaps.append({"ticker": entity, "metric_id": metric_id, "status": answer["status"], "reason": answer.get("reason")})
                answers.append(answer)
        event("temporal", "历史版本选择", "仅使用申报日期下一日及此前可得的 SEC 年报条目；不从后续版本补数。")
        event("retrieve", "原始操作数检索", f"取得 {len(evidence_ids)} 条可追溯 XBRL 记录，保留标签、期间、申报号及哈希。")
        available = [row for row in answers if row["status"] == "available"]
        event("compute", "确定性财务计算", f"{len(available)}/{len(answers)} 项获得数值；缺失依赖与冲突作为显式缺口。")
        event("verify", "答案交付检查", "检查公司、指标、年度期间、单位、数值、完整操作数及证据时间。", "completed" if not gaps else "partial")
        claims = [{"id": f"C{index:02d}", "metric_id": row["metric_id"], "answer_id": row["id"],
                   "text": f"{row['ticker']} FY{row['fiscal_year']} {row['label']}：{row['display_value']}（{row['period_start'] + ' 至 ' if row['period_start'] else '截至 '}{row['period_end']}）。",
                   "evidence_ids": row["evidence_ids"]} for index, row in enumerate(available, 1)]
        selection = {"claim_ids": [row["id"] for row in claims], "watch_ids": ["risks", "valuation"]}
        model = {"used": False, "status": "deterministic", "role": "organize_verified_claims_only"}
        if request["mode"] == "snapshot" and claims:
            try:
                organized = synthesize(self.settings, request["question"], {"requested_metrics": metric_ids}, claims)
                selection = organized["selection"]
                model.update(used=True, status="completed", provider="DeepSeek", configured_model=self.settings.deepseek_model, receipt=organized["receipt"])
            except ResearchError as exc:
                model.update(status="fallback", reason=exc.message if hasattr(exc, "message") else str(exc))
        elif request["mode"] == "snapshot":
            model.update(status="skipped_no_verified_claims")
        chosen = {row["id"]: row for row in claims}
        # The provider prioritizes a bounded subset; append every other verified
        # claim so a two-issuer brief cannot omit a company or requested metric.
        ordered_ids = selection["claim_ids"] + [key for key in chosen if key not in selection["claim_ids"]]
        brief = [chosen[identifier] for identifier in ordered_ids]
        event("synthesize", "研究摘要", "DeepSeek 已完成受约束的事实排序。" if model["used"] else "使用完整的已验证事实摘要；模型状态单独记录。")
        periods = {(row.get("period_start"), row.get("period_end")) for row in available if row.get("period_start")}
        limitations = [cube["policy"]["source_limitation"], "这是历史财务证据研究；不包含估值所需的股价、未来预测或交易执行。"]
        if len(tickers) > 1 and len(periods) > 1:
            limitations.append("公司财年期间不同；仅并列展示，未计算跨公司差值或排名。")
        return {"report_type": "terminal", "version": "3.0.0", "title": " × ".join(tickers) + f" · FY{year} 财务研究",
                "question": request["question"], "as_of": cutoff, "fiscal_year": year, "tickers": tickers,
                "data_mode": request["mode"], "generated_at": now(), "answers": answers,
                "summary": " ".join(row["text"] for row in brief) if brief else "当前截止日与指标要求下没有通过验证的数值，请查看证据缺口。",
                "brief": brief, "gaps": gaps, "evidence": [cube["evidence"][key] for key in sorted(evidence_ids)],
                "model": model, "trace": trace, "policy": cube["policy"], "sources": sources,
                "coverage": {"available": len(available), "total": len(answers)}, "limitations": limitations,
                "next_steps": [WATCH[key] for key in selection["watch_ids"]]}


class ApplicationEngine:
    def __init__(self, settings: Settings):
        from .live_engine import LiveResearchEngine
        self.legacy = WorkflowEngine(settings)
        self.terminal = TerminalEngine(settings)
        self.live = LiveResearchEngine(settings)

    def run(self, request, emit=None):
        if request.get("workflow") == "live_research":
            return self.live.run(request, emit)
        engine = self.terminal if request.get("workflow") == "terminal" else self.legacy
        return engine.run(request, emit)


def terminal_markdown(report: dict) -> str:
    lines = [f"# {report['title']}", "", f"研究问题：{report['question']}", "", f"截止日：{report['as_of']} · 模型状态：{report['model']['status']}", "",
             report["summary"], "", "| 公司 | 指标 | 数值 | 财务期间 | 状态 | 证据 |", "| --- | --- | --- | --- | --- | --- |"]
    for row in report["answers"]:
        lines.append(f"| {row['ticker']} | {row['label']} | {row['display_value']} | {row.get('period_start', '—')} / {row.get('period_end', '—')} | {row['status']} | {', '.join(row['evidence_ids'])} |")
    lines += ["", "## 证据与计算"]
    for row in report["answers"]:
        lines.append(f"- {row['ticker']} {row['label']}：{row.get('formula') or row.get('reason')}")
    for row in report["evidence"]:
        lines.append(f"- [{row['id']}]({row['url']}) · {row['taxonomy_tag']} · {row['value']} {row['unit']} · filed {row['filed']} · available {row['available_from']} · `{row['fact_sha256']}`")
    lines += ["", "## 边界与后续核验", *[f"- {item}" for item in report["limitations"] + report["next_steps"]]]
    return "\n".join(lines) + "\n"
