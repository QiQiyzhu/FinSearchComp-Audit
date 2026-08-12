from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .live_agent import (
    ANTHROPIC_WEB_SEARCH_TOOL,
    TRACE_SCHEMA_VERSION,
    AnthropicMessagesWebSearch,
    RequestConfig,
    extract_anthropic_citations,
    extract_anthropic_output_text,
    extract_anthropic_search_actions,
    extract_anthropic_search_sources,
    parse_json_object,
    prompt_sha256,
    request_config_view,
)


COMPUTE_STRATEGY = "atlas_compute"
COMPUTE_LABEL = "ATLAS-Compute（证据程序化）"
COMPUTE_METHOD_VERSION = "atlas-compute-1.0"
OPERATIONS = ("relative_change_percent", "difference_of_ratios_pp")

OPERAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "value": {"type": "string"},
        "unit": {"type": "string"},
        "target_period": {"type": "string"},
        "published_at": {"type": ["string", "null"]},
        "url": {"type": "string"},
        "evidence_text": {"type": "string"},
    },
    "required": [
        "label",
        "value",
        "unit",
        "target_period",
        "published_at",
        "url",
        "evidence_text",
    ],
    "additionalProperties": False,
}

COMPUTE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["compute", "abstain"]},
        "operation": {
            "type": ["string", "null"],
            "enum": [*OPERATIONS, None],
        },
        "operands": {"type": "array", "items": OPERAND_SCHEMA},
        "explanation": {"type": "string"},
    },
    "required": ["action", "operation", "operands", "explanation"],
    "additionalProperties": False,
}


def runtime_case(case: dict[str, Any]) -> dict[str, Any]:
    """Return the only benchmark fields allowed to enter an API prompt."""

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


def build_compute_research_prompt(case: dict[str, Any]) -> str:
    task = runtime_case(case)
    return (
        "You are the evidence acquisition stage of ATLAS-Compute for financial "
        "numerical questions. Search the live web and prefer the exact SEC 10-K "
        "filed by the cutoff. Decompose the question before searching. Retrieve "
        "every raw operand at full reported precision, its unit, fiscal period, "
        "filing date and URL. Do not substitute rounded narrative percentages "
        "when the question asks for calculation from unrounded million-dollar "
        "figures. For a relative change use (current/prior - 1)*100. For a "
        "difference in percentage-point ratios use "
        "(left_numerator/left_denominator - "
        "right_numerator/right_denominator)*100, where left minus right follows "
        "the wording of the question. Name the operation and list operands in "
        "that exact order. Attach a native citation to every operand. Do not use "
        "information first published after the cutoff and do not invent missing "
        "values. If any operand is unavailable, say that the computation must "
        "abstain. Write a concise research memo, not JSON.\n\n"
        "Task metadata:\n"
        + json.dumps(task, ensure_ascii=False, indent=2)
    )


def build_compute_normalization_payload(
    case: dict[str, Any],
    research_text: str,
    citations: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    model: str,
    effort: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    packet = {
        "task": runtime_case(case),
        "research_memo": research_text,
        "retrieved_sources": sources,
        "native_citations": citations,
    }
    serialized = json.dumps(packet, ensure_ascii=False)
    forbidden = ("gold_answer", "gold_calculation", "reference_sources")
    if any(field in serialized for field in forbidden):
        raise RuntimeError("Evaluation label leaked into ATLAS-Compute packet")
    return {
        "model": model,
        "max_tokens": max_output_tokens,
        "system": (
            "Convert the cited research memo into an executable calculation "
            "plan. Copy only exact raw operands, URLs, dates and units present in "
            "the supplied material; never add facts from memory. Select "
            "relative_change_percent with exactly [current, prior], or "
            "difference_of_ratios_pp with exactly "
            "[left_numerator, left_denominator, right_numerator, "
            "right_denominator]. The order must preserve the direction asked in "
            "the question. Do not calculate or emit the final answer. Abstain if "
            "the cited material lacks any required operand."
        ),
        "messages": [{"role": "user", "content": serialized}],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": COMPUTE_PLAN_SCHEMA},
        },
    }


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
    return urlunsplit(
        (parsed.scheme.lower(), host, parsed.path.rstrip("/") or "/", parsed.query, "")
    )


