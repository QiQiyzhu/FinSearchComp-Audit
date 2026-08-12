from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
from typing import Any

from .models import EvidenceDocument, QuerySpec


DEFAULT_DATASET = (
    Path(__file__).resolve().parents[1]
    / "temporal_clash"
    / "data"
    / "controlled_cases.jsonl"
)
_SYNTHETIC_MARKER = re.compile(r"^【人工扰动[^】]*】")


def sanitize_evidence_text(text: str) -> str:
    return _SYNTHETIC_MARKER.sub("", text.strip())


def runtime_document_id(
    candidate: dict[str, Any], *, base_question_id: str
) -> str:
    """Create an opaque ID without gold/future/conflict label words."""

    fields = {
        "base_question_id": base_question_id,
        "text": sanitize_evidence_text(str(candidate.get("evidence_text_zh", ""))),
        "answer_value": str(candidate.get("answer_value", "")),
        "unit": str(candidate.get("unit", "missing")),
        "target_period": str(candidate.get("target_period", "missing")),
        "published_at": str(candidate.get("published_at", "missing")),
        "revision": str(candidate.get("revision", "missing")),
    }
    digest = hashlib.sha256(
        json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return f"doc-{digest}"


def load_cases(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_corpus(cases: list[dict[str, Any]]) -> list[EvidenceDocument]:
    documents: dict[str, EvidenceDocument] = {}
    clean_authorities = {
        str(case["base_question_id"]): str(case["candidates"][0]["source_authority"])
        for case in cases
        if case["evidence_condition"] == "clean"
    }
    for case in cases:
        base_question_id = str(case["base_question_id"])
        for candidate in case["candidates"]:
            document_id = runtime_document_id(
                candidate, base_question_id=base_question_id
            )
            source_url = str(candidate.get("source_url", ""))
            if source_url.startswith("synthetic://"):
                source_url = f"https://evidence.local/{document_id}"
            source_authority = str(candidate.get("source_authority", "unknown"))
            if source_authority == "synthetic_future_snapshot":
                source_authority = clean_authorities[base_question_id]
            document = EvidenceDocument.from_candidate(
                candidate,
                base_question_id=base_question_id,
                document_id=document_id,
                text=sanitize_evidence_text(
                    str(candidate.get("evidence_text_zh", ""))
                ),
                source_url=source_url,
                source_authority=source_authority,
            )
            previous = documents.get(document.document_id)
            if previous is not None and previous != document:
                raise ValueError(
                    f"candidate id {document.document_id!r} has inconsistent content"
                )
            documents[document.document_id] = document
    return [documents[key] for key in sorted(documents)]


def benchmark_queries(cases: list[dict[str, Any]]) -> list[tuple[QuerySpec, dict[str, Any]]]:
    clean_cases = [case for case in cases if case["evidence_condition"] == "clean"]
    return [(QuerySpec.from_case(case), case) for case in clean_cases]


def benchmark_labels(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for case in cases:
        for candidate in case["candidates"]:
            candidate_id = str(candidate["candidate_id"])
            document_id = runtime_document_id(
                candidate, base_question_id=str(case["base_question_id"])
            )
            current = {
                "base_question_id": str(case["base_question_id"]),
                "is_perturbed": bool(candidate["is_perturbed"]),
                "supports_gold": bool(candidate["supports_gold"]),
            }
            previous = labels.get(document_id)
            if previous is not None and previous != current:
                raise ValueError(f"inconsistent benchmark labels for {candidate_id}")
            labels[document_id] = current
    return labels
