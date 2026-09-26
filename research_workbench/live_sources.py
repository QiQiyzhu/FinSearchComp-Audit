"""Bounded, online-only search and reading of primary financial sources.

The free search path uses SEC submissions and EDGAR full-text search (EFTS).
EFTS is a public website endpoint rather than a versioned API: if it changes,
submissions discovery still works and the unavailable search is disclosed.
No search snippet is promoted to document evidence and no offline fixture is
used on failure. A day-only filing date becomes available the following day.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import threading
import time
from typing import Any, Callable
from urllib.parse import quote, urlencode, urljoin, urlsplit

import httpx

from .config import Settings
from .sources import COMPANIES, ResearchError, Source, digest, now, sec_url

MAX_BYTES = 8_000_000
MAX_TEXT = 18_000
MAX_REQUESTS = 22
MAX_SECONDS = 120
SEC_HOSTS = frozenset({"www.sec.gov", "sec.gov", "data.sec.gov", "efts.sec.gov"})
IR_HOSTS = {
    "MSFT": {"www.microsoft.com", "news.microsoft.com"},
    "AAPL": {"www.apple.com", "investor.apple.com"},
    "NVDA": {"nvidianews.nvidia.com", "investor.nvidia.com", "www.nvidia.com"},
    "GOOGL": {"abc.xyz", "www.abc.xyz", "blog.google"},
    "META": {"investor.atmeta.com", "about.fb.com"},
    "AMZN": {"ir.aboutamazon.com", "www.aboutamazon.com", "press.aboutamazon.com"},
    "TSLA": {"ir.tesla.com", "www.tesla.com"},
    "AMD": {"ir.amd.com", "www.amd.com"},
}
FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"})
_SEC_LOCK = threading.Lock()
_LAST_SEC_REQUEST = 0.0


def _public_addresses(host: str) -> list[str]:
    return [entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


def validate_url(url: str, allowed_hosts: set[str] | frozenset[str], resolver: Callable = _public_addresses) -> str:
    """Exact host allowlist plus public-DNS validation, including each redirect.

    The allowlist is administrator-owned; neither user input nor search results
    can add hosts. DNS is checked on each request. The HTTP client does not use
    environment proxies, cookies, credentials, or automatic redirects.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in {None, 443} or host not in allowed_hosts
                or "\\" in url or any(ord(c) < 32 for c in url)):
            raise ValueError("URL policy")
        addresses = resolver(host)
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("address policy")
    except (ValueError, OSError) as exc:
        raise ResearchError("SOURCE_URL_BLOCKED", "材料地址不在允许的官方公开来源内。") from exc
    return host


