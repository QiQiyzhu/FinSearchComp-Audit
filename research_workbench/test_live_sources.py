"""No-network acceptance checks for online-only source retrieval."""
from dataclasses import replace
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

import httpx

from research_workbench.config import Settings
from research_workbench.live_sources import (
    MAX_BYTES, SEC_HOSTS, LiveSourceClient, parse_document, select_passages, validate_url,
)
from research_workbench.sources import ResearchError


CIK = 789019
ACC = "0000789019-26-000101"
FILED = "2026-07-29"
DOC = "msft-20260630.htm"
HTML = "<html><head><title>Microsoft filing</title><script>secret bad instructions</script></head><body><h1>Microsoft annual report</h1><p>Cloud revenue increased with Azure demand. This paragraph is official primary document text and not a search result snippet.</p><table><tr><td>Revenue</td><td>123</td></tr></table></body></html>"


def submission(*, filed=FILED, form="10-K", document=DOC, accession=ACC):
    return {"cik": str(CIK), "filings": {"recent": {"filingDate": [filed], "form": [form],
            "accessionNumber": [accession], "primaryDocument": [document]}, "files": []}}


def hit(*, filed=FILED, cik=CIK, document=DOC):
    return {"_id": f"{ACC}:{document}", "_source": {"file_date": filed, "ciks": [str(cik)],
            "adsh": ACC, "form": "10-K", "file_type": "10-K"}}


class LiveSourcesTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(sec_user_agent="test-reader test@example.invalid")
        self.resolve = lambda host: ["93.184.216.34"]
        self.sleeps = patch("research_workbench.live_sources.time.sleep")
        self.sleeps.start()
        self.addCleanup(self.sleeps.stop)
        self.calls = []

    def client(self, handler):
        def tracked(request):
            self.calls.append(request)
            return handler(request)
        return LiveSourceClient(self.settings, transport=httpx.MockTransport(tracked), resolver=self.resolve)

    def handler(self, request):
        if request.url.host == "data.sec.gov" and "/submissions/" in request.url.path:
            return httpx.Response(200, json=submission())
        if request.url.host == "efts.sec.gov":
            return httpx.Response(200, json={"hits": {"hits": [hit()]}})
        if "/companyfacts/" in request.url.path:
            return httpx.Response(200, json={"cik": CIK, "facts": {"us-gaap": {}}})
        if "/Archives/" in request.url.path:
            return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        raise AssertionError(str(request.url))

    def test_actual_search_read_and_companyfacts_code_path(self):
        result = self.client(self.handler).search_and_read("MSFT", ["cloud OR Azure"], "2026-09-26")
        self.assertEqual(len(result["documents"]), 1)
        document = result["documents"][0]
        self.assertEqual(document["status"], "read")
        self.assertIn("Cloud revenue increased", document["text"])
        self.assertEqual(document["available_from"], "2026-07-30")
        self.assertEqual(len(document["sha256"]), 64)
        self.assertEqual(result["financial_source"].data_mode, "live")
        self.assertEqual(result["searches"][1]["provider"], "sec_efts")
        self.assertTrue(any("q=cloud" in str(request.url) for request in self.calls))
        self.assertTrue(all(request.method == "GET" for request in self.calls))

    def test_same_day_and_future_filings_are_excluded_before_fetch(self):
        def handler(request):
            if request.url.host == "data.sec.gov":
                return httpx.Response(200, json=submission(filed="2026-09-26"))
            return httpx.Response(200, json={"hits": {"hits": [hit(filed="2026-09-26"), hit(filed="2026-09-27")]}})
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2026-09-26", include_financials=False)
        self.assertFalse(result["documents"])
        self.assertFalse(any("/Archives/" in request.url.path for request in self.calls))

    def test_next_day_filing_is_admitted(self):
        result = self.client(self.handler).search_and_read("MSFT", ["cloud"], "2026-07-30", include_financials=False)
        self.assertEqual(len(result["documents"]), 1)

    def test_efts_uses_root_forms_even_when_amendments_are_eligible(self):
        self.client(self.handler).search_and_read("MSFT", ["cloud"], "2026-09-26", include_financials=False)
        query = next(request for request in self.calls if request.url.host == "efts.sec.gov")
        self.assertEqual(query.url.params["forms"], "10-K,10-Q,8-K")

    def test_wrong_issuer_search_hit_is_rejected(self):
        def handler(request):
            if request.url.host == "data.sec.gov":
                return httpx.Response(200, json={"cik": CIK, "filings": {"recent": {}, "files": []}})
            return httpx.Response(200, json={"hits": {"hits": [hit(cik=320193)]}})
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2026-09-26", include_financials=False)
        self.assertFalse(result["documents"])

    def test_search_failure_uses_live_submissions_but_discloses_gap(self):
        def handler(request):
            if request.url.host == "efts.sec.gov":
                return httpx.Response(503)
            return self.handler(request)
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2026-09-26", include_financials=False)
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["searches"][1]["status"], "failed")
        self.assertTrue(result["gaps"])

    def test_network_failure_never_falls_back_to_snapshot(self):
        def handler(request):
            raise httpx.ConnectError("blocked", request=request)
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2026-09-26")
        self.assertFalse(result["documents"])
        self.assertIsNone(result["financial_source"])
        self.assertIn("SOURCE_NETWORK", {gap["code"] for gap in result["gaps"]})

    def test_search_snippets_are_not_read_evidence(self):
        def handler(request):
            if "/Archives/" in request.url.path:
                return httpx.Response(403)
            response = self.handler(request)
            return response
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2026-09-26", include_financials=False)
        self.assertFalse(result["documents"])
        self.assertGreater(result["searches"][1]["result_count"], 0)

    def test_private_dns_is_blocked(self):
        for address in ("127.0.0.1", "10.0.0.1", "192.168.0.2", "169.254.169.254", "::1", "fc00::1"):
            with self.subTest(address=address), self.assertRaises(ResearchError):
                validate_url("https://data.sec.gov/a", SEC_HOSTS, lambda host: [address])

    def test_mixed_public_private_dns_is_blocked(self):
        with self.assertRaises(ResearchError):
            validate_url("https://data.sec.gov/a", SEC_HOSTS, lambda host: ["93.184.216.34", "127.0.0.1"])

    def test_untrusted_scheme_hostname_credentials_port_are_blocked(self):
        for url in ("http://data.sec.gov/a", "file:///secret", "https://data.sec.gov.evil.example/a",
                    "https://data.sec.gov@evil.example/a", "https://user:secret@data.sec.gov/a",
                    "https://data.sec.gov:8443/a", "https://127.0.0.1/a", "https://data.sec.gov\\@evil.example/a"):
            with self.subTest(url=url), self.assertRaises(ResearchError):
                validate_url(url, SEC_HOSTS, self.resolve)

    def test_redirect_escape_is_not_requested(self):
        client = self.client(lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"}))
        with self.assertRaises(ResearchError) as raised:
            client._request("https://www.sec.gov/document", hosts=SEC_HOSTS)
        self.assertEqual(raised.exception.code, "SOURCE_URL_BLOCKED")
        self.assertEqual(len(self.calls), 1)

    def test_same_host_redirect_is_validated_and_followed(self):
        client = self.client(lambda request: httpx.Response(302, headers={"location": "/final"}) if request.url.path == "/start" else httpx.Response(200, text=HTML))
        raw, _, url = client._request("https://www.sec.gov/start", hosts=SEC_HOSTS)
        self.assertIn(b"Microsoft", raw)
        self.assertEqual(url, "https://www.sec.gov/final")

    def test_provider_secret_is_never_redirected(self):
        client = self.client(lambda request: httpx.Response(307, headers={"location": "https://www.sec.gov/a"}))
        with self.assertRaises(ResearchError):
            client._request("https://api.tavily.com/search", hosts={"api.tavily.com"}, body={"api_key": "test-only"})
        self.assertEqual(len(self.calls), 1)

    def test_byte_budget_applies_to_decoded_response(self):
        client = self.client(lambda request: httpx.Response(200, content=b"x" * (MAX_BYTES + 1)))
        with self.assertRaises(ResearchError) as raised:
            client._request("https://www.sec.gov/a", hosts=SEC_HOSTS)
        self.assertEqual(raised.exception.code, "SOURCE_SIZE_LIMIT")

    def test_large_official_filing_under_twenty_mb_is_allowed(self):
        payload = b"x" * 9_000_000
        client = self.client(lambda request: httpx.Response(200, content=payload))
        raw, _, _ = client._request("https://www.sec.gov/filing.htm", hosts=SEC_HOSTS)
        self.assertEqual(len(raw), len(payload))

    def test_script_style_ixhidden_do_not_enter_visible_text(self):
        parsed = parse_document(b'<html><head><title>Title</title><style>secret</style></head><body><ix:header><ix:hidden>hidden fact</ix:hidden></ix:header><p>Shown <b>value</b>.</p><script>bad instruction</script><table><tr><td>A</td><td>42</td></tr></table></body></html>')
        self.assertEqual(parsed["title"], "Title")
        self.assertIn("Shown value.", parsed["text"])
        self.assertIn("A | 42", parsed["text"])
        for hidden in ("secret", "hidden fact", "bad instruction"):
            self.assertNotIn(hidden, parsed["text"])

    def test_passage_selection_preserves_actual_text_and_late_matches(self):
        text = "\n".join([f"Unrelated paragraph {index} " + "a " * 60 for index in range(500)]) + "\nAzure cloud grew rapidly with major enterprise demand."
        selected, truncated = select_passages(text, ["Azure cloud"], 1200)
        self.assertTrue(truncated)
        self.assertLessEqual(len(selected), 1200)
        self.assertIn("Azure cloud grew rapidly", selected)
        self.assertIn("omitted source paragraphs", selected)

    def test_passage_keeps_preceding_negation_and_following_qualifier(self):
        padding = "\n".join("Unrelated filler " + "word " * 25 for _ in range(100))
        text = padding + "\nThis is a hypothetical scenario, not an achieved result.\nAzure revenue would rise under this assumption.\nActual results may differ substantially.\n" + padding
        selected, _ = select_passages(text, ["Azure revenue"], 1200)
        self.assertIn("hypothetical scenario, not an achieved result", selected)
        self.assertIn("Azure revenue would rise", selected)
        self.assertIn("Actual results may differ", selected)

    def test_passage_rotates_across_queries_instead_of_only_repeated_topic(self):
        text = "\n".join("Cloud Azure revenue " * 12 + f"topic {index}" for index in range(80))
        text += "\nCapital expenditure context for this period.\nCapital expenditures are increasing to support datacenter infrastructure.\nThis is the disclosed investment discussion."
        selected, _ = select_passages(text, ["cloud OR Azure", "capital expenditures"], 3000)
        self.assertIn("Cloud Azure revenue", selected)
        self.assertIn("Capital expenditures are increasing", selected)
        self.assertIn("Capital expenditure context for this period", selected)

    def test_companyfacts_identity_mismatch_is_rejected(self):
        client = self.client(lambda request: httpx.Response(200, json={"cik": 320193, "facts": {}}))
        with self.assertRaises(ResearchError) as raised:
            client.companyfacts("MSFT")
        self.assertEqual(raised.exception.code, "SEC_SCHEMA")

    def test_unknown_pdf_format_is_not_text(self):
        with self.assertRaises(ResearchError):
            parse_document(b"%PDF-1.7", "application/pdf")

    def test_future_cutoff_and_unsupported_issuer_reject_before_network(self):
        client = self.client(self.handler)
        with self.assertRaises(ResearchError):
            client.search_and_read("MSFT", ["cloud"], (date.today() + timedelta(days=2)).isoformat())
        with self.assertRaises(ResearchError):
            client.search_and_read("BOGUS", ["cloud"], "2026-09-26")
        self.assertFalse(self.calls)

    def test_unknown_ir_date_is_excluded_even_with_search_provider_date(self):
        self.settings = replace(self.settings, tavily_api_key="test-key")
        def handler(request):
            if request.url.host == "api.tavily.com":
                return httpx.Response(200, json={"results": [{"url": "https://www.microsoft.com/investor/news", "title": "IR", "published_date": "2026-07-01"}]})
            if request.url.host == "www.microsoft.com":
                return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
            return self.handler(request)
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2026-09-26", include_financials=False)
        self.assertEqual(len(result["documents"]), 1)
        self.assertIn("SOURCE_DATE_UNKNOWN", {gap["code"] for gap in result["gaps"]})

    def test_future_ir_metadata_excluded(self):
        self.settings = replace(self.settings, tavily_api_key="test-key")
        def handler(request):
            if request.url.host == "api.tavily.com":
                return httpx.Response(200, json={"results": [{"url": "https://www.microsoft.com/investor/news", "title": "IR"}]})
            if request.url.host == "www.microsoft.com":
                return httpx.Response(200, text=HTML.replace("<head>", '<head><meta property="article:published_time" content="2026-09-27T12:00:00Z">'), headers={"content-type": "text/html"})
            return self.handler(request)
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2026-09-26", include_financials=False)
        self.assertIn("SOURCE_AFTER_CUTOFF", {gap["code"] for gap in result["gaps"]})

    def test_historical_archive_is_fetched_and_not_latest_substituted(self):
        def handler(request):
            if "/submissions/" in request.url.path:
                if "-submissions-" in request.url.path:
                    return httpx.Response(200, json=submission(filed="2020-07-30")["filings"]["recent"])
                data = submission()
                data["filings"]["files"] = [{"name": "CIK0000789019-submissions-001.json", "filingFrom": "2018-01-01", "filingTo": "2021-01-01"}]
                return httpx.Response(200, json=data)
            if request.url.host == "efts.sec.gov":
                return httpx.Response(200, json={"hits": {"hits": []}})
            return self.handler(request)
        result = self.client(handler).search_and_read("MSFT", ["cloud"], "2020-08-01", include_financials=False)
        self.assertEqual(result["documents"][0]["published_at"], "2020-07-30")
        self.assertTrue(any("-submissions-001.json" in request.url.path for request in self.calls))


if __name__ == "__main__":
    unittest.main()
