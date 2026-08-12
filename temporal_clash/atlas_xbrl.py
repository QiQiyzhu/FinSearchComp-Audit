from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .live_agent import (
    TRACE_SCHEMA_VERSION,
    AnthropicMessagesWebSearch,
    RequestConfig,
    extract_anthropic_output_text,
    parse_json_object,
    prompt_sha256,
    request_config_view,
)


XBRL_STRATEGY = "atlas_xbrl"
XBRL_LABEL = "ATLAS-XBRL（LLM规划 + SEC工具）"
XBRL_METHOD_VERSION = "atlas-xbrl-1.0"

COMPANIES: dict[str, dict[str, Any]] = {
    "MSFT": {"cik": 789019, "aliases": ("微软", "microsoft", "msft")},
    "AAPL": {"cik": 320193, "aliases": ("apple", "苹果", "aapl")},
    "NVDA": {"cik": 1045810, "aliases": ("nvidia", "英伟达", "nvda")},
    "TSLA": {"cik": 1318605, "aliases": ("tesla", "特斯拉", "tsla")},
    "GOOGL": {"cik": 1652044, "aliases": ("alphabet", "google", "谷歌", "googl")},
    "META": {"cik": 1326801, "aliases": ("meta", "facebook", "脸书")},
    "INTC": {"cik": 50863, "aliases": ("intel", "英特尔", "intc")},
    "AMD": {"cik": 2488, "aliases": ("amd", "超威半导体")},
}

METRIC_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "research_and_development": ("ResearchAndDevelopmentExpense",),
    "operating_income": ("OperatingIncomeLoss",),
    "selling_general_administrative": (
        "SellingGeneralAndAdministrativeExpense",
    ),
}

OPERATIONS = (
    "relative_change_percent",
    "ratio_change_pp",
    "ratio_gap_pp",
    "ratio_change_gap_pp",
    "growth_gap_pp",
)

FACT_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string", "enum": list(COMPANIES)},
        "metric": {"type": "string", "enum": list(METRIC_TAGS)},
        "fiscal_year": {"type": "integer"},
    },
    "required": ["ticker", "metric", "fiscal_year"],
    "additionalProperties": False,
}

XBRL_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["compile", "abstain"]},
        "operation": {"type": "string", "enum": [*OPERATIONS, "none"]},
        "facts": {"type": "array", "items": FACT_REQUEST_SCHEMA},
        "return_magnitude": {"type": "boolean"},
        "explanation": {"type": "string"},
    },
    "required": [
        "action",
        "operation",
        "facts",
        "return_magnitude",
        "explanation",
    ],
    "additionalProperties": False,
}


def runtime_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case[key]
        for key in (
            "id",
            "question_zh",
            "cutoff_date",
            "target_period",
            "required_version",
            "canonical_unit",
        )
    }


def build_xbrl_payload(
    case: dict[str, Any],
    *,
    model: str,
    effort: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    task = runtime_case(case)
    serialized = json.dumps(task, ensure_ascii=False)
    if any(
        field in serialized
        for field in ("gold_answer", "gold_calculation", "reference_program")
    ):
        raise RuntimeError("Evaluation label leaked into ATLAS-XBRL prompt")
    system = (
        "You are the semantic query compiler for ATLAS-XBRL. Convert one "
        "financial question into an ordered program over official SEC Company "
        "Facts. Never answer the question and never invent values. Metrics are "
        "revenue, research_and_development, operating_income, and "
        "selling_general_administrative. Use these exact contracts:\n"
        "relative_change_percent: [current metric, prior metric], "
        "(x0/x1-1)*100.\n"
        "ratio_change_pp: [current numerator, current revenue, prior numerator, "
        "prior revenue], (x0/x1-x2/x3)*100.\n"
        "ratio_gap_pp: [left numerator, left revenue, right numerator, right "
        "revenue], (x0/x1-x2/x3)*100.\n"
        "ratio_change_gap_pp: [left current numerator, left current revenue, "
        "left prior numerator, left prior revenue, right current numerator, "
        "right current revenue, right prior numerator, right prior revenue], "
        "((x0/x1-x2/x3)-(x4/x5-x6/x7))*100.\n"
        "growth_gap_pp: [left current metric, left prior metric, right current "
        "metric, right prior metric], ((x0/x1-1)-(x2/x3-1))*100.\n"
        "For 同比, the prior fiscal year is current year minus one. Questions "
        "asking 高多少、大多少、提高多少、下降多少 or a gap require the "
        "positive magnitude, so set return_magnitude=true. Use operation=none "
        "only when the task cannot be represented."
    )
    return {
        "model": model,
        "max_tokens": max_output_tokens,
        "system": system,
        "messages": [{"role": "user", "content": serialized}],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": XBRL_PLAN_SCHEMA},
        },
    }


def _question_tickers(question: str) -> list[str]:
    lowered = question.casefold()
    matches: list[tuple[int, str]] = []
    for ticker, metadata in COMPANIES.items():
        positions = [
            lowered.find(alias.casefold())
            for alias in metadata["aliases"]
            if lowered.find(alias.casefold()) >= 0
        ]
        if positions:
            matches.append((min(positions), ticker))
    return [ticker for _, ticker in sorted(matches)]


def _question_years(question: str) -> tuple[int, int]:
    years = [int(value) for value in re.findall(r"FY\s*((?:19|20)\d{2})", question, re.I)]
    if not years:
        years = [int(value) for value in re.findall(r"(?:19|20)\d{2}", question)]
    if not years:
        raise ValueError("Question does not contain a fiscal year")
    current = max(years)
    prior = min(years) if len(set(years)) > 1 else current - 1
    return current, prior


def _question_metric(question: str) -> str:
    lowered = question.casefold()
    if "研发" in lowered or "research" in lowered:
        return "research_and_development"
    if "营业利润" in lowered or "operating margin" in lowered:
        return "operating_income"
    if "销售、一般及管理" in lowered or "sg&a" in lowered:
        return "selling_general_administrative"
    if "收入" in lowered or "revenue" in lowered or "净销售额" in lowered:
        return "revenue"
    raise ValueError("Question metric is outside the XBRL grammar")


def _operation_from_question(question: str, company_count: int) -> str:
    has_ratio = any(term in question for term in ("利润率", "强度", "占收入", "占净销售额"))
    has_change = any(term in question for term in ("从FY", "变化", "增幅", "提高", "下降"))
    has_growth = "同比" in question or "增长率" in question or "降幅" in question
    if company_count == 1:
        if has_ratio and has_change:
            return "ratio_change_pp"
        if has_growth:
            return "relative_change_percent"
    if company_count == 2:
        if has_ratio and ("从FY" in question or "增幅" in question or "变化" in question):
            return "ratio_change_gap_pp"
        if has_ratio:
            return "ratio_gap_pp"
        if has_growth:
            return "growth_gap_pp"
    raise ValueError("Question structure is outside the XBRL grammar")