class _DocumentParser(HTMLParser):
    """Read visible text, preserving paragraph/table boundaries and metadata."""

    SKIP = {"script", "style", "noscript", "iframe", "svg", "head", "ix:hidden", "ix:header"}
    BREAK = {"p", "div", "section", "article", "tr", "table", "h1", "h2", "h3", "h4", "li", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta_dates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "meta" and attrs_dict.get("property", attrs_dict.get("name", "")).lower() in {
            "article:published_time", "datepublished", "date", "dc.date.issued", "pubdate",
        }:
            self.meta_dates.append(attrs_dict.get("content") or "")
        if tag == "time" and attrs_dict.get("datetime"):
            self.meta_dates.append(attrs_dict["datetime"] or "")
        if tag == "title":
            self.in_title = True
        if tag in self.SKIP:
            self.stack.append(tag)
        if not self.stack:
            if tag in self.BREAK:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if self.stack and tag == self.stack[-1]:
            self.stack.pop()
        if not self.stack and tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if not self.stack:
            self.parts.append(data)

    def result(self) -> dict[str, Any]:
        paragraphs = [re.sub(r"\s+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return {"text": "\n".join(line for line in paragraphs if line),
                "title": re.sub(r"\s+", " ", "".join(self.title_parts)).strip(),
                "dates": self.meta_dates}


def parse_document(raw: bytes, content_type: str = "text/html") -> dict[str, Any]:
    if not any(kind in content_type.lower() for kind in ("text/html", "application/xhtml", "text/plain", "application/xml", "text/xml")):
        raise ResearchError("SOURCE_FORMAT", "当前读取器仅支持官方 HTML、XML 和纯文本材料。")
    # SEC and the supported IR sites serve UTF-8; replacement characters remain
    # visible rather than inventing missing source content.
    decoded = raw.decode("utf-8", errors="replace")
    if "text/plain" in content_type and "<html" not in decoded[:500].lower():
        return {"text": decoded.strip(), "title": "", "dates": []}
    parser = _DocumentParser()
    parser.feed(decoded)
    return parser.result()


def select_passages(text: str, queries: list[str], limit: int = MAX_TEXT) -> tuple[str, bool]:
    """Select verbatim paragraph windows from a fully fetched document.

    This is deterministic lexical retrieval, not an LLM summary. Omission
    markers and text_truncated prevent a subset from looking like a full read.
    """
    if len(text) <= limit:
        return text, False
    stop = {"and", "or", "not", "the", "for", "with", "what", "latest", "microsoft", "apple", "nvidia", "alphabet", "amazon", "tesla", "meta"}
    stop |= {ticker.lower() for ticker in COMPANIES}
    term_groups = [set(term.lower() for term in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", query)) - stop for query in queries]
    # Split unusually long filing paragraphs to keep one table from consuming
    # the entire context budget. Every segment remains an exact substring.
    paragraphs = [line[start:start + 2400] for line in text.splitlines() for start in range(0, len(line), 2400)]
    rankings = []
    for terms in term_groups:
        patterns = [re.compile(r"\b" + re.escape(term) + r"\b", re.I) for term in terms]
        scores = []
        for index, paragraph in enumerate(paragraphs):
            occurrences = [len(pattern.findall(paragraph)) for pattern in patterns]
            # Reward distinct query terms, cap repetition, and gently normalize
            # length so huge boilerplate risk paragraphs do not dominate every
            # requested theme. Word boundaries keep AI from matching "said".
            score = sum((2 + min(2, count)) if count else 0 for count in occurrences) / (1 + len(paragraph) / 1800)
            if score:
                scores.append((score, index))
        rankings.append([index for _, index in sorted(scores, key=lambda row: (-row[0], row[1]))[:100]])
    chosen: set[int] = set()
    used = 0

    def add_window(indices: list[int]) -> bool:
        nonlocal used
        new = [index for index in indices if index not in chosen]
        # Two omission boundaries are sufficient for a contiguous window.
        needed = sum(len(paragraphs[index]) + 1 for index in new) + (80 if new else 0)
        if used + needed > limit:
            return False
        chosen.update(new)
        used += needed
        return True

    # Preserve document identity/period at the beginning when its paragraphs
    # fit. Then rotate across queries so a narrow capex question retains space
    # alongside repeated cloud terms.
    intro = []
    intro_size = 0
    for index, paragraph in enumerate(paragraphs):
        if intro_size + len(paragraph) > min(600, limit // 5):
            break
        intro.append(index)
        intro_size += len(paragraph) + 1
    add_window(intro)
    for rank in range(max((len(group) for group in rankings), default=0)):
        for group in rankings:
            if rank < len(group):
                center = group[rank]
                # Keep both neighbors or skip the window. Extracting only the
                # match could remove a preceding negation, period or qualifier.
                add_window(list(range(max(0, center - 1), min(len(paragraphs), center + 2))))
    # Fill remaining space with coherent leading windows, never an isolated
    # numeric row from a table or a clipped match without its qualification.
    for center in range(0, len(paragraphs), 3):
        add_window(list(range(center, min(len(paragraphs), center + 3))))
    output: list[str] = []
    previous = -1
    for index in sorted(chosen):
        if index != previous + 1:
            output.append("[... omitted source paragraphs ...]")
        output.append(paragraphs[index])
        previous = index
    if previous < len(paragraphs) - 1:
        output.append("[... omitted source paragraphs ...]")
    return "\n".join(output)[:limit], True


def _available_date(published: str) -> str:
    return (date.fromisoformat(published[:10]) + timedelta(days=1)).isoformat()


def _filing_url(cik: int, accession: str, document: str) -> str:
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:htm|html|txt|xml)", document, re.I):
        raise ResearchError("SEC_SCHEMA", "SEC 申报文档标识不符合预期格式。")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{quote(document)}"


class LiveSourceClient:
    """Per-research bounded HTTP client. Instantiate once for each job."""

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None,
                 resolver: Callable | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self.resolver = resolver or _public_addresses
        self.request_count = 0
        self.started_at = time.monotonic()

    def _request(self, url: str, *, hosts: set[str] | frozenset[str], body: dict | None = None) -> tuple[bytes, str, str]:
        global _LAST_SEC_REQUEST
        for redirect in range(4):
            host = validate_url(url, hosts, self.resolver)
            headers = {"User-Agent": self.settings.sec_user_agent if host in SEC_HOSTS else "FinAgent/4.0 research reader",
                       "Accept": "application/json,text/html,application/xhtml+xml,text/plain;q=0.9"}
            if host in SEC_HOSTS and not self.settings.sec_user_agent:
                raise ResearchError("SEC_IDENTITY_REQUIRED", "部署尚未配置 SEC 数据访问身份。")
            redirected = False
            for attempt in range(2):
                remaining = MAX_SECONDS - (time.monotonic() - self.started_at)
                if remaining <= 0:
                    raise ResearchError("SOURCE_TIME_BUDGET", "本次联网材料读取达到两分钟时间上限。")
                self.request_count += 1
                if self.request_count > MAX_REQUESTS:
                    raise ResearchError("SOURCE_REQUEST_BUDGET", "本次研究已达到外部材料请求上限。")
                if host in SEC_HOSTS:
                    with _SEC_LOCK:
                        delay = .25 - (time.monotonic() - _LAST_SEC_REQUEST)
                        if delay > 0:
                            time.sleep(delay)
                        _LAST_SEC_REQUEST = time.monotonic()
                try:
                    with httpx.Client(timeout=httpx.Timeout(min(22, remaining), connect=min(8, remaining)), trust_env=False,
                                      follow_redirects=False, transport=self.transport) as client:
                        with client.stream("POST" if body is not None else "GET", url, headers=headers, json=body) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                # Never forward a provider API key through a redirect.
                                if body is not None or "location" not in response.headers:
                                    raise ResearchError("SOURCE_REDIRECT", "材料服务返回了不允许的重定向。")
                                url = urljoin(url, response.headers["location"])
                                redirected = True
                                break
                            if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                                time.sleep(.4)
                                continue
                            if response.status_code != 200:
                                raise ResearchError("SOURCE_HTTP", f"官方材料读取失败（HTTP {response.status_code}）。")
                            declared_size = response.headers.get("content-length", "")
                            if declared_size.isdigit() and int(declared_size) > MAX_BYTES:
                                raise ResearchError("SOURCE_SIZE_LIMIT", "材料超过 8 MB 读取上限。")
                            raw = bytearray()
                            for chunk in response.iter_bytes():
                                if time.monotonic() - self.started_at > MAX_SECONDS:
                                    raise ResearchError("SOURCE_TIME_BUDGET", "本次联网材料读取达到两分钟时间上限。")
                                raw.extend(chunk)
                                if len(raw) > MAX_BYTES:
                                    raise ResearchError("SOURCE_SIZE_LIMIT", "材料超过 8 MB 读取上限。")
                            return bytes(raw), response.headers.get("content-type", "text/html"), str(response.url)
                except httpx.HTTPError as exc:
                    if attempt == 0:
                        continue
                    raise ResearchError("SOURCE_NETWORK", "无法联网读取官方材料；本次未使用离线数据替代。") from exc
            if not redirected:
                break
        raise ResearchError("SOURCE_REDIRECT", "材料重定向次数超过限制。")

    def _json(self, url: str, *, body: dict | None = None, hosts: set[str] | frozenset[str] = SEC_HOSTS) -> tuple[dict, bytes]:
        raw, _, _ = self._request(url, hosts=hosts, body=body)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("object expected")
            return payload, raw
        except (ValueError, UnicodeError) as exc:
            raise ResearchError("SOURCE_SCHEMA", "官方来源没有返回可识别的数据结构。") from exc

    def companyfacts(self, ticker: str) -> Source:
        self._company(ticker)
        payload, raw = self._json(sec_url(ticker))
        if payload.get("cik") != COMPANIES[ticker]["cik"] or not isinstance(payload.get("facts"), dict):
            raise ResearchError("SEC_SCHEMA", "实时 SEC 数据的公司身份或事实结构校验失败。")
        return Source(payload, sec_url(ticker), now(), digest(payload), "live", False, hashlib.sha256(raw).hexdigest())

    @staticmethod
    def _company(ticker: str) -> dict:
        if ticker not in COMPANIES:
            raise ResearchError("UNSUPPORTED_ISSUER", "当前联网研究支持终端列出的八家公司。")
        return COMPANIES[ticker]

    def _submissions(self, ticker: str, as_of: str, forms: set[str]) -> list[dict]:
        company = self._company(ticker)
        cik = company["cik"]
        payload, _ = self._json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        if str(payload.get("cik", "")).lstrip("0") != str(cik):
            raise ResearchError("SEC_SCHEMA", "SEC 申报列表公司身份不匹配。")
        history = [payload.get("filings", {}).get("recent", {})]
        # The recent feed contains >=1 year or 1,000 filings. Read at most two
        # matching archive pages for historical dates; never silently use newer.
        recent_dates = history[0].get("filingDate", [])
        if not recent_dates or min(recent_dates) > as_of:
            for entry in payload.get("filings", {}).get("files", [])[:20]:
                if len(history) >= 3:
                    break
                if entry.get("filingFrom", "9999") > as_of:
                    continue
                name = entry.get("name", "")
                if not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
                    continue
                page, _ = self._json(f"https://data.sec.gov/submissions/{name}")
                history.append(page)
        candidates: list[dict] = []
        for columns in history:
            for index, form in enumerate(columns.get("form", [])):
                if form not in forms:
                    continue
                try:
                    filed = columns["filingDate"][index]
                    available = _available_date(filed)
                    if available > as_of:
                        continue
                    accession = columns["accessionNumber"][index]
                    document = columns["primaryDocument"][index]
                    url = _filing_url(cik, accession, document)
                    candidates.append({"title": f"{company['name']} {form} · {filed}", "url": url,
                                       "published_at": filed, "available_from": available,
                                       "source_type": "sec_filing", "accession": accession, "form": form,
                                       "discovered_by": "sec_submissions"})
                except (KeyError, IndexError, TypeError, ValueError, ResearchError):
                    continue
        return sorted(candidates, key=lambda item: item["published_at"], reverse=True)

    def _efts(self, ticker: str, query: str, as_of: str, forms: set[str]) -> list[dict]:
        cik = self._company(ticker)["cik"]
        end = date.fromisoformat(as_of) - timedelta(days=1)
        start = max(date(2001, 1, 1), end - timedelta(days=1826))
        # EFTS expects root form filters. Supplying /A in this parameter can
        # return zero matches even when the corresponding originals match;
        # amendments remain eligible in the returned document metadata and the
        # submissions discovery path includes them explicitly.
        root_forms = {form.removesuffix("/A") for form in forms}
        params = {"q": query[:180], "ciks": f"{cik:010d}", "forms": ",".join(sorted(root_forms)),
                  "dateRange": "custom", "category": "custom", "startdt": start.isoformat(), "enddt": end.isoformat()}
        payload, _ = self._json("https://efts.sec.gov/LATEST/search-index?" + urlencode(params))
        if not isinstance(payload.get("hits", {}).get("hits"), list):
            raise ResearchError("SEC_SEARCH_SCHEMA", "SEC 全文检索接口结构已变更。")
        candidates = []
        for hit in payload["hits"]["hits"][:20]:
            source = hit.get("_source", {})
            try:
                if str(cik) not in {str(int(value)) for value in source.get("ciks", [])}:
                    continue
                filed = source["file_date"]
                available = _available_date(filed)
                if available > as_of or source.get("form") not in forms:
                    continue
                accession = source["adsh"]
                document = hit["_id"].split(":", 1)[1]
                candidates.append({"title": f"{COMPANIES[ticker]['name']} {source.get('file_type', source['form'])} · {filed}",
                                   "url": _filing_url(cik, accession, document), "published_at": filed,
                                   "available_from": available, "source_type": "sec_filing", "accession": accession,
                                   "form": source["form"], "discovered_by": "sec_efts", "query": query})
            except (KeyError, IndexError, TypeError, ValueError, ResearchError):
                continue
        # Prefer a recent matching disclosure over an old high keyword score.
        return sorted(candidates, key=lambda item: item["published_at"], reverse=True)

    def _tavily(self, ticker: str, query: str) -> list[dict]:
        payload, _ = self._json("https://api.tavily.com/search", hosts={"api.tavily.com"}, body={
            "api_key": self.settings.tavily_api_key, "query": f"{COMPANIES[ticker]['name']} {query[:180]}",
            "search_depth": "basic", "max_results": 3, "include_answer": False,
            "include_raw_content": False, "include_domains": sorted(IR_HOSTS[ticker]),
        })
        return [{"title": str(hit.get("title", "公司官方材料"))[:250], "url": hit.get("url", ""),
                 "source_type": "company_ir", "discovered_by": "tavily", "query": query}
                for hit in payload.get("results", [])[:3]]

    def search_and_read(self, ticker: str, queries: list[str], as_of: str, forms: list[str] | None = None,
                        max_documents: int = 4, *, include_financials: bool = True) -> dict[str, Any]:
        company = self._company(ticker)
        cutoff = date.fromisoformat(as_of)
        if cutoff > datetime.now(timezone.utc).date() or cutoff < date(2001, 1, 2):
            raise ResearchError("INVALID_CUTOFF", "联网研究截止日期必须位于 2001 年至今天之间。")
        queries = list(dict.fromkeys(str(query).strip()[:180] for query in queries if str(query).strip()))[:3]
        if not queries:
            queries = ["revenue OR risk"]
        forms_set = set(forms or ["10-K", "10-Q", "8-K"]) & FORMS
        if not forms_set:
            raise ResearchError("UNSUPPORTED_FORMS", "当前读取器支持 10-K、10-Q、8-K 及其修订。")
        forms_set |= {form + "/A" for form in forms_set if not form.endswith("/A")}
        max_documents = max(1, min(4, max_documents))
        searches: list[dict] = []
        gaps: list[dict] = []
        base: list[dict] = []
        search_matches: list[dict] = []
        try:
            base = self._submissions(ticker, as_of, forms_set)
            searches.append({"query": f"{ticker} {'/'.join(sorted(forms_set))} before {as_of}",
                             "provider": "sec_submissions", "result_count": len(base), "status": "ok"})
        except ResearchError as exc:
            gaps.append({"code": exc.code, "message": exc.message})
            searches.append({"query": ticker, "provider": "sec_submissions", "result_count": 0, "status": "failed"})
        for query in queries:
            try:
                hits = self._efts(ticker, query, as_of, forms_set)
                search_matches.extend(hits)
                searches.append({"query": query, "provider": "sec_efts", "result_count": len(hits), "status": "ok"})
            except ResearchError as exc:
                searches.append({"query": query, "provider": "sec_efts", "result_count": 0, "status": "failed"})
                gaps.append({"code": exc.code, "message": "SEC 关键词搜索不可用；仅使用本次成功取得的材料。"})
        # Reserve baseline quarterly + annual context, then keyword discoveries.
        anchors = [next((item for item in base if item["form"].removesuffix("/A") == form), None) for form in ("10-Q", "10-K")]
        candidates = [item for item in anchors if item] + search_matches + base[:8]
        if self.settings.tavily_api_key:
            try:
                hits = self._tavily(ticker, queries[0])
                # Give optional official IR search a chance within the same cap.
                candidates = candidates[:2] + hits[:1] + candidates[2:] + hits[1:]
                searches.append({"query": queries[0], "provider": "tavily_official_ir", "result_count": len(hits), "status": "ok"})
            except ResearchError as exc:
                searches.append({"query": queries[0], "provider": "tavily_official_ir", "result_count": 0, "status": "failed"})
                gaps.append({"code": exc.code, "message": "补充的公司官网搜索不可用。"})
        documents = []
        seen: set[str] = set()
        allowed_hosts = SEC_HOSTS | IR_HOSTS[ticker]
        # Reserve time for the structured financial input before potentially
        # large filing HTML downloads consume the shared network time budget.
        financial_source = None
        if include_financials:
            try:
                financial_source = self.companyfacts(ticker)
            except ResearchError as exc:
                gaps.append({"code": exc.code, "message": exc.message})
        for item in candidates:
            if len(documents) >= max_documents or len(seen) >= 8 or self.request_count >= MAX_REQUESTS - 2:
                break
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            try:
                raw, content_type, final_url = self._request(item["url"], hosts=allowed_hosts)
                parsed = parse_document(raw, content_type)
                if len(parsed["text"]) < 100:
                    raise ResearchError("SOURCE_EMPTY", "材料正文过短，未作为研究证据使用。")
                if not item.get("published_at"):
                    dates = sorted({value[:10] for value in parsed["dates"] if re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$| )", value)})
                    if len(dates) != 1:
                        raise ResearchError("SOURCE_DATE_UNKNOWN", "公司网页缺少唯一可核验的发布日期，未纳入历史证据。")
                    item = {**item, "published_at": dates[0], "available_from": _available_date(dates[0])}
                if item["available_from"] > as_of:
                    raise ResearchError("SOURCE_AFTER_CUTOFF", "材料在截止日之后才可用，已排除。")
                selected, truncated = select_passages(parsed["text"], queries)
                raw_hash = hashlib.sha256(raw).hexdigest()
                documents.append({**item, "id": f"DOC_{len(documents) + 1}", "url": final_url,
                                  "title": item.get("title") or parsed["title"] or company["name"],
                                  "retrieved_at": now(), "sha256": raw_hash,
                                  "text_sha256": hashlib.sha256(selected.encode("utf-8")).hexdigest(),
                                  "text": selected, "snippet": selected[:400], "status": "read",
                                  "text_truncated": truncated, "original_text_chars": len(parsed["text"]),
                                  "bytes_fetched": len(raw), "date_policy": "published_date_plus_one_day"})
            except (ResearchError, ValueError) as exc:
                gaps.append({"code": exc.code if isinstance(exc, ResearchError) else "SOURCE_DATE_INVALID",
                             "message": exc.message if isinstance(exc, ResearchError) else "材料日期格式不可核验。",
                             "url": item["url"] if urlsplit(item["url"]).hostname in allowed_hosts else ""})
        if not documents:
            gaps.append({"code": "NO_READ_DOCUMENTS", "message": "本次未取得截止日前可读取的官方正文；搜索结果摘要没有作为证据。"})
        return {"documents": documents, "searches": searches, "gaps": gaps,
                "financial_source": financial_source, "network_requests": self.request_count,
                "retrieved_at": now(), "date_policy": "published_date_plus_one_day",
                "scope": "SEC filings and optional company official IR; no broad news coverage"}
