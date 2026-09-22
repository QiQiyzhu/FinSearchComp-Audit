"""A deliberately bounded bilingual financial question compiler.

The compiler describes individual requested metrics and operations. It does
not use a model, assign a ticker, silently pick a quarter, or invent periods.
"""
from __future__ import annotations

import re
from typing import Any

from .sources import COMPANIES, METRICS

DERIVED = {
    "operating_margin": {"label": "营业利润率", "inputs": ["operating_income", "revenue"], "unit": "%"},
    "net_margin": {"label": "净利润率", "inputs": ["net_income", "revenue"], "unit": "%"},
    "free_cash_flow": {"label": "自由现金流（OCF − PP&E）", "inputs": ["operating_cash_flow", "capital_expenditure"], "unit": "USD"},
    "rd_ratio": {"label": "研发费用率（研发 / 收入）", "inputs": ["research_and_development", "revenue"], "unit": "%"},
    "cash_conversion": {"label": "现金转换率（经营现金流 / 净利润）", "inputs": ["operating_cash_flow", "net_income"], "unit": "%"},
}

PATTERNS = [
    ("free_cash_flow", r"自由现金流|free\s+cash\s*flow|\bfcf\b"),
    ("operating_margin", r"营业利润率|经营利润率|营业收益率|operating\s+(?:profit\s+)?margin"),
    ("net_margin", r"净利润率|净利率|net\s+(?:profit\s+|income\s+)?margin"),
    ("rd_ratio", r"研发(?:费用|投入|支出)?(?:率|强度)|研发.{0,12}(?:占.{0,5}(?:收入|营收)|(?:收入|营收)(?:比|占比))|r&d\s+(?:intensity|ratio)|research\s+and\s+development.{0,25}(?:revenue|ratio|intensity)"),
    ("cash_conversion", r"现金(?:转换|转化)率|cash\s+conversion(?!\s+cycle)(?:\s+(?:ratio|rate))?|经营现金流.{0,8}(?:除以|/|占).{0,5}净利润|ocf\s*/\s*net\s+income"),
    ("operating_cash_flow", r"经营(?:活动)?(?:产生的)?现金流(?:量)?(?:净额)?|operating\s+cash\s*flow|cash\s*flow\s+from\s+operations|\bocf\b"),
    ("capital_expenditure", r"资本(?:性)?支出|资本开支|\bcapex\b|capital\s+expenditure|pp&e"),
    ("operating_income", r"营业利润(?!率)|经营利润(?!率)|operating\s+(?:income|profit)(?!\s+margin)"),
    ("net_income", r"净利润(?!率)|净收益|net\s+(?:income|profit)(?!\s+margin)"),
    ("research_and_development", r"研发(?:费用|投入|支出)?|r&d|research\s+and\s+development"),
    ("revenue", r"营业收入|营收|收入|净销售额|revenue|net\s+sales|\bsales\b"),
]


def metric_label(metric: str) -> str:
    return DERIVED.get(metric, METRICS.get(metric, {})).get("label", metric)


def years_in(text: str) -> list[int]:
    text = re.sub(r"(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)", "", text)
    text = re.sub(r"(?:截至|截止|as\s+of)\s*(?:19|20)\d{2}年\d{1,2}月\d{1,2}日", "", text, flags=re.I)
    matches = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text)
    return sorted({int(value) for value in matches})


