"""Generate deterministic static demo reports without network or model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import EXAMPLES
from .config import Settings
from .workflows import WorkflowEngine
from .sources import DATA_DIR


def generate(output: Path) -> None:
    engine = WorkflowEngine(Settings())
    capture = json.loads((DATA_DIR / "demo_companyfacts.json").read_text(encoding="utf-8"))["captured_at"]
    reports = []
    for example in EXAMPLES:
        request = {key: example[key] for key in ("question", "ticker", "as_of", "mode")}
        if example.get("compare_with"):
            request["compare_with"] = example["compare_with"]
        report = engine.run(request)
        # Static artifact timestamps identify the underlying capture, not a
        # fictitious live run. Runtime jobs use actual timestamps.
        report["generated_at"] = capture
        report["static_replay"] = True
        report["timestamp_basis"] = "data_capture"
        report["trace_note"] = "预生成的静态工作流回放；步骤时间统一锚定数据抓取日期，不是实际运行时延或实测步骤时间。"
        for event in report["trace"]:
            event["timestamp"] = capture
            event["timestamp_basis"] = "data_capture"
        for child in report.get("companies", []):
            child["generated_at"] = capture
            child["static_replay"] = True
            for event in child["trace"]:
                event["timestamp"] = capture
                event["timestamp_basis"] = "data_capture"
        report["example_id"] = example["id"]
        reports.append(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema_version": 2, "examples": EXAMPLES, "reports": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Generated {len(reports)} audited offline reports: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "demo_reports.json")
    generate(parser.parse_args().output)
