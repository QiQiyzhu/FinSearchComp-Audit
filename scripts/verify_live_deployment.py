"""Verify the remote API without exposing tokens or charging for a run by default."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx


def verify(args: argparse.Namespace) -> dict:
    parsed = urlsplit(args.base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("base-url must be an origin, without credentials, query, fragment or path")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("remote deployments require HTTPS")
    token = os.getenv("FINAGENT_API_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    receipt: dict = {"checked_at": datetime.now(timezone.utc).isoformat(), "base_url": args.base_url.rstrip("/"), "paid_run_requested": args.submit, "checks": {}}
    with httpx.Client(base_url=receipt["base_url"], timeout=50, trust_env=False, follow_redirects=False) as client:
        health = None
        for attempt in range(3):
            try:
                health = client.get("/api/health")
                if health.status_code not in {502, 503, 504}:
                    break
            except httpx.TimeoutException:
                if attempt == 2:
                    raise
            if attempt < 2:
                time.sleep(3)
        if health is None:
            raise TimeoutError("service did not wake within the health verification window")
        health.raise_for_status()
        receipt["checks"]["health"] = health.json().get("status") == "ok"
        receipt["version"] = health.json().get("version")
        config = client.get("/api/config")
        config.raise_for_status()
        public = config.json()
        receipt["checks"]["deepseek_configured"] = public.get("features", {}).get("deepseek") is True
        openapi = client.get("/openapi.json")
        openapi.raise_for_status()
        receipt["checks"]["live_route"] = "/api/live/research" in openapi.json().get("paths", {})
        terminal = client.get("/terminal/")
        receipt["checks"]["terminal"] = terminal.status_code == 200 and "text/html" in terminal.headers.get("content-type", "")
        cors = client.options("/api/live/research", headers={"Origin": args.origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,idempotency-key"})
        receipt["checks"]["cors"] = cors.status_code == 200 and cors.headers.get("access-control-allow-origin") == args.origin
        if args.submit:
            if not all(receipt["checks"].values()):
                raise RuntimeError("preflight failed; no paid run submitted")
            body = {"question": args.question, "ticker": args.ticker, "as_of": args.as_of, "mode": "live"}
            response = client.post("/api/live/research", json=body, headers={**headers, "Idempotency-Key": f"deployment-smoke-{uuid.uuid4()}"})
            response.raise_for_status()
            if response.status_code != 202:
                raise RuntimeError("live endpoint did not accept an asynchronous job")
            identifier = response.json()["id"]
            receipt["job_id"] = identifier
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                response = client.get(f"/api/research/{identifier}", headers=headers)
                response.raise_for_status()
                job = response.json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(2)
            else:
                raise TimeoutError("job was accepted but did not complete within the verification window")
            receipt["checks"]["job_completed"] = job["status"] == "completed"
            receipt["job_status"] = job["status"]
            if job["status"] == "completed":
                result = job["result"]
                receipt["report_type"] = result.get("report_type")
                receipt["checks"]["live_report"] = result.get("report_type") == "live_research"
                receipt["source_count"] = len(result.get("sources", []))
                receipt["claim_count"] = len(result.get("claims", []))
                receipt["model_receipt_count"] = len(result.get("model_receipts", []))
                receipt["model_stages_completed"] = sorted({row.get("stage") for row in result.get("model_receipts", []) if row.get("status") == "completed" and row.get("stage")})
                receipt["checks"]["sources_present"] = receipt["source_count"] > 0
                receipt["checks"]["model_chain_completed"] = {"plan", "draft", "verify"}.issubset(receipt["model_stages_completed"])
                receipt["checks"]["verified_claim_present"] = any(claim.get("verification", {}).get("structural") is True and claim.get("verification", {}).get("entailment") == "supported" for claim in result.get("claims", []))
                receipt["report_sha256"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                events = client.get(f"/api/research/{identifier}/events", headers=headers)
                events.raise_for_status()
                receipt["event_count"] = len(events.json().get("events", []))
                receipt["checks"]["execution_trace"] = receipt["event_count"] > 0
                for extension in ("markdown", "json"):
                    export = client.get(f"/api/research/{identifier}/export", params={"format": extension}, headers=headers)
                    receipt["checks"][f"export_{extension}"] = export.status_code == 200 and len(export.content) > 100
    receipt["passed"] = all(receipt["checks"].values())
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--origin", default="https://qiqiyzhu.github.io")
    parser.add_argument("--output", type=Path, default=Path("build/verification/live-deployment.json"))
    parser.add_argument("--submit", action="store_true", help="Submit one real research job; incurs configured provider usage")
    parser.add_argument("--ticker", choices=["MSFT", "AAPL", "NVDA", "AMD", "AMZN", "GOOGL", "META", "TSLA"], default="MSFT")
    parser.add_argument("--question", default="检索微软最新官方年度财报，核验收入与经营现金流，列出来源日期和仍需核验的问题。")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--timeout", type=int, default=360)
    args = parser.parse_args()
    try:
        receipt = verify(args)
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError, TimeoutError) as exc:
        # Do not echo response bodies, input tokens or provider diagnostics.
        detail = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        print(f"Deployment verification failed: {detail}. No credentials were printed.", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
