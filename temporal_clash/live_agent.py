from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .live_atlas import (
    ATLAS_STRATEGY,
    apply_atlas_validation,
    atlas_initial_query,
)


OPENAI_PROVIDER = "openai"
ANTHROPIC_PROVIDER = "anthropic"
PROVIDERS = (OPENAI_PROVIDER, ANTHROPIC_PROVIDER)

DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_WEB_SEARCH_TOOL = "web_search_20260318"
TRACE_SCHEMA_VERSION = "2.0"
HTTP_USER_AGENT = (
    "FinSearchComp-Audit/1.0 "
    "(https://github.com/QiQiyzhu/FinSearchComp-Audit)"
)

STRATEGIES = (
    "plain_agent",
    "temporal_prompt",
    "metadata_filter",
    "teg_validator",
    ATLAS_STRATEGY,
)

STRATEGY_LABELS = {
    "plain_agent": "普通搜索 Agent",
    "temporal_prompt": "时间约束 Prompt",
    "metadata_filter": "元数据过滤器",
    "teg_validator": "完整证据验证器",
    ATLAS_STRATEGY: "ATLAS-RAG（校准版）",
}

LOCAL_VALIDATION_PROFILES = {
    "plain_agent": (),
    "temporal_prompt": (),
    "metadata_filter": ("published_at", "target_period", "revision"),
    "teg_validator": ("published_at", "target_period", "revision", "unit"),
    ATLAS_STRATEGY: (),
}

EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "published_at": {
            "type": ["string", "null"],
            "description": "YYYY-MM-DD, or null when the source does not establish it.",
        },
        "target_period": {"type": ["string", "null"]},
        "revision": {"type": ["string", "null"]},
        "unit": {"type": ["string", "null"]},
        "evidence_text": {"type": ["string", "null"]},
    },
    "required": [
        "url",
        "title",
        "published_at",
        "target_period",
        "revision",
        "unit",
        "evidence_text",
    ],
    "additionalProperties": False,
}

AGENT_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["answer", "abstain"]},
        "answer_value": {"type": ["string", "null"]},
        "unit": {"type": ["string", "null"]},
        "explanation": {"type": "string"},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    },
    "required": ["action", "answer_value", "unit", "explanation", "evidence"],
    "additionalProperties": False,
}

OUTPUT_CONTRACT = """
Return only one JSON object, without Markdown fences, matching this contract:
{
  "action": "answer" or "abstain",
  "answer_value": "numeric string, or null",
  "unit": "the requested canonical unit, or null",
  "explanation": "one short sentence",
  "evidence": [
    {
      "url": "https://... or null",
      "title": "source title or null",
      "published_at": "YYYY-MM-DD or null",
      "target_period": "the exact requested target period or null",
      "revision": "the exact requested version or null",
      "unit": "the unit supported by this evidence or null",
      "evidence_text": "short supporting passage or calculation description"
    }
  ]
}
If reliable evidence is insufficient, use action="abstain". Never invent a
publication date or metadata value: use null when the page does not establish it.
Every evidence URL must come from a source actually retrieved in this run.
""".strip()

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)[^\s\"']+"),
)


def default_model_for_provider(provider: str) -> str:
    if provider == OPENAI_PROVIDER:
        return DEFAULT_MODEL
    if provider == ANTHROPIC_PROVIDER:
        return DEFAULT_ANTHROPIC_MODEL
    raise ValueError(f"Unknown provider: {provider}")


def credential_is_available(provider: str) -> bool:
    if provider == OPENAI_PROVIDER:
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == ANTHROPIC_PROVIDER:
        return bool(
            os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
        )
    raise ValueError(f"Unknown provider: {provider}")


def credential_help(provider: str) -> str:
    if provider == OPENAI_PROVIDER:
        return "Set OPENAI_API_KEY in the current shell."
    if provider == ANTHROPIC_PROVIDER:
        return (
            "Set ANTHROPIC_AUTH_TOKEN (relay/Bearer auth) or ANTHROPIC_API_KEY "
            "(official x-api-key auth) in the current shell."
        )
    raise ValueError(f"Unknown provider: {provider}")


