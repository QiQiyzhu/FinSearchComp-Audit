"""Exact metric computation and independently answerable research requests."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .planning import DERIVED, metric_label
from .sources import METRICS, Source, select_facts


def rounded(value: Decimal, places: int = 2) -> str:
    return format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def format_value(value: Decimal, unit: str) -> str:
    if unit == "USD":
        return f"${rounded(value / Decimal(1000000000))}B"
    return rounded(value) + (" 个百分点" if unit == "percentage_points" else "%")


def collect_periods(source: Source, ticker: str, as_of: str, plan: dict[str, Any]) -> tuple[dict, dict, list, list]:
    """Fetch once, select each explicit period locally, deduplicate evidence IDs."""
    initial, _, initial_missing = select_facts(source, ticker, as_of, plan["fiscal_year"])
    requested = list(dict.fromkeys([plan["fiscal_year"]] + [year for item in plan["requests"] for year in item["fiscal_years"]]))
    periods: dict[int | None, dict[str, Any]] = {}
    evidence, identifiers = [], {}
    primary = None
    for requested_year in requested:
        facts, local_evidence, _ = select_facts(source, ticker, as_of, requested_year)
        if not facts:
            continue
        replacements = {}
        for item in local_evidence:
            key = (item["metric"], item["period_start"], item["period_end"], item["published_at"], item["accession"], item["value"])
            if key not in identifiers:
                identifiers[key] = f"E{len(evidence) + 1:02d}"
                evidence.append({**item, "id": identifiers[key]})
            replacements[item["id"]] = identifiers[key]
        for suffix in ["", "_prior"]:
            group = {metric: {**facts[metric + suffix], "evidence_id": replacements[facts[metric + suffix]["evidence_id"]]}
                     for metric in METRICS if metric + suffix in facts}
            if not group:
                continue
            year = next(iter(group.values())).get("fiscal_year")
            # Unknown fiscal labels may support a latest-period value, but must
            # never be relabelled using a calendar end year or overwrite it.
            if suffix and year is None:
                continue
            periods[year] = group
            if requested_year == plan["fiscal_year"] and not suffix:
                primary = year
    legacy = dict(periods.get(primary, {})) if initial else {}
    comparison_year = None
    if primary is not None:
        older_requested = [year for year in plan["requested_years"] if year < primary]
        comparison_year = min(older_requested) if len(older_requested) == 1 else primary - 1
    if comparison_year in periods:
        legacy.update({metric + "_prior": row for metric, row in periods[comparison_year].items()})
    return legacy, periods, evidence, initial_missing


def compute_metric(metric: str, facts: dict[str, Any]) -> dict[str, Any]:
    inputs = DERIVED.get(metric, {}).get("inputs", [metric])
    missing = [item for item in inputs if item not in facts]
    refs = [facts[item]["evidence_id"] for item in inputs if item in facts]
    if missing:
        return {"reason": "缺少同期间的" + "、".join(metric_label(item) for item in missing) + "证据。", "evidence_ids": refs, "missing_inputs": missing}
    rows = [facts[item] for item in inputs]
    if len({(row["start"], row["end"]) for row in rows}) != 1:
        return {"reason": "参与计算的指标期间不一致，未进行计算。", "evidence_ids": refs, "missing_inputs": []}
    values = [Decimal(row["value"]) for row in rows]
    unit = DERIVED.get(metric, {}).get("unit", "USD")
    formula = "SEC reported value"
    if metric not in DERIVED:
        value = values[0]
    elif metric == "free_cash_flow":
        value = values[0] - values[1]
        formula = f"{rows[0]['value']} − {rows[1]['value']}"
    else:
        if values[1] <= 0:
            return {"reason": f"{metric_label(inputs[1])}为零或负数，{metric_label(metric)}不具备稳定的正分母解释，暂不输出该比率。", "evidence_ids": refs, "missing_inputs": []}
        value = values[0] / values[1] * 100
        formula = f"{rows[0]['value']} / {rows[1]['value']} × 100"
    return {"raw_value": value, "value": format(value, "f") if unit == "USD" else rounded(value), "unit": unit,
            "display_value": format_value(value, unit), "formula": formula, "evidence_ids": refs,
            "period_start": rows[0]["start"], "period_end": rows[0]["end"], "fiscal_year": rows[0].get("fiscal_year"), "reason": None}


def build_answers(plan: dict[str, Any], periods: dict, latest_fiscal_year: int | None) -> list[dict[str, Any]]:
    answers = []

    def append(item: dict[str, Any]) -> None:
        item["id"] = f"A{len(answers) + 1:02d}"
        answers.append(item)

    for task in plan["requests"]:
        metric, operation = task["metric_id"], task["operation"]
        requested_years = task["fiscal_years"] or ([latest_fiscal_year] if operation == "value" else [latest_fiscal_year - 1, latest_fiscal_year] if latest_fiscal_year is not None else [None, None])
        target_years = requested_years if operation == "value" else [task.get("target_fiscal_year") or (max(year for year in requested_years if year is not None) if any(year is not None for year in requested_years) else None)]
        for target in target_years:
            prior_year = (task.get("comparison_fiscal_year") or min(requested_years)) if operation != "value" and None not in requested_years else None
            label = metric_label(metric) + {"value": "", "growth_pct": "变化率", "growth_amount": "变化额", "change_pp": "变化（百分点）"}[operation]
            item = {"label": label, "metric_id": metric, "operation": operation, "answerability": "missing_evidence",
                    "value": None, "display_value": "暂不可计算", "unit": DERIVED.get(metric, {}).get("unit", "USD"),
                    "formula": "", "evidence_ids": [], "fiscal_year": target, "comparison_fiscal_year": prior_year,
                    "period_start": None, "period_end": None, "comparison_period_start": None, "comparison_period_end": None,
                    "reason": None, "text": "", "missing_inputs": []}
            if task.get("reason"):
                item.update(answerability="unsupported", reason=task["reason"])
            elif operation != "value" and (prior_year is None or prior_year == target):
                item.update(reason="缺少可确认的两个独立年度端点，未推测比较期间。")
            else:
                current = compute_metric(metric, periods.get(target, {}))
                item.update({key: current[key] for key in ["evidence_ids", "period_start", "period_end", "missing_inputs"] if key in current})
                if current["reason"]:
                    item["reason"] = f"FY{target}：" + current["reason"] if target is not None else current["reason"]
                elif operation == "value":
                    item.update({key: current[key] for key in ["value", "display_value", "unit", "formula"]})
                    item["answerability"] = "answered"
                else:
                    prior = compute_metric(metric, periods.get(prior_year, {}))
                    item["evidence_ids"] = list(dict.fromkeys(item["evidence_ids"] + prior["evidence_ids"]))
                    item["comparison_period_start"] = prior.get("period_start")
                    item["comparison_period_end"] = prior.get("period_end")
                    if prior["reason"]:
                        item["reason"] = f"FY{prior_year}：" + prior["reason"]
                        item["missing_inputs"] = prior.get("missing_inputs", [])
                    elif operation == "growth_pct" and prior["raw_value"] <= 0:
                        item["reason"] = "比较期基数为零或负数，百分比增长率不适合解释；绝对变化额仍可单独请求。"
                        item["unit"] = "%"
                    else:
                        if operation == "growth_pct":
                            value = (current["raw_value"] / prior["raw_value"] - 1) * 100
                            unit = "%"
                            formula = f"(({current['formula']}) / ({prior['formula']}) − 1) × 100" if metric in DERIVED else f"({current['value']} / {prior['value']} − 1) × 100"
                        else:
                            value = current["raw_value"] - prior["raw_value"]
                            unit = "percentage_points" if current["unit"] == "%" else "USD"
                            formula = f"({current['formula']}) − ({prior['formula']})" if metric in DERIVED else f"{current['value']} − {prior['value']}"
                        item.update(answerability="answered", value=format(value, "f") if unit == "USD" else rounded(value),
                                    display_value=format_value(value, unit), unit=unit, formula=formula)
            period = f"FY{target}" if target is not None else "截止日前最新合格年度"
            if operation != "value" and prior_year is not None:
                period += f" 相比 FY{prior_year}"
            if item["answerability"] == "answered":
                exact = f"（{item['value']} USD）" if item["unit"] == "USD" else ""
                item["text"] = f"{period} {label}为 {item['display_value']}{exact}。"
            else:
                item["text"] = f"{period} {label}暂未回答：{item['reason']}"
            append(item)
    for gap in plan["gaps"]:
        append({"label": "研究范围缺口", "metric_id": "scope", "operation": "unsupported", "answerability": "unsupported",
                "value": None, "display_value": "暂未覆盖", "unit": "", "formula": "", "evidence_ids": [],
                "fiscal_year": None, "comparison_fiscal_year": None, "period_start": None, "period_end": None,
                "comparison_period_start": None, "comparison_period_end": None, "reason": gap, "text": gap, "missing_inputs": []})
    return answers


def metric_table(facts: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics, claims = [], []
    current = {key: value for key, value in facts.items() if not key.endswith("_prior")}
    previous = {key.removesuffix("_prior"): value for key, value in facts.items() if key.endswith("_prior")}

    def claim(metric: str, text: str, kind: str, refs: list[str]) -> None:
        claims.append({"id": f"C{len(claims) + 1:02d}", "metric_id": metric, "text": text, "kind": kind, "evidence_ids": refs})

    for metric in [*METRICS, *DERIVED]:
        value = compute_metric(metric, current)
        if value["reason"]:
            continue
        row = {key: val for key, val in value.items() if key not in {"raw_value", "reason"}}
        row.update(id=metric, label=metric_label(metric))
        row["value_evidence_ids"] = value["evidence_ids"].copy()
        claim(metric, f"截至 {row['period_end']} 的年度{row['label']}为 {row['display_value']}。", "derived" if metric in DERIVED else "verified", row["evidence_ids"].copy())
        old = compute_metric(metric, previous)
        if not old["reason"]:
            refs = list(dict.fromkeys(value["evidence_ids"] + old["evidence_ids"]))
            row.update(comparison_value=old["value"], comparison_period_end=old["period_end"], comparison_fiscal_year=old["fiscal_year"])
            difference = value["raw_value"] - old["raw_value"]
            if value["unit"] == "%":
                row["change_pp"] = rounded(difference)
                row["change_formula"] = f"({value['formula']}) − ({old['formula']})"
                row["evidence_ids"] = refs
                claim(metric, f"{row['label']}相比截至 {old['period_end']} 的比较年度变化 {rounded(difference)} 个百分点。", "derived", refs)
            else:
                row["change_amount"] = format(difference, "f")
                if old["raw_value"] > 0:
                    growth = (value["raw_value"] / old["raw_value"] - 1) * 100
                    row["change_pct"] = rounded(growth)
                    row["change_formula"] = f"({value['value']} / {old['value']} - 1) × 100"
                    row["evidence_ids"] = refs
                    claim(metric, f"{row['label']}相比截至 {old['period_end']} 的比较年度变化 {rounded(growth)}%。", "derived", refs)
                else:
                    row["growth_reason"] = "比较期基数为零或负数，百分比增长不输出。"
        metrics.append(row)
    return metrics, claims
