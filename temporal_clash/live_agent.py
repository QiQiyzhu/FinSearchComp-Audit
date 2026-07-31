from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable


DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

STRATEGIES = (
    "plain_agent",
    "temporal_prompt",
    "metadata_filter",
    "teg_validator",
)

STRATEGY_LABELS = {
    "plain_agent": "普通搜索 Agent",
    "temporal_prompt": "时间约束 Prompt",
    "metadata_filter": "元数据过滤器",
    "teg_validator": "完整证据验证器",
}

LOCAL_VALIDATION_PROFILES = {
    "plain_agent": (),
    "temporal_prompt": (),
    "metadata_filter": ("published_at", "target_period", "revision"),
    "teg_validator": ("published_at", "target_period", "revision", "unit"),
}

OUTPUT_CONTRACT = """
Return only one JSON object, without Markdown fences:
{
  "action": "answer" or "abstain",
  "answer_value": "numeric string, or null",
  "unit": "the requested canonical unit, or null",
  "explanation": "one short sentence",
  "evidence": [
    {
      "url": "https://...",
      "title": "source title",
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
""".strip()


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
    raise ValueError(f"Unknown strategy: {strategy}")


def build_prompt(case: dict[str, Any], strategy: str) -> str:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    return "\n\n".join(
        [
            _strategy_rules(strategy, case),
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
    return [
        item.get("action", {})
        for item in response.get("output", [])
        if item.get("type") == "web_search_call"
    ]


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
) -> dict[str, Any]:
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
    reasoning_effort: str = "low"
    search_context_size: str = "low"
    max_output_tokens: int = 1200
    timeout_seconds: int = 120
    max_retries: int = 0


Transport = Callable[[dict[str, Any]], dict[str, Any]]


class OpenAIResponsesWebSearch:
    """Minimal Responses API client that keeps API keys out of files and traces."""

    def __init__(
        self,
        config: RequestConfig,
        *,
        transport: Transport | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._api_key = api_key
        self._base_url = (
            base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")

    def _http_transport(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Use plan mode first, then set the "
                "environment variable before --confirm-live."
            )
        request = urllib.request.Request(
            f"{self._base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI API network error: {exc.reason}") from exc

    def run(self, case: dict[str, Any], strategy: str) -> dict[str, Any]:
        prompt = build_prompt(case, strategy)
        payload = {
            "model": self.config.model,
            "instructions": (
                "You are a financial research agent. Use web search when needed. "
                "Follow the requested JSON contract exactly."
            ),
            "input": prompt,
            "tools": [
                {
                    "type": "web_search",
                    "search_context_size": self.config.search_context_size,
                }
            ],
            "reasoning": {"effort": self.config.reasoning_effort},
            "max_output_tokens": self.config.max_output_tokens,
            "store": False,
        }

        started = time.perf_counter()
        response: dict[str, Any] | None = None
        error: Exception | None = None
        transport = self._transport or self._http_transport
        for attempt in range(1, self.config.max_retries + 2):
            try:
                response = transport(payload)
                error = None
                break
            except Exception as exc:
                error = exc
                if attempt > self.config.max_retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))
        latency = time.perf_counter() - started
        if response is None:
            raise RuntimeError(str(error) if error else "Unknown API failure")

        output_text = extract_output_text(response)
        parsed = normalize_agent_result(parse_json_object(output_text))
        validated = apply_local_validation(strategy, parsed, case)
        return {
            "response_id": response.get("id"),
            "model": response.get("model") or self.config.model,
            "strategy": strategy,
            "strategy_label": STRATEGY_LABELS[strategy],
            "prompt_sha256": prompt_sha256(prompt),
            "prompt": prompt,
            "latency_seconds": round(latency, 3),
            "usage": response.get("usage") or {},
            "search_actions": extract_search_actions(response),
            "api_citations": extract_api_citations(response),
            "output_text": output_text,
            "result": validated,
        }
