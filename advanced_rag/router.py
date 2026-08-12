from __future__ import annotations

import re

from .models import QuerySpec, RoutePlan


MARKET_AUTHORITIES = ("market_data_api",)
MACRO_AUTHORITIES = ("official_statistics", "central_bank")
FILING_AUTHORITIES = (
    "regulator_filing",
    "company_filing_archive",
    "company_release",
)
WEB_AUTHORITIES = ("official_web", "web_search")


class AdaptiveRouter:
    """Route each question to source families and retrieval depth.

    The prototype uses transparent rules so that every route can be audited.
    A learned router can later implement the same interface.
    """

    _market_terms = (
        "标普",
        "纳斯达克",
        "指数",
        "回撤",
        "涨幅",
        "回报率",
        "收盘价",
        "股价",
    )
    _macro_terms = (
        "cpi",
        "fomc",
        "联邦基金",
        "经常账户",
        "国际收支",
        "通胀",
        "利率",
    )
    _filing_terms = (
        "财年",
        "财报",
        "销售额",
        "研发费用",
        "现金流",
        "利润",
        "资产",
        "负债",
        "收入",
        "周转率",
        "资本性支出",
        "拆分",
    )
    _multi_hop_terms = (
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

    def plan(self, query: QuerySpec) -> RoutePlan:
        normalized = query.question.lower()
        preferred: list[str] = []
        reasons: list[str] = []

        if any(term in normalized for term in self._market_terms):
            preferred.extend(MARKET_AUTHORITIES)
            reasons.append("问题包含行情/收益计算信号，优先 point-in-time 行情接口")
        if any(term in normalized for term in self._macro_terms):
            preferred.extend(MACRO_AUTHORITIES)
            reasons.append("问题包含宏观或央行信号，优先官方统计与央行来源")
        if any(term in normalized for term in self._filing_terms):
            preferred.extend(FILING_AUTHORITIES)
            reasons.append("问题包含公司财务字段，优先监管申报和公司原始文件")
        if not preferred:
            preferred.extend(WEB_AUTHORITIES)
            reasons.append("未命中结构化来源规则，使用官方网页与通用搜索回退")

        preferred = list(dict.fromkeys(preferred + list(WEB_AUTHORITIES)))
        complexity = self._complexity(normalized)
        if complexity == "multi_step":
            reasons.append("问题需要比较、聚合或跨期计算，启用多步检索预算")
            initial_k, correction_k = 8, 24
        else:
            reasons.append("问题是单事实查询，先使用较小检索预算")
            initial_k, correction_k = 5, 16

        authority_hint = " ".join(self._authority_hints(preferred))
        variants = (
            query.question,
            (
                f"{query.question} 截止 {query.cutoff_date} "
                f"期间 {query.target_period} 版本 {query.required_version} "
                f"单位 {query.canonical_unit}"
            ),
            f"{query.question} {authority_hint}",
        )
        return RoutePlan(
            complexity=complexity,
            preferred_authorities=tuple(preferred),
            query_variants=variants,
            initial_k=initial_k,
            correction_k=correction_k,
            reasons=tuple(reasons),
        )

    def authority_utility(self, authority: str, plan: RoutePlan) -> float:
        if authority not in plan.preferred_authorities:
            return 0.35
        index = plan.preferred_authorities.index(authority)
        return max(0.55, 1.0 - 0.08 * index)

    def _complexity(self, normalized_question: str) -> str:
        keyword_hits = sum(
            term in normalized_question for term in self._multi_hop_terms
        )
        year_hits = len(re.findall(r"(?:19|20)\d{2}", normalized_question))
        return "multi_step" if keyword_hits or year_hits >= 2 else "single_step"

    @staticmethod
    def _authority_hints(authorities: list[str]) -> list[str]:
        labels = {
            "market_data_api": "历史行情 API",
            "official_statistics": "官方统计",
            "central_bank": "央行公告",
            "regulator_filing": "监管申报",
            "company_filing_archive": "公司财报归档",
            "company_release": "公司公告",
            "official_web": "官方网站",
            "web_search": "网页搜索",
        }
        return [labels[item] for item in authorities if item in labels]