def _number(value: Any) -> Decimal:
    text = str(value).strip().replace(",", "").replace("$", "")
    if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise ValueError(f"Invalid numeric operand: {value!r}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid numeric operand: {value!r}") from exc


def execute_compute_plan(
    plan: dict[str, Any],
    case: dict[str, Any],
    sources: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    operation = plan.get("operation")
    operands = plan.get("operands") or []
    expected_count = {
        "relative_change_percent": 2,
        "difference_of_ratios_pp": 4,
    }.get(operation)
    violations: list[str] = []
    if plan.get("action") != "compute":
        violations.append("model_abstained")
    if expected_count is None:
        violations.append("operation_invalid")
    elif len(operands) != expected_count:
        violations.append("operand_count_mismatch")

    allowed_urls = {
        canonical
        for item in [*sources, *citations]
        if (canonical := _canonical_url(item.get("url")))
    }
    cutoff = date.fromisoformat(str(case["cutoff_date"]))
    operand_audits: list[dict[str, Any]] = []
    values: list[Decimal] = []
    for operand in operands:
        issues: list[str] = []
        canonical = _canonical_url(operand.get("url"))
        if canonical not in allowed_urls:
            issues.append("url_not_retrieved_or_cited")
        published_at = operand.get("published_at")
        if published_at:
            try:
                if date.fromisoformat(str(published_at)) > cutoff:
                    issues.append("published_after_cutoff")
            except ValueError:
                issues.append("published_at_invalid")
        if not operand.get("evidence_text"):
            issues.append("evidence_text_missing")
        try:
            values.append(_number(operand.get("value")))
        except ValueError:
            issues.append("numeric_value_invalid")
        operand_audits.append(
            {
                "label": operand.get("label"),
                "url": operand.get("url"),
                "accepted": not issues,
                "violations": issues,
            }
        )
        violations.extend(issues)

    raw_answer: Decimal | None = None
    if not violations:
        try:
            if operation == "relative_change_percent":
                raw_answer = (values[0] / values[1] - Decimal(1)) * Decimal(100)
            elif operation == "difference_of_ratios_pp":
                raw_answer = (
                    values[0] / values[1] - values[2] / values[3]
                ) * Decimal(100)
        except (InvalidOperation, ZeroDivisionError):
            violations.append("calculation_failed")

    action = "answer" if raw_answer is not None and not violations else "abstain"
    rounded = (
        raw_answer.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if raw_answer is not None and not violations
        else None
    )
    answer_value = format(rounded, "f") if rounded is not None else None
    formula = {
        "relative_change_percent": "(x0 / x1 - 1) * 100",
        "difference_of_ratios_pp": "(x0 / x1 - x2 / x3) * 100",
    }.get(operation)
    evidence = [
        {
            "url": operand.get("url"),
            "title": operand.get("label"),
            "published_at": operand.get("published_at"),
            "target_period": operand.get("target_period"),
            "revision": case["required_version"],
            "unit": operand.get("unit"),
            "evidence_text": operand.get("evidence_text"),
        }
        for operand in operands
    ]
    return {
        "action": action,
        "model_action": plan.get("action"),
        "answer_value": answer_value,
        "unit": case["canonical_unit"] if action == "answer" else None,
        "explanation": (
            f"Deterministic Decimal execution: {formula}; rounded half-up to 2 decimals."
            if action == "answer"
            else "ATLAS-Compute abstained because the executable evidence plan failed validation."
        ),
        "evidence": evidence,
        "accepted_evidence": [
            evidence[index]
            for index, audit in enumerate(operand_audits)
            if audit["accepted"]
        ],
        "evidence_audits": operand_audits,
        "filter_triggered": plan.get("action") == "compute" and action == "abstain",
        "calculation_trace": {
            "method_version": COMPUTE_METHOD_VERSION,
            "operation": operation,
            "formula": formula,
            "operand_values": [str(value) for value in values],
            "raw_answer": str(raw_answer) if raw_answer is not None else None,
            "rounded_answer": answer_value,
            "rounding": "Decimal ROUND_HALF_UP, 2 decimals",
            "violations": sorted(set(violations)),
        },
        "compute_plan": plan,
        "atlas_method_version": COMPUTE_METHOD_VERSION,
    }


class AtlasComputeClient:
    def __init__(
        self,
        config: RequestConfig,
        *,
        base_url: str | None = None,
    ) -> None:
        self.config = config
        self.client = AnthropicMessagesWebSearch(config, base_url=base_url)

    @property
    def base_url(self) -> str:
        return self.client.base_url

    def run(self, case: dict[str, Any]) -> dict[str, Any]:
        research_prompt = build_compute_research_prompt(case)
        research_payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": research_prompt,
            "messages": [{"role": "user", "content": case["question_zh"]}],
            "tools": [
                {
                    "type": ANTHROPIC_WEB_SEARCH_TOOL,
                    "name": "web_search",
                    "max_uses": self.config.max_tool_calls,
                    "allowed_callers": ["direct"],
                    "response_inclusion": "full",
                }
            ],
            "tool_choice": {"type": "tool", "name": "web_search"},
            "output_config": {"effort": self.config.reasoning_effort},
        }
        serialized = json.dumps(research_payload, ensure_ascii=False)
        if any(
            field in serialized
            for field in ("gold_answer", "gold_calculation", "reference_sources")
        ):
            raise RuntimeError("Evaluation label leaked into ATLAS-Compute research")

        started = time.perf_counter()
        research, research_attempts = self.client._request_with_retries(
            research_payload, self.client._http_transport
        )
        if research.get("stop_reason") in {"pause_turn", "max_tokens"}:
            raise RuntimeError(
                f"ATLAS-Compute research ended with {research.get('stop_reason')}"
            )
        research_text = extract_anthropic_output_text(research)
        citations = extract_anthropic_citations(research)
        sources = extract_anthropic_search_sources(research)
        normalize_payload = build_compute_normalization_payload(
            case,
            research_text,
            citations,
            sources,
            model=self.config.model,
            effort=self.config.reasoning_effort,
            max_output_tokens=self.config.max_output_tokens,
        )
        normalize, normalize_attempts = self.client._request_with_retries(
            normalize_payload, self.client._http_transport
        )
        if normalize.get("stop_reason") == "max_tokens":
            raise RuntimeError("ATLAS-Compute normalization exhausted max_tokens")
        output_text = extract_anthropic_output_text(normalize)
        plan = parse_json_object(output_text)
        result = execute_compute_plan(plan, case, sources, citations)
        latency = time.perf_counter() - started

        research_usage = research.get("usage") or {}
        normalize_usage = normalize.get("usage") or {}
        usage = {
            key: sum(int(item.get(key) or 0) for item in (research_usage, normalize_usage))
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        }
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        usage["server_tool_use"] = {
            "web_search_requests": int(
                ((research_usage.get("server_tool_use") or {}).get("web_search_requests"))
                or 0
            )
        }
        usage["stages"] = [research_usage, normalize_usage]
        raw = {"research": research, "normalization": normalize}
        return {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "provider": "anthropic",
            "response_id": research.get("id"),
            "normalization_response_id": normalize.get("id"),
            "response_ids": [
                value for value in (research.get("id"), normalize.get("id")) if value
            ],
            "requested_model": self.config.model,
            "model": research.get("model") or self.config.model,
            "normalization_model": normalize.get("model") or self.config.model,
            "strategy": COMPUTE_STRATEGY,
            "strategy_label": COMPUTE_LABEL,
            "prompt_sha256": prompt_sha256(research_prompt),
            "prompt": research_prompt,
            "request_config": request_config_view(self.config),
            "structured_output_mode": "anthropic_compute_plan_then_decimal",
            "latency_seconds": round(latency, 3),
            "http_attempts": research_attempts + normalize_attempts,
            "usage": usage,
            "search_actions": extract_anthropic_search_actions(research),
            "search_sources": sources,
            "api_citations": citations,
            "research_output_text": research_text,
            "output_text": output_text,
            "result": result,
            "request_payloads": {
                "research": research_payload,
                "normalization": normalize_payload,
            },
            "raw_response": raw,
            "raw_response_sha256": hashlib.sha256(
                json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }
