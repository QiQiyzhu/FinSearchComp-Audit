from __future__ import annotations

import hashlib
import json
from typing import Any

from .live_agent import (
    AGENT_RESULT_SCHEMA,
    AnthropicMessagesWebSearch,
    RequestConfig,
    apply_local_validation,
    extract_anthropic_output_text,
    normalize_agent_result,
    parse_json_object,
)
from .live_validate import canonical_sha256


FUSION_STRATEGY = "atlas_fusion"
FUSION_LABEL = "ATLAS-Fusion（跨轨迹仲裁）"
FUSION_METHOD_VERSION = "atlas-fusion-1.0"


def case_metadata(case: dict[str, Any]) -> dict[str, Any]:
    """Return only runtime fields; evaluation labels never enter fusion prompts."""

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


def _deduplicate_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        url = str(item.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(item)
    return result


def build_fusion_packet(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("Fusion requires at least one source trajectory")
    metadata = case_metadata(records[0]["case"])
    if any(case_metadata(record["case"]) != metadata for record in records):
        raise ValueError("Fusion records must describe the same case")

    cited_urls = {
        str(item.get("url"))
        for record in records
        for item in (record.get("api_citations") or [])
        if item.get("url")
    }
    cited_urls.update(
        str(item.get("url"))
        for record in records
        for item in ((record.get("result") or {}).get("evidence") or [])
        if item.get("url")
    )
    source_metadata = _deduplicate_by_url(
        [
            dict(item)
            for record in records
            for item in (record.get("search_sources") or [])
            if item.get("url") in cited_urls
        ]
    )
    trajectories = []
    for record in records:
        result = record.get("result") or {}
        trajectories.append(
            {
                "strategy": record["strategy"],
                "candidate": {
                    "model_action": result.get("model_action", result.get("action")),
                    "answer_value": result.get("answer_value"),
                    "unit": result.get("unit"),
                    "explanation": result.get("explanation"),
                    "evidence": result.get("evidence") or [],
                },
                "research_memo": record.get("research_output_text") or "",
                "native_citations": record.get("api_citations") or [],
            }
        )
    packet = {
        "task": metadata,
        "independent_search_trajectories": trajectories,
        "cited_source_metadata": source_metadata,
    }
    serialized = json.dumps(packet, ensure_ascii=False)
    forbidden = ("gold_answer", "source_url", "evidence_text_zh")
    if any(field in serialized for field in forbidden):
        raise RuntimeError("Evaluation label leaked into ATLAS-Fusion packet")
    return packet


def build_fusion_payload(
    records: list[dict[str, Any]],
    *,
    model: str,
    effort: str = "medium",
    max_output_tokens: int = 4800,
) -> tuple[dict[str, Any], dict[str, Any]]:
    packet = build_fusion_packet(records)
    payload = {
        "model": model,
        "max_tokens": max_output_tokens,
        "system": (
            "You are the listwise evidence-arbitration stage of ATLAS-RAG. The "
            "input contains independent real Web Search trajectories for one "
            "financial point-in-time question. Do not vote blindly and do not use "
            "unstated memory. Resolve candidate conflicts by checking the cited "
            "evidence: prefer regulator filings, official statistics, central-bank "
            "releases, issuer reports, and direct market series; require the exact "
            "requested period, version and unit; and reject explicit post-cutoff "
            "evidence. Missing metadata is uncertainty, not automatic contradiction. "
            "For a derived answer, recompute from cited operands and briefly state "
            "the formula in evidence_text. Every output evidence URL must already "
            "appear in the supplied packet. If the packet is insufficient or the "
            "conflict cannot be resolved, abstain. Return only the schema output."
        ),
        "messages": [
            {
                "role": "user",
                "content": json.dumps(packet, ensure_ascii=False),
            }
        ],
        "output_config": {
            "effort": effort,
            "format": {
                "type": "json_schema",
                "schema": AGENT_RESULT_SCHEMA,
            },
        },
    }
    return payload, packet


def union_search_sources(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _deduplicate_by_url(
        [
            dict(item)
            for record in records
            for item in (record.get("search_sources") or [])
        ]
    )


def union_citations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _deduplicate_by_url(
        [
            dict(item)
            for record in records
            for item in (record.get("api_citations") or [])
        ]
    )


class AtlasFusionClient:
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

    def run(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        payload, packet = build_fusion_payload(
            records,
            model=self.config.model,
            effort=self.config.reasoning_effort,
            max_output_tokens=self.config.max_output_tokens,
        )
        response, attempts = self.client._request_with_retries(
            payload, self.client._http_transport
        )
        if response.get("stop_reason") == "max_tokens":
            raise RuntimeError("ATLAS-Fusion exhausted max_tokens")
        output_text = extract_anthropic_output_text(response)
        parsed = normalize_agent_result(parse_json_object(output_text))
        sources = union_search_sources(records)
        validated = apply_local_validation(
            "atlas_rag", parsed, records[0]["case"], sources
        )
        source_urls = {str(item.get("url")) for item in sources if item.get("url")}
        evidence_urls = {
            str(item.get("url"))
            for item in validated.get("evidence") or []
            if item.get("url")
        }
        if not evidence_urls.issubset(source_urls):
            raise RuntimeError("ATLAS-Fusion emitted an URL outside source trajectories")
        return {
            "method_version": FUSION_METHOD_VERSION,
            "strategy": FUSION_STRATEGY,
            "strategy_label": FUSION_LABEL,
            "response_id": response.get("id"),
            "requested_model": self.config.model,
            "model": response.get("model") or self.config.model,
            "http_attempts": attempts,
            "usage": response.get("usage") or {},
            "input_packet_sha256": canonical_sha256(packet),
            "input_trajectory_count": len(records),
            "input_search_calls": sum(
                len(record.get("search_actions") or []) for record in records
            ),
            "input_source_count": len(sources),
            "input_citation_count": len(union_citations(records)),
            "search_sources": sources,
            "api_citations": union_citations(records),
            "output_text": output_text,
            "result": validated,
            "request_payload": payload,
            "raw_response": response,
            "raw_response_sha256": hashlib.sha256(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
