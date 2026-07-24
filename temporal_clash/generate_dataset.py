from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
BASE_CASES_PATH = HERE / "base_cases.json"
DATASET_PATH = HERE / "data" / "controlled_cases.jsonl"

CONDITIONS = (
    "clean",
    "future_only",
    "period_conflict",
    "unit_conflict",
    "version_conflict",
)

UNIT_SHIFTS = {
    "USD_million": ("USD_billion", Decimal("0.001")),
    "USD_100million": ("USD_billion", Decimal("0.1")),
    "percent": ("basis_point", Decimal("100")),
    "percentage_point": ("basis_point", Decimal("100")),
    "basis_point": ("percent", Decimal("0.01")),
    "ratio": ("percent", Decimal("100")),
    "times": ("percent", Decimal("100")),
    "shares_per_share": ("percent", Decimal("100")),
}


def _format_decimal(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"))
    return format(normalized.normalize(), "f")


def _wrong_value(value: str) -> str:
    number = Decimal(value)
    if number == 0:
        return "1"
    return _format_decimal(number * Decimal("1.10"))


def _other_version(version: str) -> str:
    if version == "first_release":
        return "revised"
    if version == "filed":
        return "restated"
    return "preliminary"


def _clean_candidate(base: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": f"{base['id']}-gold",
        "answer_value": base["gold_answer"],
        "unit": base["canonical_unit"],
        "target_period": base["target_period"],
        "published_at": base["source_published_at"],
        "revision": base["required_version"],
        "source_url": base["source_url"],
        "source_authority": base["source_authority"],
        "evidence_text_zh": base["evidence_text_zh"],
        "is_perturbed": False,
        "perturbation": None,
        "supports_gold": True,
    }


def _build_case(base: dict[str, Any], condition: str) -> dict[str, Any]:
    clean = _clean_candidate(base)
    cutoff = date.fromisoformat(base["cutoff_date"])

    if condition == "clean":
        candidates = [clean]
        expected_action = "answer"
    elif condition == "future_only":
        future = dict(clean)
        future.update(
            {
                "candidate_id": f"{base['id']}-future",
                "published_at": (cutoff + timedelta(days=365)).isoformat(),
                "source_url": f"synthetic://future/{base['id']}",
                "source_authority": "synthetic_future_snapshot",
                "evidence_text_zh": f"【人工扰动：截止日之后发布】{base['evidence_text_zh']}",
                "is_perturbed": True,
                "perturbation": "future_source",
            }
        )
        candidates = [future]
        expected_action = "abstain"
    elif condition == "period_conflict":
        distractor = dict(clean)
        distractor.update(
            {
                "candidate_id": f"{base['id']}-wrong-period",
                "answer_value": _wrong_value(base["gold_answer"]),
                "target_period": f"邻近期间（非{base['target_period']}）",
                "source_url": f"synthetic://period-conflict/{base['id']}",
                "evidence_text_zh": "【人工扰动：错期间】数值来自相邻年度或错误观察窗口。",
                "is_perturbed": True,
                "perturbation": "period_conflict",
                "supports_gold": False,
            }
        )
        candidates = [distractor, clean]
        expected_action = "answer"
    elif condition == "unit_conflict":
        wrong_unit, factor = UNIT_SHIFTS[base["canonical_unit"]]
        distractor = dict(clean)
        distractor.update(
            {
                "candidate_id": f"{base['id']}-wrong-unit",
                "answer_value": _format_decimal(Decimal(base["gold_answer"]) * factor),
                "unit": wrong_unit,
                "source_url": f"synthetic://unit-conflict/{base['id']}",
                "evidence_text_zh": "【人工扰动：错单位】同一数量被改写为另一单位，但上下文未同步说明。",
                "is_perturbed": True,
                "perturbation": "unit_conflict",
                "supports_gold": False,
            }
        )
        candidates = [distractor, clean]
        expected_action = "answer"
    elif condition == "version_conflict":
        distractor = dict(clean)
        distractor.update(
            {
                "candidate_id": f"{base['id']}-wrong-version",
                "answer_value": _wrong_value(base["gold_answer"]),
                "revision": _other_version(base["required_version"]),
                "source_url": f"synthetic://version-conflict/{base['id']}",
                "evidence_text_zh": "【人工扰动：错版本】数值来自初值、修订值或重述版本中的另一版本。",
                "is_perturbed": True,
                "perturbation": "version_conflict",
                "supports_gold": False,
            }
        )
        candidates = [distractor, clean]
        expected_action = "answer"
    else:
        raise ValueError(f"Unknown condition: {condition}")

    return {
        "case_id": f"{base['id']}__{condition}",
        "base_question_id": base["id"],
        "question_zh": base["question_zh"],
        "cutoff_date": base["cutoff_date"],
        "target_period": base["target_period"],
        "required_version": base["required_version"],
        "canonical_unit": base["canonical_unit"],
        "gold_answer": base["gold_answer"],
        "evidence_condition": condition,
        "expected_action": expected_action,
        "candidates": candidates,
        "construction": {
            "real_question": True,
            "evidence_perturbation": condition != "clean",
            "perturbation_method": "deterministic_manual_template",
            "provenance": base["provenance"],
        },
    }


def generate() -> list[dict[str, Any]]:
    bases = json.loads(BASE_CASES_PATH.read_text(encoding="utf-8"))
    cases = [
        _build_case(base, condition)
        for base in bases
        for condition in CONDITIONS
    ]
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    return cases


if __name__ == "__main__":
    generated = generate()
    print(f"Generated {len(generated)} controlled cases at {DATASET_PATH}")