def compile_question_plan(
    case: dict[str, Any], model_plan: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Canonicalize the LLM plan with a label-free financial grammar."""

    question = str(case["question_zh"])
    tickers = _question_tickers(question)
    current, prior = _question_years(question)
    metric = _question_metric(question)
    operation = _operation_from_question(question, len(tickers))
    facts: list[dict[str, Any]]
    if operation == "relative_change_percent":
        facts = [
            {"ticker": tickers[0], "metric": metric, "fiscal_year": current},
            {"ticker": tickers[0], "metric": metric, "fiscal_year": prior},
        ]
    elif operation == "ratio_change_pp":
        facts = [
            {"ticker": tickers[0], "metric": metric, "fiscal_year": current},
            {"ticker": tickers[0], "metric": "revenue", "fiscal_year": current},
            {"ticker": tickers[0], "metric": metric, "fiscal_year": prior},
            {"ticker": tickers[0], "metric": "revenue", "fiscal_year": prior},
        ]
    elif operation == "ratio_gap_pp":
        facts = [
            {"ticker": tickers[0], "metric": metric, "fiscal_year": current},
            {"ticker": tickers[0], "metric": "revenue", "fiscal_year": current},
            {"ticker": tickers[1], "metric": metric, "fiscal_year": current},
            {"ticker": tickers[1], "metric": "revenue", "fiscal_year": current},
        ]
    elif operation == "ratio_change_gap_pp":
        facts = []
        for ticker in tickers:
            facts.extend(
                [
                    {"ticker": ticker, "metric": metric, "fiscal_year": current},
                    {"ticker": ticker, "metric": "revenue", "fiscal_year": current},
                    {"ticker": ticker, "metric": metric, "fiscal_year": prior},
                    {"ticker": ticker, "metric": "revenue", "fiscal_year": prior},
                ]
            )
    elif operation == "growth_gap_pp":
        facts = []
        for ticker in tickers:
            facts.extend(
                [
                    {"ticker": ticker, "metric": metric, "fiscal_year": current},
                    {"ticker": ticker, "metric": metric, "fiscal_year": prior},
                ]
            )
    else:  # pragma: no cover - guarded above
        raise ValueError(f"Unsupported operation: {operation}")

    canonical = {
        "action": "compile",
        "operation": operation,
        "facts": facts,
        "return_magnitude": True,
        "explanation": "Canonicalized from question text after LLM classification.",
    }
    comparable_model = {
        key: model_plan.get(key)
        for key in ("action", "operation", "facts", "return_magnitude")
    }
    comparable_canonical = {
        key: canonical[key]
        for key in ("action", "operation", "facts", "return_magnitude")
    }
    return canonical, comparable_model != comparable_canonical


class SecCompanyFactsClient:
    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self.timeout_seconds = timeout_seconds
        self.cache: dict[str, dict[str, Any]] = {}
        self.response_hashes: dict[str, str] = {}

    @staticmethod
    def url_for(ticker: str) -> str:
        cik = int(COMPANIES[ticker]["cik"])
        return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

    def fetch(self, ticker: str) -> tuple[dict[str, Any], bool]:
        ticker = ticker.upper()
        if ticker in self.cache:
            return self.cache[ticker], True
        url = self.url_for(ticker)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "yzhu.research@example.com FinSearchComp-Audit/1.0",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Host": "data.sec.gov",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"SEC Company Facts HTTP {exc.code} for {ticker}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"SEC Company Facts network error for {ticker}: {exc.reason}") from exc
        data = json.loads(raw.decode("utf-8"))
        self.cache[ticker] = data
        self.response_hashes[ticker] = hashlib.sha256(raw).hexdigest()
        return data, False

    def fact(
        self,
        request: dict[str, Any],
        *,
        cutoff_date: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        ticker = str(request["ticker"]).upper()
        metric = str(request["metric"])
        fiscal_year = int(request["fiscal_year"])
        if ticker not in COMPANIES or metric not in METRIC_TAGS:
            raise ValueError("Unknown ticker or metric")
        data, cache_hit = self.fetch(ticker)
        cutoff = date.fromisoformat(cutoff_date)
        chosen: tuple[str, dict[str, Any], dict[str, Any]] | None = None
        for tag in METRIC_TAGS[metric]:
            fact = ((data.get("facts") or {}).get("us-gaap") or {}).get(tag)
            if not fact:
                continue
            candidates = []
            for row in ((fact.get("units") or {}).get("USD") or []):
                try:
                    filed = date.fromisoformat(str(row.get("filed")))
                    end = date.fromisoformat(str(row.get("end")))
                    start = date.fromisoformat(str(row.get("start")))
                except (TypeError, ValueError):
                    continue
                duration = (end - start).days
                if (
                    row.get("form") == "10-K"
                    and filed <= cutoff
                    and end.year == fiscal_year
                    and 300 <= duration <= 380
                    and row.get("val") is not None
                ):
                    candidates.append(row)
            if candidates:
                row = max(candidates, key=lambda item: str(item.get("filed")))
                chosen = (tag, fact, row)
                break
        if not chosen:
            raise ValueError(f"No filed 10-K fact for {ticker}/{metric}/FY{fiscal_year}")
        tag, fact, row = chosen
        try:
            value = Decimal(str(row["val"])) / Decimal(1_000_000)
        except (InvalidOperation, KeyError) as exc:
            raise ValueError("SEC fact is not a valid USD value") from exc
        source_url = self.url_for(ticker)
        evidence = {
            "ticker": ticker,
            "entity_name": data.get("entityName"),
            "metric": metric,
            "taxonomy_tag": tag,
            "label": fact.get("label"),
            "fiscal_year": fiscal_year,
            "value": format(value, "f"),
            "unit": "USD_million",
            "start": row.get("start"),
            "end": row.get("end"),
            "filed": row.get("filed"),
            "form": row.get("form"),
            "accession": row.get("accn"),
            "url": source_url,
            "sec_response_sha256": self.response_hashes[ticker],
        }
        action = {
            "type": "sec_companyfacts",
            "ticker": ticker,
            "metric": metric,
            "fiscal_year": fiscal_year,
            "url": source_url,
            "cache_hit": cache_hit,
            "status": "completed",
        }
        return evidence, action


def execute_program(
    operation: str,
    facts: list[dict[str, Any]],
    *,
    return_magnitude: bool,
) -> tuple[Decimal, str]:
    values = [Decimal(str(item["value"])) for item in facts]
    if operation == "relative_change_percent" and len(values) == 2:
        answer = (values[0] / values[1] - Decimal(1)) * Decimal(100)
        formula = "(x0 / x1 - 1) * 100"
    elif operation in {"ratio_change_pp", "ratio_gap_pp"} and len(values) == 4:
        answer = (values[0] / values[1] - values[2] / values[3]) * Decimal(100)
        formula = "(x0 / x1 - x2 / x3) * 100"
    elif operation == "ratio_change_gap_pp" and len(values) == 8:
        answer = (
            (values[0] / values[1] - values[2] / values[3])
            - (values[4] / values[5] - values[6] / values[7])
        ) * Decimal(100)
        formula = "((x0/x1-x2/x3) - (x4/x5-x6/x7)) * 100"
    elif operation == "growth_gap_pp" and len(values) == 4:
        answer = (
            (values[0] / values[1] - Decimal(1))
            - (values[2] / values[3] - Decimal(1))
        ) * Decimal(100)
        formula = "((x0/x1-1) - (x2/x3-1)) * 100"
    else:
        raise ValueError("Operation and SEC fact count do not match")
    return (abs(answer) if return_magnitude else answer), formula


class AtlasXbrlClient:
    def __init__(
        self,
        config: RequestConfig,
        *,
        base_url: str | None = None,
        sec_client: SecCompanyFactsClient | None = None,
    ) -> None:
        self.config = config
        self.model_client = AnthropicMessagesWebSearch(config, base_url=base_url)
        self.sec_client = sec_client or SecCompanyFactsClient(
            timeout_seconds=min(config.timeout_seconds, 120)
        )

    @property
    def base_url(self) -> str:
        return self.model_client.base_url

    def run(self, case: dict[str, Any]) -> dict[str, Any]:
        payload = build_xbrl_payload(
            case,
            model=self.config.model,
            effort=self.config.reasoning_effort,
            max_output_tokens=self.config.max_output_tokens,
        )
        started = time.perf_counter()
        response, attempts = self.model_client._request_with_retries(
            payload, self.model_client._http_transport
        )
        if response.get("stop_reason") == "max_tokens":
            raise RuntimeError("ATLAS-XBRL compiler exhausted max_tokens")
        output_text = extract_anthropic_output_text(response)
        model_plan = parse_json_object(output_text)
        plan, compiler_repaired = compile_question_plan(case, model_plan)
        facts: list[dict[str, Any]] = []
        tool_actions: list[dict[str, Any]] = []
        for request in plan["facts"]:
            fact, action = self.sec_client.fact(
                request, cutoff_date=str(case["cutoff_date"])
            )
            facts.append(fact)
            tool_actions.append(action)
        answer, formula = execute_program(
            plan["operation"],
            facts,
            return_magnitude=bool(plan["return_magnitude"]),
        )
        rounded = answer.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        answer_value = format(rounded, "f")
        evidence = [
            {
                "url": fact["url"],
                "title": f"SEC Company Facts: {fact['entity_name']} / {fact['label']}",
                "published_at": fact["filed"],
                "target_period": f"FY{fact['fiscal_year']}",
                "revision": "filed",
                "unit": fact["unit"],
                "evidence_text": (
                    f"{fact['ticker']} {fact['taxonomy_tag']} FY{fact['fiscal_year']} "
                    f"= {fact['value']} USD_million; accession {fact['accession']}."
                ),
            }
            for fact in facts
        ]
        citations = [
            {
                "url": fact["url"],
                "title": f"SEC Company Facts: {fact['ticker']}",
                "cited_text": evidence[index]["evidence_text"],
                "type": "sec_companyfacts",
            }
            for index, fact in enumerate(facts)
        ]
        sources = list(
            {
                fact["url"]: {
                    "url": fact["url"],
                    "title": f"SEC Company Facts: {fact['entity_name']}",
                    "page_age": fact["filed"],
                    "type": "official_structured_data",
                }
                for fact in facts
            }.values()
        )
        result = {
            "action": "answer",
            "model_action": model_plan.get("action"),
            "answer_value": answer_value,
            "unit": case["canonical_unit"],
            "explanation": (
                "Claude compiled the question; a label-free grammar canonicalized "
                "the program; official SEC XBRL facts were executed with Decimal."
            ),
            "evidence": evidence,
            "accepted_evidence": evidence,
            "evidence_audits": [
                {"url": item["url"], "accepted": True, "violations": []}
                for item in evidence
            ],
            "filter_triggered": False,
            "compiler_repaired": compiler_repaired,
            "model_plan": model_plan,
            "compiled_plan": plan,
            "calculation_trace": {
                "method_version": XBRL_METHOD_VERSION,
                "operation": plan["operation"],
                "formula": formula,
                "return_magnitude": plan["return_magnitude"],
                "operand_values": [fact["value"] for fact in facts],
                "raw_answer": str(answer),
                "rounded_answer": answer_value,
                "rounding": "Decimal ROUND_HALF_UP, 2 decimals",
            },
            "atlas_method_version": XBRL_METHOD_VERSION,
        }
        latency = time.perf_counter() - started
        return {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "provider": "anthropic+sec",
            "response_id": response.get("id"),
            "response_ids": [response.get("id")],
            "requested_model": self.config.model,
            "model": response.get("model") or self.config.model,
            "strategy": XBRL_STRATEGY,
            "strategy_label": XBRL_LABEL,
            "prompt_sha256": prompt_sha256(json.dumps(payload, ensure_ascii=False)),
            "prompt": payload["messages"][0]["content"],
            "request_config": request_config_view(self.config),
            "structured_output_mode": "anthropic_xbrl_program_json_schema",
            "latency_seconds": round(latency, 3),
            "http_attempts": attempts,
            "usage": response.get("usage") or {},
            "search_actions": tool_actions,
            "search_sources": sources,
            "api_citations": citations,
            "output_text": output_text,
            "result": result,
            "request_payload": payload,
            "sec_facts": facts,
            "sec_api_calls": sum(not action["cache_hit"] for action in tool_actions),
            "sec_cache_hits": sum(action["cache_hit"] for action in tool_actions),
            "raw_response": response,
        }
