"""Versioned annual SEC facts for the public, evidence-bound research terminal.

The dataset is reconstructed from a later CompanyFacts capture. It is not a
historically archived SEC database. Date-only ``filed`` timestamps become
available the following calendar day; no same-day / intraday claim is made.
This module deliberately does not import the v2 selector or its calculator.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from .sources import COMPANIES

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = Path(__file__).parent / "data" / "terminal_upstream"
DEFAULT_CUBE = ROOT / "site" / "workbench" / "data" / "finance_cube.json"
FORMS = {"10-K", "10-K/A"}
MIN_YEAR = 2019
MAX_YEAR = 2026

REPORTED = {
    "revenue": ("营业收入", "duration", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]),
    "operating_income": ("营业利润", "duration", ["OperatingIncomeLoss"]),
    "net_income": ("净利润", "duration", ["NetIncomeLoss"]),
    "operating_cash_flow": ("经营现金流", "duration", ["NetCashProvidedByUsedInOperatingActivities"]),
    "capital_expenditure": ("PP&E 现金资本支出", "duration", ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    "research_and_development": ("研发费用", "duration", ["ResearchAndDevelopmentExpense"]),
    "gross_profit": ("毛利", "duration", ["GrossProfit"]),
    "cost_of_revenue": ("营业成本", "duration", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]),
    "assets": ("总资产", "instant", ["Assets"]),
    "liabilities": ("总负债", "instant", ["Liabilities"]),
    "stockholders_equity": ("归属母公司股东权益", "instant", ["StockholdersEquity"]),
    "cash": ("现金及现金等价物", "instant", ["CashAndCashEquivalentsAtCarryingValue"]),
    "current_assets": ("流动资产", "instant", ["AssetsCurrent"]),
    "current_liabilities": ("流动负债", "instant", ["LiabilitiesCurrent"]),
}
DERIVED = {
    "free_cash_flow": ("自由现金流", "USD", "subtract", ["operating_cash_flow", "capital_expenditure"]),
    "operating_margin": ("营业利润率", "%", "ratio", ["operating_income", "revenue"]),
    "net_margin": ("净利率", "%", "ratio", ["net_income", "revenue"]),
    "gross_margin": ("毛利率", "%", "ratio", ["gross_profit", "revenue"]),
    "rd_ratio": ("研发费用率", "%", "ratio", ["research_and_development", "revenue"]),
    "cash_conversion": ("净利润现金转化率", "%", "ratio", ["operating_cash_flow", "net_income"]),
    "liabilities_to_assets": ("资产负债率", "%", "ratio", ["liabilities", "assets"]),
    "current_ratio": ("流动比率", "x", "ratio", ["current_assets", "current_liabilities"]),
    "cash_to_assets": ("现金资产比", "%", "ratio", ["cash", "assets"]),
    "capex_ratio": ("资本支出收入比", "%", "ratio", ["capital_expenditure", "revenue"]),
    "free_cash_flow_margin": ("自由现金流率", "%", "ratio", ["free_cash_flow", "revenue"]),
}
POLICY = {
    "availability": "filed_date_plus_one_calendar_day",
    "availability_note": "SEC CompanyFacts 只有申报日期；保守地从申报后下一日起纳入，不推断申报当日盘中可用性。",
    "version_selection": "latest_available_filing_per_exact_period; unequal same-date values abstain",
    "fiscal_year": "FY filing accession anchor plus consecutive annual end dates; no calendar-year relabeling",
    "scope": "annual US-GAAP USD facts from 10-K and 10-K/A; FY2019–FY2026 when present",
    "source_limitation": "2026-09-22 抓取的 CompanyFacts 历史条目重建，不是当时保存的完整数据库；不包含业绩公告、新闻及盘中数据。",
    "calculation": "exact rational operands; final display rounded half up to 2 decimals; same-accession operands required",
    "missing": "unknown / unavailable_as_of / unsupported / conflict are explicit; missing is never zero",
    "gross_profit_fallback": "When GrossProfit is absent, revenue minus cost_of_revenue from the same accession and period; explicitly marked calculated.",
    "liabilities": "Only reported Liabilities. Assets minus parent equity is not substituted because noncontrolling interests may exist.",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def metric_catalog() -> dict[str, dict[str, Any]]:
    catalog = {key: {"id": key, "label": label, "unit": "USD", "kind": kind, "tags": tags,
                     "inputs": [key], "operation": "reported"}
               for key, (label, kind, tags) in REPORTED.items()}
    for key, (label, unit, operation, inputs) in DERIVED.items():
        catalog[key] = {"id": key, "label": label, "unit": unit, "kind": "instant" if key in {"liabilities_to_assets", "current_ratio", "cash_to_assets"} else "duration",
                        "inputs": inputs, "operation": operation}
    return catalog


def archive_cached_sources(cache_dir: Path, archive_dir: Path = ARCHIVE_DIR) -> dict[str, Any]:
    """Freeze authenticated, already-downloaded payloads; never reads .env/network."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "serialization": "canonical UTF-8 JSON, deterministic gzip mtime=0; not original HTTP bytes", "sources": {}}
    for ticker in COMPANIES:
        stored = json.loads((cache_dir / f"sec-{ticker}.json").read_text(encoding="utf-8"))
        payload = stored["payload"]
        encoded = canonical(payload)
        fingerprint = hashlib.sha256(encoded).hexdigest()
        if fingerprint != stored["sha256"] or payload.get("cik") != COMPANIES[ticker]["cik"]:
            raise ValueError(f"Invalid cache integrity or company identity: {ticker}")
        compressed = gzip.compress(encoded, mtime=0)
        (archive_dir / f"{ticker}.json.gz").write_bytes(compressed)
        manifest["sources"][ticker] = {
            "path": f"{ticker}.json.gz", "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{COMPANIES[ticker]['cik']:010d}.json",
            "canonical_payload_sha256": fingerprint, "upstream_response_sha256": stored["upstream_sha256"],
            "gzip_sha256": hashlib.sha256(compressed).hexdigest(), "retrieved_at": stored["retrieved_at"],
            "bytes_uncompressed": len(encoded), "bytes_gzip": len(compressed),
        }
    (archive_dir / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    return manifest


def load_sources(archive_dir: Path = ARCHIVE_DIR) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
    payloads = {}
    for ticker, metadata in manifest["sources"].items():
        compressed = (archive_dir / metadata["path"]).read_bytes()
        encoded = gzip.decompress(compressed)
        if hashlib.sha256(compressed).hexdigest() != metadata["gzip_sha256"] or hashlib.sha256(encoded).hexdigest() != metadata["canonical_payload_sha256"]:
            raise ValueError(f"Archive hash mismatch: {ticker}")
        payload = json.loads(encoded)
        if payload.get("cik") != COMPANIES[ticker]["cik"]:
            raise ValueError(f"Archive identity mismatch: {ticker}")
        payloads[ticker] = payload
    return payloads, manifest


def candidates(payload: dict[str, Any], captured_date: str) -> dict[str, list[dict[str, Any]]]:
    """Keep exact raw rows plus tag; reject nonannual, non-USD or malformed rows."""
    result = {metric: [] for metric in REPORTED}
    taxonomy = payload.get("facts", {}).get("us-gaap", {})
    captured = date.fromisoformat(captured_date)
    for metric, (_, kind, tags) in REPORTED.items():
        for rank, tag in enumerate(tags):
            for raw in taxonomy.get(tag, {}).get("units", {}).get("USD", []):
                try:
                    end, filed = date.fromisoformat(raw["end"]), date.fromisoformat(raw["filed"])
                    value = Decimal(str(raw["val"]))
                    start = date.fromisoformat(raw["start"]) if kind == "duration" else None
                except (KeyError, ValueError, TypeError, InvalidOperation):
                    continue
                if raw.get("form") not in FORMS or not raw.get("accn") or not value.is_finite() or not end <= filed <= captured:
                    continue
                if kind == "duration" and not (330 <= (end - start).days <= 380):
                    continue
                if kind == "instant" and "start" in raw:
                    continue
                if metric in {"revenue", "capital_expenditure", "research_and_development", "assets", "liabilities", "cash", "current_assets", "current_liabilities", "cost_of_revenue"} and value < 0:
                    continue
                # Retain one look-back year for changes, but do not include all
                # CompanyFacts history in a browser artifact.
                if end.year < MIN_YEAR - 2:
                    continue
                result[metric].append({"raw": raw, "tag": tag, "rank": rank, "value": format(value, "f"), "available_from": next_day(raw["filed"])})
    return result


def fiscal_labels(eligible: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, str], int]:
    """Resolve FY using only rows actually eligible at this event."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for metric, rows in eligible.items():
        if REPORTED[metric][1] != "duration":
            continue
        for item in rows:
            row = item["raw"]
            if row.get("fp") == "FY" and isinstance(row.get("fy"), int):
                groups.setdefault(row["accn"], []).append(row)
    votes: dict[tuple[str, str], set[int]] = {}
    for rows in groups.values():
        years = {row["fy"] for row in rows}
        if len(years) != 1:
            continue
        periods = {(row["start"], row["end"]) for row in rows}
        ends = sorted({end for _, end in periods}, reverse=True)
        if not ends or (date.fromisoformat(max(row["filed"] for row in rows)) - date.fromisoformat(ends[0])).days > 200:
            continue
        labels = {ends[0]: next(iter(years))}
        for newer, older in zip(ends, ends[1:]):
            if newer not in labels or not 330 <= (date.fromisoformat(newer) - date.fromisoformat(older)).days <= 380:
                break
            labels[older] = labels[newer] - 1
        for period in periods:
            if period[1] in labels:
                votes.setdefault(period, set()).add(labels[period[1]])
    return {period: next(iter(labels)) for period, labels in votes.items() if len(labels) == 1}


def _round(value: Fraction, places: int = 2) -> str:
    with localcontext() as context:
        context.prec = 70
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return format(decimal.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def _exact(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _display(value: Fraction, unit: str) -> str:
    if unit == "USD":
        return "$" + _round(value / 1_000_000_000) + "B"
    return _round(value) + {"%": "%", "x": "×", "percentage_points": " 个百分点"}.get(unit, "")


def missing(metric: str, status: str, reason: str, evidence_ids: list[str] | None = None, *, unit: str | None = None) -> dict[str, Any]:
    catalog = metric_catalog()
    return {"status": status, "value": None, "display_value": "—", "unit": unit or catalog.get(metric, {}).get("unit", ""),
            "evidence_ids": evidence_ids or [], "formula": None, "reason": reason}


def _available(value: Fraction, unit: str, refs: list[str], formula: str, operation: str, inputs: list[str]) -> dict[str, Any]:
    if unit == "USD":
        text = str(value.numerator) if value.denominator == 1 else _round(value)
    else:
        text = _round(value)
    return {"status": "available", "value": text, "exact_value": _exact(value), "display_value": _display(value, unit),
            "unit": unit, "evidence_ids": list(dict.fromkeys(refs)), "formula": formula, "operation": operation, "inputs": inputs, "reason": None}


def _period_rows(rows: list[dict[str, Any]], period: tuple[str, str], kind: str) -> list[dict[str, Any]]:
    return [item for item in rows if item["raw"]["end"] == period[1] and (kind == "instant" or item["raw"].get("start") == period[0])]


def _select(rows: list[dict[str, Any]], all_rows: list[dict[str, Any]], period: tuple[str, str], metric: str) -> tuple[dict[str, Any] | None, str]:
    kind = REPORTED[metric][1]
    matching = _period_rows(rows, period, kind)
    if not matching:
        return None, "unavailable_as_of" if _period_rows(all_rows, period, kind) else "unknown"
    latest = max(item["available_from"] for item in matching)
    current = [item for item in matching if item["available_from"] == latest]
    if len({Fraction(item["value"]) for item in current}) != 1:
        return None, "conflict"
    return min(current, key=lambda item: (item["rank"], item["raw"]["accn"], canonical(item["raw"]))), "available"


def _evidence(ticker: str, metric: str, item: dict[str, Any], metadata: dict[str, Any], fiscal_year: int) -> dict[str, Any]:
    row = item["raw"]
    row_sha = sha({"taxonomy": "us-gaap", "tag": item["tag"], "unit": "USD", "row": row})
    identifier = ticker + "-" + row_sha[:16]
    accession = row["accn"]
    return {"id": identifier, "ticker": ticker, "metric_id": metric, "taxonomy": "us-gaap", "taxonomy_tag": item["tag"], "unit": "USD",
            "value": item["value"], "fiscal_year": fiscal_year, "period_start": row.get("start"), "period_end": row["end"],
            "filed": row["filed"], "available_from": item["available_from"], "accession": accession, "form": row["form"], "raw_row": row,
            "url": f"https://www.sec.gov/Archives/edgar/data/{COMPANIES[ticker]['cik']}/{accession.replace('-', '')}/{accession}-index.htm",
            "source_url": metadata["source_url"], "retrieved_at": metadata["retrieved_at"],
            "canonical_payload_sha256": metadata["canonical_payload_sha256"], "upstream_response_sha256": metadata["upstream_response_sha256"], "fact_sha256": row_sha}


def _calculate(metric: str, metrics: dict[str, Any], evidence: dict[str, Any], *, override: tuple | None = None) -> dict[str, Any]:
    _, unit, operation, inputs = override or DERIVED[metric]
    operands = [metrics[key] for key in inputs]
    refs = list(dict.fromkeys(ref for value in operands for ref in value["evidence_ids"]))
    unavailable = [key for key in inputs if metrics[key]["status"] != "available"]
    if unavailable:
        statuses = {metrics[key]["status"] for key in unavailable}
        status = next((item for item in ["conflict", "unavailable_as_of", "unsupported", "unknown"] if item in statuses), "unknown")
        return missing(metric, status, "缺少可用的同期间输入：" + "、".join(metric_catalog()[key]["label"] for key in unavailable), refs, unit=unit)
    source_rows = [evidence[ref] for ref in refs]
    if len({row["accession"] for row in source_rows}) != 1:
        return missing(metric, "unknown", "操作数来自不同申报版本；未混合版本计算。", refs, unit=unit)
    if len({row["period_end"] for row in source_rows}) != 1 or len({row["period_start"] for row in source_rows if row["period_start"] is not None}) > 1:
        return missing(metric, "conflict", "操作数期间不一致。", refs, unit=unit)
    values = [Fraction(value["exact_value"]) for value in operands]
    if operation == "subtract":
        value = values[0] - values[1]
        formula = f"{inputs[0]} − {inputs[1]}"
    else:
        if values[1] <= 0:
            return missing(metric, "unsupported", "分母为零或负数，不输出缺乏稳定正分母解释的比率。", refs, unit=unit)
        factor = 100 if unit == "%" else 1
        value = values[0] / values[1] * factor
        formula = f"{inputs[0]} / {inputs[1]}" + (" × 100" if factor == 100 else "")
    return _available(value, unit, refs, formula, operation, inputs)


def _annual(ticker: str, year: int, period: tuple[str, str], eligible: dict, all_rows: dict, metadata: dict, evidence: dict) -> dict:
    metrics = {}
    for metric in REPORTED:
        selected, status = _select(eligible[metric], all_rows[metric], period, metric)
        if selected is None:
            reasons = {"unknown": "该期间没有收录此口径的年度 USD 事实。", "unavailable_as_of": "该指标仅存在于截止日之后的申报中。", "conflict": "最新可用申报日存在不同数值，未自动取舍。"}
            metrics[metric] = missing(metric, status, reasons[status])
            continue
        item = _evidence(ticker, metric, selected, metadata, year)
        if item["id"] in evidence and evidence[item["id"]] != item:
            raise ValueError("Evidence identifier collision")
        evidence[item["id"]] = item
        metrics[metric] = _available(Fraction(selected["value"]), "USD", [item["id"]], f"us-gaap:{selected['tag']}", "reported", [metric])
    if metrics["gross_profit"]["status"] in {"unknown", "unavailable_as_of"}:
        fallback = _calculate("gross_profit", metrics, evidence, override=("毛利", "USD", "subtract", ["revenue", "cost_of_revenue"]))
        if fallback["status"] == "available":
            fallback["note"] = "由同份申报的营业收入减营业成本计算，非 GrossProfit 直接报告值。"
            metrics["gross_profit"] = fallback
    for metric in DERIVED:
        metrics[metric] = _calculate(metric, metrics, evidence)
    return {"fiscal_year": year, "period_start": period[0], "period_end": period[1], "metrics": metrics}


def _changes(current: dict, previous: dict | None, *, amount: bool = False) -> dict:
    result = {}
    for metric, value in current["metrics"].items():
        original_unit = metric_catalog()[metric]["unit"]
        if amount and original_unit != "USD":
            continue
        unit = "USD" if amount else "%" if original_unit == "USD" else "percentage_points" if original_unit == "%" else "x"
        prior = previous["metrics"].get(metric) if previous else None
        refs = list(dict.fromkeys(value["evidence_ids"] + (prior["evidence_ids"] if prior else [])))
        if previous is None or prior is None:
            result[metric] = missing(metric, "unknown", "缺少前一连续财政年度。", refs, unit=unit)
            continue
        if not 330 <= (date.fromisoformat(current["period_end"]) - date.fromisoformat(previous["period_end"])).days <= 380:
            result[metric] = missing(metric, "unsupported", "两个年度端点不连续，未按同比计算。", refs, unit=unit)
            continue
        unavailable = [item["status"] for item in [value, prior] if item["status"] != "available"]
        if unavailable:
            status = next((item for item in ["conflict", "unavailable_as_of", "unsupported", "unknown"] if item in unavailable), "unknown")
            result[metric] = missing(metric, status, "当前或前一年度指标不可用。", refs, unit=unit)
            continue
        a, b = Fraction(value["exact_value"]), Fraction(prior["exact_value"])
        if amount:
            change, operation, formula = a - b, "growth_amount", "current − prior"
        elif original_unit == "USD":
            if b <= 0:
                result[metric] = missing(metric, "unsupported", "前期基数为零或负数，未输出同比百分比。", refs, unit=unit)
                continue
            change, operation, formula = (a - b) / b * 100, "growth_pct", "(current − prior) / prior × 100"
        else:
            change, operation, formula = a - b, "change_pp" if original_unit == "%" else "change_multiple", "current − prior (using unrounded operands)"
        result[metric] = _available(change, unit, refs, formula, operation, [metric])
        result[metric]["comparison_fiscal_year"] = previous["fiscal_year"]
    return result


def build_cube(payloads: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    evidence, companies = {}, {}
    for ticker, payload in payloads.items():
        metadata = manifest["sources"][ticker]
        all_rows = candidates(payload, metadata["retrieved_at"][:10])
        event_dates = sorted({item["available_from"] for rows in all_rows.values() for item in rows})
        annuals, events = {}, []
        for cutoff in event_dates:
            eligible = {metric: [item for item in rows if item["available_from"] <= cutoff] for metric, rows in all_rows.items()}
            labels = fiscal_labels(eligible)
            # Multiple duration boundaries for one FY are not silently mixed.
            periods_by_year: dict[int, list[tuple[str, str]]] = {}
            for period, year in labels.items():
                if MIN_YEAR <= year <= MAX_YEAR and _period_rows(eligible["revenue"], period, "duration"):
                    periods_by_year.setdefault(year, []).append(period)
            local = {}
            for year, periods in sorted(periods_by_year.items()):
                if len(periods) == 1:
                    local[year] = _annual(ticker, year, periods[0], eligible, all_rows, metadata, evidence)
            if not local:
                continue
            identifiers = []
            for year, annual in sorted(local.items()):
                annual["changes"] = _changes(annual, local.get(year - 1))
                annual["amount_changes"] = _changes(annual, local.get(year - 1), amount=True)
                identifier = f"{ticker}-{year}-{sha(annual)[:12]}"
                annuals.setdefault(identifier, {"id": identifier, **annual})
                identifiers.append(identifier)
            if events and events[-1]["annual_ids"] == identifiers:
                continue
            event_rows = [item for rows in all_rows.values() for item in rows if item["available_from"] == cutoff]
            events.append({"available_from": cutoff, "filed": (date.fromisoformat(cutoff) - timedelta(days=1)).isoformat(),
                           "accessions": sorted({item["raw"]["accn"] for item in event_rows}), "annual_ids": identifiers})
        companies[ticker] = {"ticker": ticker, **{key: COMPANIES[ticker][key] for key in ["name", "cik"]},
                             "events": events, "annuals": annuals, "source": metadata,
                             "fiscal_years": sorted({item["fiscal_year"] for item in annuals.values()})}
    # A metric could select evidence during an event later deduplicated or
    # omitted. Publish only citations reachable from the final artifact.
    used = {ref for company in companies.values() for annual in company["annuals"].values()
            for section in ["metrics", "changes", "amount_changes"] for metric in annual[section].values() for ref in metric["evidence_ids"]}
    evidence = {key: evidence[key] for key in sorted(used)}
    return {"schema_version": "3.0.0", "dataset_id": "sec-annual-terminal-v3", "captured_at": max(item["retrieved_at"] for item in manifest["sources"].values()),
            "source_manifest_sha256": sha(manifest), "policy": POLICY, "metric_catalog": metric_catalog(),
            "companies": companies, "evidence": evidence,
            "statistics": {"companies": len(companies), "metrics": len(metric_catalog()), "events": sum(len(item["events"]) for item in companies.values()),
                           "annual_versions": sum(len(item["annuals"]) for item in companies.values()), "evidence_rows": len(evidence)}}


def load_cube(path: Path | str | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_CUBE).read_text(encoding="utf-8"))


def select_state(cube: dict[str, Any], ticker: str, cutoff: str) -> dict[str, Any]:
    date.fromisoformat(cutoff)
    company = cube["companies"].get(ticker.upper())
    if company is None:
        return {"status": "unsupported", "ticker": ticker.upper(), "cutoff": cutoff, "event": None, "annuals": [], "reason": "公司尚未收录。"}
    if cutoff > company["source"]["retrieved_at"][:10]:
        return {"status": "unknown", "ticker": ticker.upper(), "cutoff": cutoff, "event": None, "annuals": [], "reason": "截止日超过此次数据抓取日；不能保证覆盖此后发布的申报。"}
    events = [item for item in company["events"] if item["available_from"] <= cutoff]
    if not events:
        return {"status": "unavailable_as_of", "ticker": ticker.upper(), "cutoff": cutoff, "event": None, "annuals": [], "reason": "截止日之前尚无收录的可用年度申报。"}
    event = events[-1]
    return {"status": "available", "ticker": ticker.upper(), "cutoff": cutoff, "event": event,
            "annuals": sorted((company["annuals"][identifier] for identifier in event["annual_ids"]), key=lambda item: item["fiscal_year"]), "reason": None}


def query_metric(cube: dict[str, Any], ticker: str, cutoff: str, fiscal_year: int | None, metric_id: str) -> dict[str, Any]:
    """Return one typed, independently verifiable value; never fills from future."""
    base = {"ticker": ticker.upper(), "entity": cube["companies"].get(ticker.upper(), {}).get("name"), "metric_id": metric_id,
            "fiscal_year": fiscal_year, "period_start": None, "period_end": None,
            "required_inputs": cube["metric_catalog"].get(metric_id, {}).get("inputs", [])}
    if metric_id not in cube["metric_catalog"]:
        return {**base, **missing(metric_id, "unsupported", "该指标尚未纳入年度财报指标目录。")}
    state = select_state(cube, ticker, cutoff)
    if state["status"] != "available":
        return {**base, **missing(metric_id, state["status"], state["reason"])}
    annuals = state["annuals"]
    if fiscal_year is None:
        annual = annuals[-1]
    else:
        annual = next((item for item in annuals if item["fiscal_year"] == fiscal_year), None)
    if annual is None:
        future = fiscal_year > date.fromisoformat(cutoff).year or any(item["fiscal_year"] == fiscal_year for item in cube["companies"][ticker.upper()]["annuals"].values())
        return {**base, **missing(metric_id, "unavailable_as_of" if future else "unsupported", "该财政年度在截止日不可用。" if future else "财政年度不在已收录范围内。")}
    return {**base, **deepcopy(annual["metrics"][metric_id]), "fiscal_year": annual["fiscal_year"], "period_start": annual["period_start"] if cube["metric_catalog"][metric_id]["kind"] == "duration" else None, "period_end": annual["period_end"], "annual_id": annual["id"]}


def evidence_gate(cube: dict[str, Any], evidence_ids: list[str], cutoff: str, *, ticker: str | None = None) -> dict[str, Any]:
    """Check a citation set before a public answer is allowed to pass."""
    date.fromisoformat(cutoff)
    unknown, future, wrong_company = [], [], []
    for ref in dict.fromkeys(evidence_ids):
        row = cube["evidence"].get(ref)
        if row is None:
            unknown.append(ref)
        elif row["available_from"] > cutoff:
            future.append(ref)
        elif ticker and row["ticker"] != ticker.upper():
            wrong_company.append(ref)
    return {"passed": bool(evidence_ids) and not (unknown or future or wrong_company), "unknown_ids": unknown, "future_ids": future, "wrong_company_ids": wrong_company, "empty": not evidence_ids}
