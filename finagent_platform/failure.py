from __future__ import annotations

import json
from typing import Any


def classify_decision(decision: dict[str, Any]) -> str | None:
    """Classify completed Agent outcomes for failure analytics."""

    if not isinstance(decision, dict) or decision.get("action") not in {
        "answer",
        "abstain",
    }:
        return "malformed_agent_result"
    if decision["action"] == "answer":
        return None

    reasons = " ".join(str(item) for item in decision.get("rejection_reasons", []))
    trace_text = json.dumps(decision.get("trace", []), ensure_ascii=False)
    combined = reasons + " " + trace_text
    if "检索未返回" in combined or '"returned": 0' in combined:
        return "empty_retrieval"
    if "published_at" in combined or "时间" in combined or "future" in combined.lower():
        return "temporal_violation"
    if "字段检查" in combined or "unit" in combined or "version" in combined:
        return "invalid_evidence"
    if "置信度" in combined or "confidence" in combined.lower():
        return "low_confidence"
    return "insufficient_evidence"


def classify_exception(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "upstream_timeout"
    if isinstance(error, json.JSONDecodeError):
        return "malformed_model_output"
    if isinstance(error, (KeyError, TypeError, ValueError)):
        return "invalid_pipeline_output"
    return "internal_error"
