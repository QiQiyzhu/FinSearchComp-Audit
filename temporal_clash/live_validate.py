from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .live_agent import (
    ANTHROPIC_PROVIDER,
    OPENAI_PROVIDER,
    TRACE_SCHEMA_VERSION,
    prompt_sha256,
)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def observed_search_calls(record: dict[str, Any]) -> int:
    actions = len(record.get("search_actions") or [])
    usage = record.get("usage") or {}
    usage_calls = int(
        ((usage.get("server_tool_use") or {}).get("web_search_requests")) or 0
    )
    return max(actions, usage_calls)


def validate_live_record(
    record: dict[str, Any],
    base_dir: Path | None = None,
) -> list[str]:
    """Return protocol violations that make a live record non-research-ready."""

    violations: list[str] = []
    if record.get("status") != "ok":
        return ["api_run_failed"]
    if record.get("trace_schema_version") != TRACE_SCHEMA_VERSION:
        violations.append("trace_schema_version_mismatch")

    provider = record.get("provider")
    if provider not in {OPENAI_PROVIDER, ANTHROPIC_PROVIDER}:
        violations.append("provider_missing_or_unknown")

    prompt = record.get("prompt")
    if not prompt or record.get("prompt_sha256") != prompt_sha256(str(prompt)):
        violations.append("prompt_hash_mismatch")
    if not record.get("response_ids"):
        violations.append("response_id_missing")
    if record.get("model") is None:
        violations.append("resolved_model_missing")

    config = record.get("request_config") or {}
    if not config.get("force_search"):
        violations.append("forced_search_disabled")
    if not config.get("structured_output"):
        violations.append("structured_output_disabled")
    if not config.get("save_raw_response"):
        violations.append("raw_response_capture_disabled")
    has_inline_raw = "raw_response" in record
    has_linked_raw = bool(
        record.get("raw_response_path") and record.get("raw_response_sha256")
    )
    if not has_inline_raw and not has_linked_raw:
        violations.append("raw_response_missing")
    if has_linked_raw and base_dir is not None:
        root = base_dir.resolve()
        raw_path = (root / str(record["raw_response_path"])).resolve()
        if raw_path != root and root not in raw_path.parents:
            violations.append("raw_response_path_outside_run")
        elif not raw_path.is_file():
            violations.append("raw_response_file_missing")
        else:
            try:
                raw_value = json.loads(raw_path.read_text(encoding="utf-8"))
                if canonical_sha256(raw_value) != record["raw_response_sha256"]:
                    violations.append("raw_response_hash_mismatch")
            except (OSError, json.JSONDecodeError):
                violations.append("raw_response_unreadable")

    search_actions = record.get("search_actions") or []
    search_sources = record.get("search_sources") or []
    if not search_actions:
        violations.append("web_search_action_missing")
    if not search_sources:
        violations.append("complete_search_sources_missing")

    max_tool_calls = int(config.get("max_tool_calls") or 0)
    if max_tool_calls < 1:
        violations.append("max_tool_calls_invalid")
    elif observed_search_calls(record) > max_tool_calls:
        violations.append("max_tool_calls_exceeded")

    expected_mode = {
        OPENAI_PROVIDER: "openai_text_json_schema",
        ANTHROPIC_PROVIDER: "anthropic_two_stage_json_schema",
    }.get(provider)
    if expected_mode and record.get("structured_output_mode") != expected_mode:
        violations.append("structured_output_mode_mismatch")

    if provider == ANTHROPIC_PROVIDER:
        if len(record.get("response_ids") or []) != 2:
            violations.append("anthropic_two_stage_response_missing")
        if record.get("normalization_model") != record.get("model"):
            violations.append("anthropic_stage_model_mismatch")

    source_urls = {
        str(source.get("url"))
        for source in search_sources
        if isinstance(source, dict) and source.get("url")
    }
    citation_urls = {
        str(citation.get("url"))
        for citation in (record.get("api_citations") or [])
        if isinstance(citation, dict) and citation.get("url")
    }
    evidence_urls = {
        str(evidence.get("url"))
        for evidence in ((record.get("result") or {}).get("evidence") or [])
        if isinstance(evidence, dict) and evidence.get("url")
    }
    if not evidence_urls.issubset(source_urls | citation_urls):
        violations.append("evidence_url_not_retrieved")

    if (record.get("result") or {}).get("action") == "answer":
        if not record.get("api_citations"):
            violations.append("answered_without_api_citation")
        if not evidence_urls:
            violations.append("answered_without_evidence_url")

    return violations


def validate_protocol_consistency(
    records: list[dict[str, Any]],
    base_dir: Path | None = None,
) -> list[str]:
    successful = [record for record in records if record.get("status") == "ok"]
    if not successful:
        return ["no_successful_records"]

    violations: list[str] = []
    config_hashes = {
        canonical_sha256(record.get("request_config") or {})
        for record in successful
    }
    if len(config_hashes) != 1:
        violations.append("request_config_changed_between_strategies")

    providers = {record.get("provider") for record in successful}
    requested_models = {record.get("requested_model") for record in successful}
    resolved_models = {record.get("model") for record in successful}
    if len(providers) != 1:
        violations.append("provider_changed_between_strategies")
    if len(requested_models) != 1:
        violations.append("requested_model_changed_between_strategies")
    if len(resolved_models) != 1:
        violations.append("resolved_model_changed_between_strategies")

    for record in successful:
        record_violations = validate_live_record(record, base_dir)
        if record_violations:
            case_id = (record.get("case") or {}).get("id", "unknown")
            strategy = record.get("strategy", "unknown")
            violations.extend(
                f"{case_id}/{strategy}:{item}" for item in record_violations
            )
    return violations
