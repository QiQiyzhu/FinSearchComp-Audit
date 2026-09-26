"""Explicit paid local acceptance run; saves actual online source/model receipts."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_workbench.config import Settings
from research_workbench.live_engine import LiveResearchEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="MSFT")
    parser.add_argument("--question", required=True)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an acceptance receipt")
    report = LiveResearchEngine(Settings.from_env()).run({"ticker": args.ticker, "question": args.question, "as_of": args.as_of, "mode": "live"},
        emit=lambda row: print(json.dumps(row, ensure_ascii=True), flush=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "claims": len(report["claims"]), "sources": len(report["sources"]),
                      "financial_answers": len(report["financial_answers"]), "receipts": report["model_receipts"]}))


if __name__ == "__main__":
    main()
