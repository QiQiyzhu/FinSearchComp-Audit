"""Research assembly: direct answers first, optional model highlights second."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .answers import build_answers, collect_periods, compute_metric, metric_table, rounded
from .planning import metric_label, plan_question
from .sources import COMPANIES, DEMO_CUTOFF, METRICS, ResearchError, now


def evidence_findings(metrics: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    indexed = {metric["id"]: metric for metric in metrics}
    strengths, risks = [], []

    def finding(destination, text, involved, kind="derived"):
        refs = sorted({ref for metric in involved for ref in metric["evidence_ids"]})
        destination.append({"text": text, "evidence_ids": refs, "kind": kind})

    revenue = indexed.get("revenue")
    if revenue and "change_pct" in revenue:
        growth = Decimal(revenue["change_pct"])
        if growth != 0:
            finding(strengths if growth > 0 else risks, f"收入较比较年度变化 {revenue['change_pct']}%，{'支持增长延续性研究' if growth > 0 else '需核对需求、价格或业务变化'}。", [revenue])
    for key in ["operating_margin", "net_margin"]:
        metric = indexed.get(key)
        if metric and "change_pp" in metric and Decimal(metric["change_pp"]) != 0:
            improvement = Decimal(metric["change_pp"])
            finding(strengths if improvement > 0 else risks, f"{metric['label']}较比较年度变化 {metric['change_pp']} 个百分点；变化原因仍需附注或分部证据。", [metric])
    fcf = indexed.get("free_cash_flow")
    if fcf:
        finding(strengths if Decimal(fcf["value"]) > 0 else risks, f"已披露口径的自由现金流为 {fcf['display_value']}，计算仅扣除 PP&E 现金资本支出。", [fcf])
    conversion = indexed.get("cash_conversion")
    if conversion:
        finding(strengths if Decimal(conversion["value"]) >= 100 else risks, f"经营现金流 / 净利润为 {conversion['display_value']}；需结合营运资本及非现金项目解释。", [conversion])
    ocf, capex = indexed.get("operating_cash_flow"), indexed.get("capital_expenditure")
    if ocf and capex and "change_pct" in ocf and "change_pct" in capex and Decimal(capex["change_pct"]) > Decimal(ocf["change_pct"]):
        finding(risks, f"PP&E 现金资本支出增长 {capex['change_pct']}%，高于经营现金流变化 {ocf['change_pct']}%；应核对投入回收期。", [ocf, capex])
    return strengths, risks


def run_research(engine, request: dict[str, Any], emit=None) -> dict[str, Any]:
    # Imported at call time to retain the established engine test seams and
    # avoid moving model/network behavior into the arithmetic layer.
    from .engine import WATCH, search_context, synthesize, validate_citations

    trace = []

    def event(step, label, detail, status="completed"):
        row = {"step": step, "label": label, "detail": detail, "status": status, "timestamp": now()}
        trace.append(row)
        if emit:
            emit(row)

    ticker, as_of, mode, question = (request[key] for key in ("ticker", "as_of", "mode", "question"))
    plan = plan_question(question, ticker)
    event("plan", "问题拆解", f"识别 {len(plan['requested_metrics'])} 个指标、{len(plan['requests'])} 个数值/变化请求；每项独立核验期间与依赖。")
    source = engine.sources.live(ticker) if mode == "live" else engine.sources.snapshot(ticker)
    event("retrieve", "官方来源检索", f"{'实时 SEC API' if source.data_mode == 'live' else '历史 SEC 快照'}；{'已验证缓存' if source.cache_hit else 'SHA-256 完整性校验'}。")
    facts, periods, evidence, missing = collect_periods(source, ticker, as_of, plan)
    latest_fiscal_year = next((row.get("fiscal_year") for key, row in facts.items() if not key.endswith("_prior")), plan["fiscal_year"])
    event("temporal", "财年与期间核验", f"filed ≤ {as_of}；财年由申报锚点和年度期间序列确定，不把比较行的 filing fy 或自然年直接当财年。")
    metrics, claims = metric_table(facts)
    answers = build_answers(plan, periods, latest_fiscal_year)
    for answer in answers:
        if answer["answerability"] == "answered":
            claims.append({"id": f"C{len(claims) + 1:02d}", "answer_id": answer["id"], "metric_id": answer["metric_id"],
                           "operation": answer["operation"], "text": answer["text"], "kind": "derived" if answer["operation"] != "value" or answer["metric_id"] not in METRICS else "verified", "evidence_ids": answer["evidence_ids"]})
    answered = [answer for answer in answers if answer["answerability"] == "answered"]
    unanswered = [answer for answer in answers if answer["answerability"] != "answered"]
    event("compute", "精确计算", f"完成 {len(answered)}/{len(answers)} 个直接答案；变化额、变化率及百分点分别计算，保留未舍入中间值。")
    if not validate_citations(claims, evidence, as_of):
        raise ResearchError("EVIDENCE_VALIDATION", "答案引用或截止日核验失败，报告未发布。")
    event("verify", "逐问题复核", f"{len(answered)} 个答案通过；{len(unanswered)} 个问题保留缺口，不用其他年度或无关指标替代。", "partial" if unanswered else "completed")
    gaps = list(dict.fromkeys(answer["text"] for answer in unanswered))
    watch_ids = list(dict.fromkeys([focus for focus in plan["focuses"] if focus in WATCH] + ["valuation", "risks"]))[:4]
    selection = {"claim_ids": [claim["id"] for claim in claims if claim.get("answer_id")][:24], "watch_ids": watch_ids}
    model = {"provider": "DeepSeek", "used": False, "model": engine.settings.deepseek_model, "status": "not_requested",
             "strategy": "verified_claim_selection", "detail": "直接答案由确定性计算生成；未调用模型。"}
    if mode in {"snapshot", "live"} and engine.settings.deepseek_api_key and answered and plan["supported"]:
        event("synthesize_start", "模型整理核验结果", "直接答案保持完整；DeepSeek 仅选择已验证事实及后续复核动作。", "running")
        try:
            synthesis = synthesize(engine.settings, question, plan, claims)
            selection = synthesis["selection"]
            model.update(used=True, status="completed", detail="DeepSeek 已整理核验后的研究要点；完整直接答案不被模型选择截断。", selection=selection, **synthesis["receipt"])
        except ResearchError as error:
            model.update(status="rejected" if error.code == "MODEL_GROUNDING_REJECTED" else "unavailable", detail=error.message)
            gaps.append(error.message + " 已保留完整的确定性答案。")
    elif mode in {"snapshot", "live"}:
        model.update(status="not_configured" if not engine.settings.deepseek_api_key else "scope_abstained", detail="问题范围、证据或模型配置不足，未调用付费模型。")
    event("synthesize", "研究要点归纳", model["detail"], "completed" if mode == "demo" or model["used"] else "partial")
    # The direct summary includes every answered request. A model never decides
    # which subquestions disappear from the result.
    summary_parts = [answer["text"] + " " + " ".join(f"[{identifier}]" for identifier in answer["evidence_ids"]) for answer in answered]
    summary = " ".join(summary_parts)
    if unanswered:
        summary = f"已回答 {len(answered)}/{len(answers)} 项；未覆盖部分：" + " ".join(answer["text"] for answer in unanswered) + (" 已核验答案：" + summary if summary else "")
    if not summary:
        summary = "没有可回答的受支持年度财务请求，请明确研究指标和财年。"
    strengths, risk_findings = evidence_findings(metrics)
    supported = bool(answers) and not unanswered
    stance = "insufficient_evidence" if not supported else "mixed"
    label = "证据不足 · 存在未答子问题" if not supported else "已核验 · 条件判断待补充"
    rationale = "判断仅覆盖已验证的年度事实；尚未回答的请求会保留具体缺口。"
    if supported and strengths and not risk_findings:
        stance, label = "constructive", "已验证积极信号 · 估值待核验"
        rationale = "研究优先级由下列证据支持的积极信号决定；同时保留反向信号，不能据此直接推导股票价格或买入结论。"
    if supported and risk_findings and not strengths:
        stance, label = "cautious", "审慎研究 · 需解释负向信号"
    if supported and strengths and risk_findings:
        stance, label = "mixed", "信号并存 · 逐项权衡"
        rationale = "证据同时支持积极与审慎信号；不按条目数量加权，不据此推导单一投资方向。"
    verdict = {"stance": stance, "label": label, "rationale": rationale, "confidence": "medium" if supported else "low",
               "conditions": ["只有在后续披露延续已验证信号、且估值与风险证据补齐后，才考虑提高研究优先级。", "若当前积极变化来自一次性因素，或现金投入持续超过经营改善，应下调判断。"],
               "evidence_ids": sorted({ref for finding in strengths + risk_findings for ref in finding["evidence_ids"]}),
               "basis": "deterministic evidence-linked research signals; no price forecast", "calibration": "not_calibrated"}
    limitations = list(dict.fromkeys(gaps + [
        "年度财报研究不包含实时行情、估值倍数、预测或交易执行；无法直接给出买卖指令、目标价或收益保证。",
        "截止日采用 SEC filed 日粒度；后来的 Company Facts 抓取不等同于截止日当时完整归档的数据库。",
        "只映射整家公司标准 us-gaap 年度标签；自定义标签、分部与财报附注可能缺失。",
        "自由现金流 = 经营现金流 − PP&E 现金资本支出；现金转换率 = 经营现金流 / 正净利润，均为明确研究口径。",
    ]))
    if source.data_mode == "snapshot":
        limitations.insert(0, f"历史快照只包含截至 {DEMO_CUTOFF} 已申报的部分财年；抓取时间 {source.retrieved_at}，不代表当前状况。")
    discoveries = []
    if mode == "live" and engine.settings.tavily_api_key:
        discoveries, notes = search_context(engine.settings, question, ticker, as_of)
        limitations.extend(notes)
        event("web", "扩展来源发现", f"{len(discoveries)} 条有日期的网页发现仅供继续核验，不纳入已验证计算。")
    event("publish", "答案与证据发布", "直接答案、未答原因、数学口径和证据来源分别保存。")
    lookup = {claim["id"]: claim for claim in claims}
    return {"title": f"{COMPANIES[ticker]['name']} · {'FY' + str(latest_fiscal_year) if latest_fiscal_year is not None else '年度'} 研究简报",
            "ticker": ticker, "company": COMPANIES[ticker]["name"], "question": question, "as_of": as_of, "mode": mode,
            "data_mode": source.data_mode, "generated_at": now(), "summary": summary, "verdict": verdict,
            "answers": answers, "metrics": metrics, "claims": claims, "evidence": evidence, "strengths": strengths, "risk_findings": risk_findings,
            "risks": [finding["text"] for finding in risk_findings] + ["尚未取得同一时点估值和完整业务风险披露；历史基本面不能决定买入价格。"],
            "limitations": limitations, "next_steps": [WATCH[item] for item in selection["watch_ids"]], "trace": trace, "model": model,
            "model_highlights": [lookup[identifier] for identifier in selection["claim_ids"] if identifier in lookup] if model["used"] else [],
            "plan": plan, "discoveries": discoveries,
            "coverage": {"verified_claims": len(claims), "cited_claims": len(claims), "evidence_count": len(evidence), "filing_cutoff": as_of,
                         "missing_metrics": missing, "missing_requested_metrics": list(dict.fromkeys(metric for answer in unanswered for metric in answer.get("missing_inputs", []))),
                         "question_supported": supported, "answered_questions": len(answered), "total_questions": len(answers),
                         "data_as_of": facts.get("revenue", {}).get("end"), "source_retrieved_at": source.retrieved_at},
            "method": {"name": "ATLAS-inspired typed evidence workflow", "version": "2.0", "checks": ["filing-cutoff", "fiscal-period-label", "exact-requested-periods", "metric-dependencies", "unit-match", "tag-conflict", "decimal-calculation", "nonpositive-denominator", "citation-integrity", "per-question-answerability"]}}
