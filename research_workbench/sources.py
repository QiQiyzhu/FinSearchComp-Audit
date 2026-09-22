from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any

import httpx

from .config import Settings

COMPANIES = {
    "MSFT": {"cik": 789019, "name": "Microsoft", "aliases": ["microsoft", "微软"]},
    "AAPL": {"cik": 320193, "name": "Apple", "aliases": ["apple", "苹果"]},
    "NVDA": {"cik": 1045810, "name": "NVIDIA", "aliases": ["nvidia", "英伟达"]},
    "GOOGL": {"cik": 1652044, "name": "Alphabet", "aliases": ["alphabet", "google", "谷歌"]},
    "META": {"cik": 1326801, "name": "Meta", "aliases": ["facebook", "脸书"]},
    "AMZN": {"cik": 1018724, "name": "Amazon", "aliases": ["amazon", "亚马逊"]},
    "TSLA": {"cik": 1318605, "name": "Tesla", "aliases": ["tesla", "特斯拉"]},
    "AMD": {"cik": 2488, "name": "AMD", "aliases": ["超威半导体"]},
}

METRICS = {
    "revenue": {"label": "营业收入", "tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]},
    "operating_income": {"label": "营业利润", "tags": ["OperatingIncomeLoss"]},
    "operating_cash_flow": {"label": "经营现金流", "tags": ["NetCashProvidedByUsedInOperatingActivities"]},
    # Do not substitute combined PP&E + intangible-acquisition tags.
    "capital_expenditure": {"label": "PP&E 现金资本支出", "tags": ["PaymentsToAcquirePropertyPlantAndEquipment"]},
    "net_income": {"label": "净利润", "tags": ["NetIncomeLoss"]},
    "research_and_development": {"label": "研发费用", "tags": ["ResearchAndDevelopmentExpense"]},
}
DEMO_CUTOFF = "2024-11-01"
DEMO_TICKERS = ["MSFT", "AAPL", "NVDA"]
DATA_DIR = Path(__file__).parent / "data"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class ResearchError(Exception):
    """Public-safe error. Never put provider response bodies or secrets here."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def sec_url(ticker: str) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{COMPANIES[ticker]['cik']:010d}.json"


@dataclass(frozen=True)
class Source:
    payload: dict[str, Any]
    url: str
    retrieved_at: str
    sha256: str
    data_mode: str
    cache_hit: bool = False
    upstream_sha256: str = ""


class SourceClient:
    """Fixed-origin SEC adapter; at most one upstream request per second/process."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._last_request = 0.0

    def snapshot(self, ticker: str) -> Source:
        archive = json.loads((DATA_DIR / "demo_companyfacts.json").read_text(encoding="utf-8"))
        record = archive["companies"].get(ticker)
        if record is None:
            raise ResearchError("SNAPSHOT_UNAVAILABLE", "该公司的离线快照尚未收录，请选择 Microsoft、Apple 或 NVIDIA，或切换实时检索。")
        if digest(record["payload"]) != record["sha256"]:
            raise ResearchError("SNAPSHOT_INTEGRITY", "离线证据的完整性校验失败。")
        return Source(record["payload"], record["source_url"], archive["captured_at"], record["sha256"], "snapshot", upstream_sha256=record["upstream_sha256"])

    def live(self, ticker: str) -> Source:
        cache = self.settings.cache_dir / f"sec-{ticker}.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < 900:
            try:
                stored = json.loads(cache.read_text(encoding="utf-8"))
                if digest(stored["payload"]) == stored["sha256"]:
                    return Source(stored["payload"], sec_url(ticker), stored["retrieved_at"], stored["sha256"], "live", True, stored["upstream_sha256"])
            except (OSError, ValueError, KeyError):
                pass
        with self._lock:
            delay = 1.0 - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()
            headers = {"User-Agent": self.settings.sec_user_agent, "Accept": "application/json"}
            try:
                with httpx.Client(timeout=httpx.Timeout(35, connect=10), trust_env=self.settings.trust_env, follow_redirects=False) as client:
                    raw = bytearray()
                    with client.stream("GET", sec_url(ticker), headers=headers) as response:
                        if response.status_code != 200:
                            raise ResearchError("SEC_UNAVAILABLE", f"SEC 数据请求未成功（HTTP {response.status_code}）。请稍后重试或明确选择离线快照。")
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 30_000_000:
                                raise ResearchError("SEC_RESPONSE_LIMIT", "SEC 数据响应超过此部署的大小限制。")
                payload = json.loads(raw)
                if payload.get("cik") != COMPANIES[ticker]["cik"] or not isinstance(payload.get("facts"), dict):
                    raise ResearchError("SEC_SCHEMA", "SEC 响应公司身份或结构校验失败。")
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                raise ResearchError("SEC_UNAVAILABLE", "无法读取 SEC 官方数据；本次实时研究未使用离线数据替代。") from exc
        stored = {"payload": payload, "sha256": digest(payload), "upstream_sha256": hashlib.sha256(raw).hexdigest(), "retrieved_at": now()}
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix(f".{threading.get_ident()}.tmp")
        temp.write_text(canonical(stored), encoding="utf-8")
        temp.replace(cache)
        return Source(payload, sec_url(ticker), stored["retrieved_at"], stored["sha256"], "live", upstream_sha256=stored["upstream_sha256"])


def annual_candidates(payload: dict[str, Any], metric: str, as_of: str) -> list[dict[str, Any]]:
    """Admit only annual USD facts actually filed by the requested cutoff.

    Keep all supported tags until selecting the period: older preferred tags
    must not hide a newer filing on a replacement tag. Reject conflicts within
    the latest eligible filing rather than silently picking a row.
    """
    cutoff = date.fromisoformat(as_of)
    candidates = []
    taxonomy = payload.get("facts", {}).get("us-gaap", {})
    for rank, tag in enumerate(METRICS[metric]["tags"]):
        for row in taxonomy.get(tag, {}).get("units", {}).get("USD", []):
            try:
                start, end, filed = (date.fromisoformat(row[key]) for key in ("start", "end", "filed"))
                value = Decimal(str(row["val"]))
            except (ValueError, TypeError, KeyError, InvalidOperation):
                continue
            if row.get("form") not in {"10-K", "10-K/A"} or not (330 <= (end - start).days <= 380):
                continue
            if not (start < end <= filed <= cutoff) or not value.is_finite():
                continue
            if metric in {"revenue", "capital_expenditure", "research_and_development"} and value < 0:
                continue
            candidates.append({**row, "value": format(value, "f"), "tag": tag, "tag_rank": rank})
    return candidates


def choose_fact(candidates: list[dict[str, Any]], period: tuple[str, str]) -> dict[str, Any] | None:
    matching = [row for row in candidates if (row["start"], row["end"]) == period]
    if not matching:
        return None
    latest = max(row["filed"] for row in matching)
    latest_rows = [row for row in matching if row["filed"] == latest]
    if len({row["value"] for row in latest_rows}) > 1:
        return None
    return sorted(latest_rows, key=lambda row: (row["tag_rank"], row.get("accn", "")))[0]


def select_facts(source: Source, ticker: str, as_of: str, fiscal_year: int | None = None) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    candidates = {metric: annual_candidates(source.payload, metric, as_of) for metric in METRICS}
    revenue = candidates["revenue"]
    if fiscal_year is not None:
        revenue = [row for row in revenue if date.fromisoformat(row["end"]).year == fiscal_year]
    if not revenue:
        return {}, [], list(METRICS)
    current_end = max(row["end"] for row in revenue)
    current_start = max(row["start"] for row in revenue if row["end"] == current_end)
    current_period = (current_start, current_end)
    previous = [row for row in candidates["revenue"] if 330 <= (date.fromisoformat(current_end) - date.fromisoformat(row["end"])).days <= 380 and row["end"] < current_start]
    prior_period = None
    if previous:
        previous_end = max(row["end"] for row in previous)
        prior_period = (max(row["start"] for row in previous if row["end"] == previous_end), previous_end)
    facts, evidence, missing = {}, [], []
    for suffix, period in [("", current_period), ("_prior", prior_period)]:
        if period is None:
            continue
        for metric in METRICS:
            selected = choose_fact(candidates[metric], period)
            if selected is None:
                missing.append(metric + suffix)
                continue
            identifier = f"E{len(evidence) + 1:02d}"
            accession = str(selected.get("accn", ""))
            cik = COMPANIES[ticker]["cik"]
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm" if accession else source.url
            title = f"{ticker} · {selected['form']} · {METRICS[metric]['label']} · {selected['end']}"
            excerpt = f"us-gaap:{selected['tag']} = {selected['value']} USD; period {selected['start']} to {selected['end']}; filed {selected['filed']}; accession {accession}."
            item = {
                "id": identifier, "title": title, "url": filing_url, "source_url": source.url,
                "source_type": "sec_companyfacts", "published_at": selected["filed"],
                "period_start": selected["start"], "period_end": selected["end"], "retrieved_at": source.retrieved_at,
                "sha256": source.sha256, "upstream_sha256": source.upstream_sha256,
                "fact_sha256": digest({key: value for key, value in selected.items() if key not in {"tag_rank"}}),
                "excerpt": excerpt, "metric": metric, "value": selected["value"], "unit": "USD",
                "taxonomy_tag": selected["tag"], "accession": accession, "data_mode": source.data_mode,
            }
            evidence.append(item)
            facts[metric + suffix] = {**selected, "evidence_id": identifier}
    return facts, evidence, missing