def redact_sensitive(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


def safe_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Base URL must not contain credentials")
    port = f":{parsed.port}" if parsed.port else ""
    hostname = parsed.hostname or ""
    netloc = f"{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _endpoint(base_url: str, resource: str) -> str:
    base = safe_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/{resource.lstrip('/')}"
    return f"{base}/v1/{resource.lstrip('/')}"


def _strategy_rules(strategy: str, case: dict[str, Any]) -> str:
    if strategy == "plain_agent":
        return (
            "Answer the financial question using web search. Prefer authoritative "
            "primary sources, but do not apply any special point-in-time rule."
        )
    temporal_rule = (
        f"Answer strictly as of {case['cutoff_date']}. Do not use evidence first "
        "published after that date, even if it gives the correct historical value."
    )
    if strategy == "temporal_prompt":
        return temporal_rule
    metadata_rule = (
        f"{temporal_rule} Only rely on evidence whose target period is exactly "
        f"{case['target_period']} and whose revision/version is "
        f"{case['required_version']}. Report missing metadata as null."
    )
    if strategy == "metadata_filter":
        return metadata_rule
    if strategy == "teg_validator":
        return (
            f"{metadata_rule} Also verify that the supported unit is exactly "
            f"{case['canonical_unit']}. Prefer regulator filings, official statistics, "
            "issuer reports, and market-data APIs. Abstain if no evidence passes every "
            "date, period, version, and unit check."
        )
    if strategy == ATLAS_STRATEGY:
        return (
            f"{temporal_rule} Use this auditable ATLAS workflow: "
            "(1) PLAN a source route: historical market series for price/return "
            "questions, regulator filings or annual reports for company accounts, "
            "and official statistics/central-bank releases for macro questions; "
            "(2) RETRIEVE with a focused query, assess whether the answer operands, "
            "target period, version, unit and source authority are sufficient, and "
            "use another focused search when something is missing; (3) RESOLVE "
            "conflicting candidate values by preferring the source matching the "
            "requested period/version and the strongest primary authority; "
            "(4) ANSWER only from cited evidence. Treat an explicitly post-cutoff "
            "date or a period/version/unit mismatch as a hard conflict. Treat "
            "unavailable publication metadata as uncertainty, not automatically as "
            "a violation; never invent it. For derived questions, retrieve every "
            "operand and show the compact calculation in evidence_text."
        )
    raise ValueError(f"Unknown strategy: {strategy}")


def build_prompt(case: dict[str, Any], strategy: str) -> str:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    return "\n\n".join(
        [
            _strategy_rules(strategy, case),
            "You must perform web search before answering.",
            "Question and audit metadata:",
            json.dumps(
                {
                    "question": case["question_zh"],
                    "cutoff_date": case["cutoff_date"],
                    "target_period": case["target_period"],
                    "required_version": case["required_version"],
                    "canonical_unit": case["canonical_unit"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            OUTPUT_CONTRACT,
        ]
    )


def build_research_prompt(case: dict[str, Any], strategy: str) -> str:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    return "\n\n".join(
        [
            _strategy_rules(strategy, case),
            "You must perform web search before answering.",
            "Question and audit metadata:",
            json.dumps(
                {
                    "question": case["question_zh"],
                    "cutoff_date": case["cutoff_date"],
                    "target_period": case["target_period"],
                    "required_version": case["required_version"],
                    "canonical_unit": case["canonical_unit"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            (
                "Write a concise research memo, not JSON. State the candidate "
                "answer and unit, identify publication date, target period and "
                "revision when available, and use the API's native citations for "
                "every factual claim. Never invent missing metadata."
            ),
        ]
    )


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def extract_output_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(str(content["text"]))
    return "\n".join(chunks).strip()


def extract_api_citations(response: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            for annotation in content.get("annotations", []):
                citation = annotation
                if annotation.get("type") == "url_citation" and isinstance(
                    annotation.get("url_citation"), dict
                ):
                    citation = annotation["url_citation"]
                url = citation.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                citations.append(
                    {
                        "url": url,
                        "title": citation.get("title"),
                        "start_index": citation.get("start_index"),
                        "end_index": citation.get("end_index"),
                    }
                )
    return citations


def extract_search_actions(response: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in response.get("output", []):
        if item.get("type") != "web_search_call":
            continue
        action = dict(item.get("action") or {})
        action["call_id"] = item.get("id")
        action["status"] = item.get("status")
        actions.append(action)
    return actions


def _normalize_source(source: Any) -> dict[str, Any] | None:
    if isinstance(source, str):
        return {"url": source, "title": None, "type": None}
    if not isinstance(source, dict):
        return None
    url = source.get("url") or source.get("source_url")
    if not url:
        return None
    return {
        "url": url,
        "title": source.get("title"),
        "type": source.get("type"),
    }


def extract_search_sources(response: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in response.get("output", []):
        if item.get("type") != "web_search_call":
            continue
        action = item.get("action") or {}
        for source in action.get("sources") or []:
            normalized = _normalize_source(source)
            if not normalized or normalized["url"] in seen:
                continue
            seen.add(normalized["url"])
            sources.append(normalized)
    return sources


def extract_anthropic_output_text(response: dict[str, Any]) -> str:
    return "\n".join(
        str(block.get("text"))
        for block in response.get("content", [])
        if block.get("type") == "text" and block.get("text")
    ).strip()


def extract_anthropic_search_actions(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for block in response.get("content", []):
        if block.get("type") != "server_tool_use" or block.get("name") != "web_search":
            continue
        actions.append(
            {
                **dict(block.get("input") or {}),
                "call_id": block.get("id"),
                "caller": block.get("caller"),
                "type": "search",
            }
        )
    return actions


def extract_anthropic_search_sources(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in response.get("content", []):
        if block.get("type") != "web_search_tool_result":
            continue
        content = block.get("content")
        if not isinstance(content, list):
            continue
        for result in content:
            if not isinstance(result, dict) or result.get("type") != "web_search_result":
                continue
            url = result.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(
                {
                    "url": url,
                    "title": result.get("title"),
                    "page_age": result.get("page_age"),
                    "type": result.get("type"),
                }
            )
    return sources


def extract_anthropic_citations(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for block in response.get("content", []):
        if block.get("type") != "text":
            continue
        for citation in block.get("citations") or []:
            url = citation.get("url")
            cited_text = str(citation.get("cited_text") or "")
            key = (str(url or ""), cited_text)
            if not url or key in seen:
                continue
            seen.add(key)
            citations.append(
                {
                    "url": url,
                    "title": citation.get("title"),
                    "cited_text": citation.get("cited_text"),
                    "type": citation.get("type"),
                }
            )
    return citations


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Model output does not contain a JSON object")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Model output JSON must be an object")
    return value


def normalize_agent_result(value: dict[str, Any]) -> dict[str, Any]:
    action = str(value.get("action", "")).strip().lower()
    if action not in {"answer", "abstain"}:
        raise ValueError("action must be 'answer' or 'abstain'")
    evidence = value.get("evidence") or []
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    normalized_evidence: list[dict[str, Any]] = []
    fields = (
        "url",
        "title",
        "published_at",
        "target_period",
        "revision",
        "unit",
        "evidence_text",
    )
    for item in evidence:
        if not isinstance(item, dict):
            continue
        normalized_evidence.append({field: item.get(field) for field in fields})
    return {
        "action": action,
        "answer_value": (
            None if value.get("answer_value") is None else str(value["answer_value"])
        ),
        "unit": None if value.get("unit") is None else str(value["unit"]),
        "explanation": str(value.get("explanation") or ""),
        "evidence": normalized_evidence,
    }


def _date_violation(observed: Any, cutoff: str) -> str | None:
    if not observed:
        return "published_at_missing"
    try:
        if date.fromisoformat(str(observed)) > date.fromisoformat(cutoff):
            return "published_after_cutoff"
    except ValueError:
        return "published_at_invalid"
    return None


def validate_evidence(
    evidence: dict[str, Any],
    case: dict[str, Any],
    checks: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    if "published_at" in checks:
        violation = _date_violation(evidence.get("published_at"), case["cutoff_date"])
        if violation:
            violations.append(violation)
    for field, expected in (
        ("target_period", case["target_period"]),
        ("revision", case["required_version"]),
        ("unit", case["canonical_unit"]),
    ):
        if field not in checks:
            continue
        observed = evidence.get(field)
        if observed is None or str(observed).strip() == "":
            violations.append(f"{field}_missing")
        elif str(observed).strip() != str(expected):
            violations.append(f"{field}_mismatch")
    return violations


def apply_local_validation(
    strategy: str,
    result: dict[str, Any],
    case: dict[str, Any],
    search_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if strategy == ATLAS_STRATEGY:
        return apply_atlas_validation(result, case, search_sources)
    checks = LOCAL_VALIDATION_PROFILES[strategy]
    audits = []
    accepted = []
    for evidence in result["evidence"]:
        violations = validate_evidence(evidence, case, checks)
        audits.append(
            {
                "url": evidence.get("url"),
                "accepted": not violations,
                "violations": violations,
            }
        )
        if not violations:
            accepted.append(evidence)

    final_action = result["action"]
    filter_triggered = False
    if checks and result["action"] == "answer" and not accepted:
        final_action = "abstain"
        filter_triggered = True

    return {
        **result,
        "model_action": result["action"],
        "action": final_action,
        "accepted_evidence": accepted if checks else list(result["evidence"]),
        "evidence_audits": audits,
        "local_checks": list(checks),
        "filter_triggered": filter_triggered,
    }


@dataclass(frozen=True)
class RequestConfig:
    model: str = DEFAULT_MODEL
    reasoning_effort: str = "medium"
    search_context_size: str = "medium"
    max_tool_calls: int = 3
    max_output_tokens: int = 1200
    timeout_seconds: int = 120
    max_retries: int = 0
    force_search: bool = True
    structured_output: bool = True
    save_raw_response: bool = True


Transport = Callable[[dict[str, Any]], dict[str, Any]]


def request_config_view(config: RequestConfig) -> dict[str, Any]:
    return asdict(config)


def http_calls_per_run(provider: str, structured_output: bool) -> int:
    if provider == OPENAI_PROVIDER:
        return 1
    if provider == ANTHROPIC_PROVIDER:
        return 2 if structured_output else 1
    raise ValueError(f"Unknown provider: {provider}")


def _combined_usage(*usage_objects: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        result[field] = sum(int(usage.get(field) or 0) for usage in usage_objects)
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    result["server_tool_use"] = {
        "web_search_requests": sum(
            int(
                ((usage.get("server_tool_use") or {}).get("web_search_requests"))
                or 0
            )
            for usage in usage_objects
        )
    }
    result["stages"] = list(usage_objects)
    return result


class _RetryingClient:
    def __init__(self, config: RequestConfig) -> None:
        self.config = config

    def _request_with_retries(
        self,
        payload: dict[str, Any],
        transport: Transport,
    ) -> tuple[dict[str, Any], int]:
        error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 2):
            try:
                return transport(payload), attempt
            except Exception as exc:
                error = exc
                if attempt > self.config.max_retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))
        raise RuntimeError(redact_sensitive(str(error) if error else "Unknown API failure"))


class OpenAIResponsesWebSearch(_RetryingClient):
    """Responses API client with forced search, full sources and JSON Schema."""

    provider = OPENAI_PROVIDER
    structured_output_mode = "openai_text_json_schema"

    def __init__(
        self,
        config: RequestConfig,
        *,
        transport: Transport | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(config)
        self._transport = transport
        self._api_key = api_key
        self._base_url = safe_base_url(
            base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def _http_transport(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(credential_help(self.provider))
        request = urllib.request.Request(
            _endpoint(self._base_url, "responses"),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": HTTP_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = redact_sensitive(
                exc.read().decode("utf-8", errors="replace")
            )
            raise RuntimeError(f"OpenAI-compatible API HTTP {exc.code}: {detail[:800]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI-compatible API network error: {exc.reason}") from exc

    def build_payload(self, case: dict[str, Any], strategy: str) -> dict[str, Any]:
        prompt = build_prompt(case, strategy)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "instructions": (
                "You are a financial research agent. You must use web search before "
                "answering. Use only URLs retrieved in this response and follow the "
                "requested JSON contract exactly."
            ),
            "input": prompt,
            "tools": [
                {
                    "type": "web_search",
                    "search_context_size": self.config.search_context_size,
                    "external_web_access": True,
                }
            ],
            "tool_choice": "required" if self.config.force_search else "auto",
            "include": ["web_search_call.action.sources"],
            "max_tool_calls": self.config.max_tool_calls,
            "reasoning": {"effort": self.config.reasoning_effort},
            "max_output_tokens": self.config.max_output_tokens,
            "store": False,
        }
        if self.config.structured_output:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "financial_research_result",
                    "schema": AGENT_RESULT_SCHEMA,
                    "strict": True,
                }
            }
        return payload

    def run(self, case: dict[str, Any], strategy: str) -> dict[str, Any]:
        prompt = build_prompt(case, strategy)
        payload = self.build_payload(case, strategy)
        started = time.perf_counter()
        response, attempts = self._request_with_retries(
            payload, self._transport or self._http_transport
        )
        latency = time.perf_counter() - started

        output_text = extract_output_text(response)
        parsed = normalize_agent_result(parse_json_object(output_text))
        sources = extract_search_sources(response)
        validated = apply_local_validation(strategy, parsed, case, sources)
        record = {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "provider": self.provider,
            "response_id": response.get("id"),
            "response_ids": [response.get("id")],
            "requested_model": self.config.model,
            "model": response.get("model") or self.config.model,
            "strategy": strategy,
            "strategy_label": STRATEGY_LABELS[strategy],
            "prompt_sha256": prompt_sha256(prompt),
            "prompt": prompt,
            "request_config": request_config_view(self.config),
            "structured_output_mode": (
                self.structured_output_mode
                if self.config.structured_output
                else "prompt_json"
            ),
            "latency_seconds": round(latency, 3),
            "http_attempts": attempts,
            "usage": response.get("usage") or {},
            "search_actions": extract_search_actions(response),
            "search_sources": sources,
            "api_citations": extract_api_citations(response),
            "output_text": output_text,
            "result": validated,
            "request_payload": payload,
        }
        if self.config.save_raw_response:
            record["raw_response"] = response
        return record


class AnthropicMessagesWebSearch(_RetryingClient):
    """Anthropic Messages client with search then schema-constrained normalization.

    Anthropic citations cannot be combined with output_config.format in one
    request. Formal mode therefore uses two fixed stages with the same model and
    effort: a forced-search research call and a JSON-Schema normalization call.
    """

    provider = ANTHROPIC_PROVIDER
    structured_output_mode = "anthropic_two_stage_json_schema"

    def __init__(
        self,
        config: RequestConfig,
        *,
        transport: Transport | None = None,
        api_key: str | None = None,
        auth_token: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(config)
        self._transport = transport
        self._api_key = api_key
        self._auth_token = auth_token
        self._base_url = safe_base_url(
            base_url or os.getenv("ANTHROPIC_BASE_URL") or DEFAULT_ANTHROPIC_BASE_URL
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def _http_transport(self, payload: dict[str, Any]) -> dict[str, Any]:
        auth_token = self._auth_token or os.getenv("ANTHROPIC_AUTH_TOKEN")
        api_key = self._api_key or os.getenv("ANTHROPIC_API_KEY")
        if not auth_token and not api_key:
            raise RuntimeError(credential_help(self.provider))
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "User-Agent": HTTP_USER_AGENT,
        }
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        else:
            headers["x-api-key"] = str(api_key)
        request = urllib.request.Request(
            _endpoint(self._base_url, "messages"),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = redact_sensitive(
                exc.read().decode("utf-8", errors="replace")
            )
            raise RuntimeError(
                f"Anthropic-compatible API HTTP {exc.code}: {detail[:800]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Anthropic-compatible API network error: {exc.reason}"
            ) from exc

    def build_research_payload(
        self, case: dict[str, Any], strategy: str
    ) -> dict[str, Any]:
        research_task = build_research_prompt(case, strategy)
        tool = {
            "type": ANTHROPIC_WEB_SEARCH_TOOL,
            "name": "web_search",
            "max_uses": self.config.max_tool_calls,
            "allowed_callers": ["direct"],
            "response_inclusion": "full",
        }
        return {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": (
                "You are a financial research agent. Always search the live web "
                "before answering. Prefer primary sources. Write a concise research "
                "memo with native citations; do not output JSON in this stage. "
                "The user message is deliberately only a compact first-search query. "
                "Follow the complete research task below after the search.\n\n"
                f"Complete research task:\n{research_task}"
            ),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        atlas_initial_query(case)
                        if strategy == ATLAS_STRATEGY
                        else case["question_zh"]
                    ),
                }
            ],
            "tools": [tool],
            "tool_choice": (
                {"type": "tool", "name": "web_search"}
                if self.config.force_search
                else {"type": "auto"}
            ),
            "output_config": {"effort": self.config.reasoning_effort},
        }

    def build_normalization_payload(
        self,
        case: dict[str, Any],
        strategy: str,
        research_text: str,
        citations: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalization_input = {
            "original_task": build_prompt(case, strategy),
            "research_draft": research_text,
            "retrieved_sources": sources,
            "citations": citations,
        }
        return {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": (
                "Convert the supplied web-research draft into the required schema. "
                "Do not add facts, URLs, dates, values, or metadata that are absent "
                "from the supplied material. Use null for unknown metadata."
            ),
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(normalization_input, ensure_ascii=False),
                }
            ],
            "output_config": {
                "effort": self.config.reasoning_effort,
                "format": {
                    "type": "json_schema",
                    "schema": AGENT_RESULT_SCHEMA,
                },
            },
        }

    def run(self, case: dict[str, Any], strategy: str) -> dict[str, Any]:
        prompt = build_prompt(case, strategy)
        research_prompt = build_research_prompt(case, strategy)
        transport = self._transport or self._http_transport
        research_payload = self.build_research_payload(case, strategy)
        started = time.perf_counter()
        research, research_attempts = self._request_with_retries(
            research_payload, transport
        )
        if research.get("stop_reason") == "pause_turn":
            raise RuntimeError(
                "Anthropic search returned pause_turn; reduce the task or implement "
                "a bounded continuation before treating this run as complete."
            )

        research_text = extract_anthropic_output_text(research)
        citations = extract_anthropic_citations(research)
        sources = extract_anthropic_search_sources(research)
        normalize: dict[str, Any] | None = None
        normalize_attempts = 0
        output_text = research_text

        if self.config.structured_output:
            normalize_payload = self.build_normalization_payload(
                case, strategy, research_text, citations, sources
            )
            normalize, normalize_attempts = self._request_with_retries(
                normalize_payload, transport
            )
            output_text = extract_anthropic_output_text(normalize)

        latency = time.perf_counter() - started
        parsed = normalize_agent_result(parse_json_object(output_text))
        validated = apply_local_validation(strategy, parsed, case, sources)
        model = research.get("model") or self.config.model
        normalize_model = (normalize or {}).get("model") if normalize else None
        usage = (
            _combined_usage(
                research.get("usage") or {}, (normalize or {}).get("usage") or {}
            )
            if normalize
            else research.get("usage") or {}
        )
        response_ids = [
            value
            for value in (research.get("id"), (normalize or {}).get("id"))
            if value
        ]
        record = {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "provider": self.provider,
            "response_id": research.get("id"),
            "normalization_response_id": (normalize or {}).get("id"),
            "response_ids": response_ids,
            "requested_model": self.config.model,
            "model": model,
            "normalization_model": normalize_model,
            "strategy": strategy,
            "strategy_label": STRATEGY_LABELS[strategy],
            "prompt_sha256": prompt_sha256(prompt),
            "prompt": prompt,
            "research_prompt_sha256": prompt_sha256(research_prompt),
            "research_prompt": research_prompt,
            "request_config": request_config_view(self.config),
            "structured_output_mode": (
                self.structured_output_mode
                if self.config.structured_output
                else "prompt_json"
            ),
            "latency_seconds": round(latency, 3),
            "http_attempts": research_attempts + normalize_attempts,
            "usage": usage,
            "search_actions": extract_anthropic_search_actions(research),
            "search_sources": sources,
            "api_citations": citations,
            "research_output_text": research_text,
            "output_text": output_text,
            "result": validated,
            "request_payloads": {
                "research": research_payload,
                "normalization": normalize_payload if normalize else None,
            },
        }
        if self.config.save_raw_response:
            record["raw_response"] = {
                "research": research,
                "normalization": normalize,
            }
        return record


def create_client(
    provider: str,
    config: RequestConfig,
    *,
    base_url: str | None = None,
    transport: Transport | None = None,
) -> OpenAIResponsesWebSearch | AnthropicMessagesWebSearch:
    if provider == OPENAI_PROVIDER:
        return OpenAIResponsesWebSearch(
            config, base_url=base_url, transport=transport
        )
    if provider == ANTHROPIC_PROVIDER:
        return AnthropicMessagesWebSearch(
            config, base_url=base_url, transport=transport
        )
    raise ValueError(f"Unknown provider: {provider}")