def plan_question(question: str, ticker: str) -> dict[str, Any]:
    lower = question.lower()
    years = years_in(question)
    gaps: list[str] = []
    scoped = [
        (r"股价|目标价|市盈率|市净率|估值|市值|买入价|卖出价|收益率|回报率|\bprice\b|valuation|market cap|\bp/e\b|\broe\b|\broa\b", "价格、估值或投资回报指标缺少同一时点的行情及估值输入。"),
        (r"资产负债|负债率|现金余额|净债务|总资产|存货|债券|每股|股息|分红|\beps\b|balance sheet|dividend|debt ratio|inventory", "资产负债表、每股或分配指标尚无受验证的标签映射。"),
        (r"毛利|gross\s+margin|gross\s+profit|ebitda|息税折旧|现金转换周期|cash\s+conversion\s+cycle", "毛利、EBITDA 或现金转换周期不在当前已验证指标范围内。"),
        (r"分部|销量|销售量|用户数|市场份额|订单量|iphone|ipad|segment|unit sales|market share|ai收入|ai相关收入|ai revenue", "产品、分部和经营数量指标不在整家公司年度标准标签范围内。"),
        (r"天气|写诗|诗歌|笑话|weather|write a poem", "该请求不属于当前金融研究工作流。"),
        (r"\bq[1-4]\b|季度|季报|quarter|月度|monthly|\bttm\b|滚动十二|过去十二个月|过去12个月", "只支持年度 10-K 指标，季度、月度或 TTM 期间不在证据范围内。"),
        (r"明年|未来.*(?:收入|营收|利润)|预测|forecast|next year", "历史报表不能单独支持未来预测，本次不生成预测数值。"),
        (r"cagr|复合增长", "当前支持年度值和两个明确端点之间的变化，不生成未验证的复合增长率。"),
    ]
    for pattern, message in scoped:
        if re.search(pattern, lower):
            gaps.append(message)
    for other, company in COMPANIES.items():
        if other != ticker and (re.search(rf"(?<![a-z]){other.lower()}(?![a-z])", lower) or any(alias in lower for alias in company["aliases"])):
            gaps.append("单公司任务包含其他发行人；请使用同业对比工作流。")
            break
    if len(years) > 4:
        gaps.append("单次请求最多支持四个明确年度；请缩小期间范围。")
    range_match = re.search(r"((?:19|20)\d{2})\s*(?:年|财年)?\s*(?:到|至|–|—|through|to)\s*(?:FY\s*)?((?:19|20)\d{2})", question, re.I)
    if range_match and abs(int(range_match[2]) - int(range_match[1])) > 1:
        gaps.append("连续多年范围未展开为完整年度序列；请明确列出所需年度或仅要求两个端点变化。")

    occupied: list[tuple[int, int]] = []
    matches: list[tuple[int, int, str]] = []
    for metric, pattern in PATTERNS:
        for match in re.finditer(pattern, lower):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            occupied.append(match.span())
            matches.append((match.start(), match.end(), metric))
    for generic_cash in re.finditer(r"现金流|cash\s*flow", lower):
        if not any(generic_cash.start() < b and generic_cash.end() > a for a, b, _ in matches):
            matches.append((*generic_cash.span(), "operating_cash_flow"))
    if (re.search(r"盈利能力|盈利质量|profitability", lower) or ("盈利" in lower and re.search(r"分析|研究|评估|概览", lower))) and not any(metric in {"operating_income", "net_income", "operating_margin", "net_margin"} for _, _, metric in matches):
        profitability = re.search(r"盈利|profitability", lower)
        span = profitability.span() if profitability else (0, 0)
        matches.extend([(*span, "operating_margin"), (*span, "net_margin")])
    if re.search(r"(?<!营业)(?<!净)利润率", lower) and not any(metric in {"operating_margin", "net_margin"} for _, _, metric in matches):
        gaps.append("利润率口径未明确；请指定营业利润率或净利润率。")
    overview = not matches and bool(re.search(r"基本面|业绩|财务|研究|风险|盈利|financial|fundamental|performance|research|risk|earnings", lower))
    if overview:
        matches = [(0, 0, metric) for metric in ["revenue", "operating_margin", "operating_cash_flow"]]
    if not matches and not gaps:
        gaps.append("未识别到支持的年度财务指标，请明确收入、利润、现金流、研发费用或其计算。")

    requests: list[dict[str, Any]] = []
    dedup = set()
    for start, end, metric in sorted(matches):
        left = max([0] + [match.end() for match in re.finditer(r"[，,；;。?!！？]", lower[:start])])
        next_stop = re.search(r"[，,；;。?!！？]", lower[end:])
        right = end + next_stop.start() if next_stop else len(lower)
        clause = lower[left:right]
        local_years = years_in(clause) or years
        # Conjunction-local operations avoid propagating "growth" from revenue
        # into an unrelated margin request. A shared trailing predicate still
        # applies to a coordinated list ("revenue and operating cash flow growth").
        separators = [match for match in re.finditer(r"与|和|及|、|以及|\band\b", lower[left:right])
                      if not any(a < left + match.start() < b for a, b, _ in matches)]
        boundaries = [left] + [left + match.end() for match in separators] + [right]
        segment_start = max([left] + [boundary for boundary in boundaries if boundary <= start])
        segment_end = min([right] + [left + match.start() for match in separators if left + match.start() >= end])
        operation_clause = lower[segment_start:segment_end] if end > start else clause
        previous_in_segment = [(a, b) for a, b, _ in matches if segment_start <= a < b <= start]
        if previous_in_segment and re.search(r"后|之后|以后|after", lower[max(b for _, b in previous_in_segment):start]):
            operation_clause = lower[start:segment_end]
        operation_pattern = r"增长|增加|减少|下降|提高|提升|变化|变动|相比|比较|对比|比(?=\s*(?:FY|20\d{2}|19\d{2}|去年))|(?:少|低|多|高).{0,2}多少|较|同比|增速|增幅|百分点|growth|increase|decrease|change|compar|difference|versus|\bvs\b|\byoy\b"
        if not re.search(operation_pattern, operation_clause):
            local_matches = [(a, b, metric_id) for a, b, metric_id in matches if left <= a < right]
            first_start = min((a for a, _, _ in local_matches), default=start)
            prefix = lower[left:first_start]
            last_segment_start = boundaries[-2] if len(boundaries) > 2 else left
            tail = lower[last_segment_start:right]
            earlier = lower[left:last_segment_start]
            if re.search(r"相比|比较|对比|compar|versus|\bvs\b", prefix):
                operation_clause += " " + prefix
            elif re.search(operation_pattern, tail) and not re.search(operation_pattern, earlier):
                operation_clause += " " + tail
        # A metric-specific clause can bind its own year while other clauses use
        # another year, e.g. FY2023 revenue; FY2024 operating cash flow.
        is_ratio = metric in DERIVED and DERIVED[metric]["unit"] == "%"
        explicit_pct = bool(re.search(r"同比|增速|增长率|增长百分比|变化率|增幅|yoy|year.over.year|growth\s+rate|percent\s+(?:growth|change)|percentage\s+(?:growth|change)", operation_clause))
        explicit_amount = bool(re.search(r"增长额|增加额|增量|变化额|增长金额|增加金额|绝对|(?:少|低|多|高).{0,2}多少|多少(?:亿|万|美?元|美元)|absolute|dollar\s+(?:growth|change)|change\s+in\s+dollars", operation_clause))
        change = bool(re.search(operation_pattern, operation_clause))
        pp = bool(re.search(r"百分点|percentage\s+points?|\bpp\b", operation_clause))
        operations: list[str] = []
        if is_ratio and (pp or (change and not explicit_pct)):
            operations = ["value", "change_pp"]
        elif explicit_pct or explicit_amount or (change and not is_ratio):
            operations = ["value"]
            if explicit_amount:
                operations.append("growth_amount")
            if explicit_pct or (change and not explicit_amount):
                operations.append("growth_pct")
            if re.search(r"(?:增长|增加|减少|变化|change|difference).{0,3}(?:多少|how much)|相比|比较|对比|compar", operation_clause) and not is_ratio and not explicit_pct:
                operations.append("growth_amount")
        else:
            operations = ["value"]
        for operation in dict.fromkeys(operations):
            operation_years = list(local_years)
            reason = None
            if operation != "value" and len(operation_years) == 1:
                operation_years.insert(0, operation_years[0] - 1)
            if operation != "value" and len(operation_years) > 2:
                reason = "该变化请求涉及两个以上端点，未明确哪两个年度参与计算。"
            target_year = comparison_year = None
            if operation != "value" and len(operation_years) == 2:
                target_year, comparison_year = max(operation_years), min(operation_years)
                direction = re.search(r"(?:FY\s*)?((?:19|20)\d{2})[^，,；;。!?\d]{0,40}(?:相比|比|较|versus|\bvs\b)[^\d]{0,12}(?:FY\s*)?((?:19|20)\d{2})", clause, re.I)
                movement = re.search(r"(?:从|from)\s*(?:FY\s*)?((?:19|20)\d{2})\s*(?:年|财年)?\s*(?:到|至|to)\s*(?:FY\s*)?((?:19|20)\d{2})", clause, re.I)
                if direction:
                    target_year, comparison_year = int(direction[1]), int(direction[2])
                elif movement:
                    target_year, comparison_year = int(movement[2]), int(movement[1])
            if operation == "growth_pct" and re.search(r"同比|yoy|year.over.year", operation_clause) and len(operation_years) == 2 and operation_years[1] - operation_years[0] != 1:
                reason = "同比必须比较相邻财年；请求的两个年度不是相邻期间。"
            if operation == "growth_pct" and re.search(r"同比|yoy|year.over.year", operation_clause) and target_year is not None and target_year - comparison_year != 1:
                reason = "同比要求目标财年相对于前一财年；请求的比较方向不是这一口径。"
            key = (metric, operation, tuple(operation_years), target_year, comparison_year)
            if key in dedup:
                continue
            dedup.add(key)
            requests.append({"metric_id": metric, "operation": operation, "fiscal_years": operation_years,
                             "target_fiscal_year": target_year, "comparison_fiscal_year": comparison_year,
                             "label": metric_label(metric), "reason": reason, "clause": question[left:right]})
    requested_metrics = list(dict.fromkeys(item["metric_id"] for item in requests))
    slots = list(dict.fromkeys(slot for metric in requested_metrics for slot in DERIVED.get(metric, {}).get("inputs", [metric])))
    focuses = []
    for name, group in [("growth", {"revenue"}), ("profitability", {"operating_income", "net_income", "operating_margin", "net_margin"}),
                        ("cashflow", {"operating_cash_flow", "capital_expenditure", "free_cash_flow", "cash_conversion"}), ("rd", {"research_and_development", "rd_ratio"})]:
        if group.intersection(requested_metrics):
            focuses.append(name)
    return {"version": "2.0", "focus": focuses[0] if len(focuses) == 1 else "overview", "focuses": focuses,
            "fiscal_year": max(years) if years else None, "requested_years": years, "requested_metrics": requested_metrics,
            "operations": list(dict.fromkeys(item["operation"] for item in requests)), "requests": requests,
            "supported": not gaps and all(not item["reason"] for item in requests), "gaps": list(dict.fromkeys(gaps)),
            "scope": "single-company annual fundamentals", "metric_slots": slots,
            "definitions": {metric: DERIVED[metric] for metric in requested_metrics if metric in DERIVED}}
