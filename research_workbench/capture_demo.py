"""Refresh the bundled historical SEC excerpt (explicit maintainer action).

python -m research_workbench.capture_demo --user-agent "Project contact URL"
The capture date is NOT the point-in-time knowledge cutoff.
"""
from __future__ import annotations

import argparse
from datetime import date
import gzip
import hashlib
import json
import time

import httpx

from .sources import COMPANIES, DATA_DIR, DEMO_CUTOFF, DEMO_TICKERS, METRICS, canonical, digest, now, sec_url


def capture(user_agent: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bundle = {"schema_version": 1, "captured_at": now(), "cutoff": DEMO_CUTOFF,
              "note": "Historical admissibility filter applied to a later SEC API capture. Not a contemporaneously archived database. Exact upstream captures are in upstream/*.json.gz; sha256 hashes decompressed bytes.",
              "companies": {}}
    tags = {tag for metric in METRICS.values() for tag in metric["tags"]}
    with httpx.Client(timeout=45, trust_env=False, headers={"User-Agent": user_agent}) as client:
        for ticker in DEMO_TICKERS:
            response = client.get(sec_url(ticker))
            response.raise_for_status()
            raw = response.content
            payload = response.json()
            filtered = {}
            for tag, concept in payload["facts"]["us-gaap"].items():
                if tag not in tags:
                    continue
                values = []
                for row in concept.get("units", {}).get("USD", []):
                    try:
                        length = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                    except (KeyError, ValueError):
                        continue
                    if row.get("form") in {"10-K", "10-K/A"} and row["filed"] <= DEMO_CUTOFF and "2023-01-01" <= row["end"] <= DEMO_CUTOFF and 330 <= length <= 380:
                        values.append(row)
                if values:
                    filtered[tag] = {"label": concept.get("label", tag), "units": {"USD": values}}
            excerpt = {"cik": payload["cik"], "entityName": payload["entityName"], "facts": {"us-gaap": filtered}}
            bundle["companies"][ticker] = {"source_url": sec_url(ticker), "sha256": digest(excerpt), "upstream_sha256": hashlib.sha256(raw).hexdigest(), "payload": excerpt}
            upstream = DATA_DIR / "upstream"
            upstream.mkdir(exist_ok=True)
            (upstream / f"{ticker}.json.gz").write_bytes(gzip.compress(raw, mtime=0))
            print(f"Captured {ticker}: {sum(len(c['units']['USD']) for c in filtered.values())} annual fact records")
            time.sleep(1)
    (DATA_DIR / "demo_companyfacts.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-agent", required=True)
    capture(parser.parse_args().user_agent)
