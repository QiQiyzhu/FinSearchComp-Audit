from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import tempfile
from typing import Any

from advanced_rag.models import QuerySpec

from .api import create_app
from .platform import FinAgentPlatform, RetryPolicy
from .store import RunStore


class _DemoAnswerService:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def answer(self, query: QuerySpec, *, request_id: str | None = None) -> dict[str, Any]:
        attempt = self.calls.get(query.query_id, 0) + 1
        self.calls[query.query_id] = attempt
        if query.query_id == "flaky" and attempt == 1:
            raise TimeoutError("injected timeout for reproducibility demo")
        if query.query_id == "missing":
            decision = {
                "action": "abstain",
                "answer_value": None,
                "unit": None,
                "selected_evidence_id": None,
                "rejection_reasons": ["检索未返回候选证据"],
                "trace": [
                    {"state": "PLAN"},
                    {"state": "RETRIEVE", "returned": 0},
                    {"state": "ABSTAIN", "reason": "insufficient_evidence"},
                ],
            }
        else:
            decision = {
                "action": "answer",
                "answer_value": "100",
                "unit": "usd_million",
                "selected_evidence_id": "doc-safe-v1",
                "rejection_reasons": [],
                "trace": [
                    {"state": "PLAN"},
                    {"state": "RETRIEVE", "returned": 3},
                    {"state": "AUDIT", "accepted": 1, "rejected": 2},
                    {"state": "ANSWER", "selected_evidence_id": "doc-safe-v1"},
                ],
            }
        return {
            "request_id": request_id,
            "cached": False,
            "latency_ms": 1.0,
            "decision": decision,
        }


def _query(query_id: str, question: str) -> QuerySpec:
    return QuerySpec(
        query_id=query_id,
        question=question,
        cutoff_date="2025-01-01",
        target_period="FY2024",
        required_version="final",
        canonical_unit="usd_million",
    )


def generate_demo(output_dir: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        platform = FinAgentPlatform(
            _DemoAnswerService(),
            RunStore(Path(directory) / "demo.sqlite3"),
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
        )
        try:
            queries = [
                _query("answerable", "Answerable filing question"),
                _query("missing", "Question with missing evidence"),
                _query("flaky", "Question recovering from an upstream timeout"),
            ]
            submitted = platform.submit_evaluation(queries, name="platform-demo")
            evaluation = platform.wait(submitted["run_id"])
            duplicate = platform.submit_evaluation(queries, name="platform-demo")
            children = platform.store.list_children(submitted["run_id"])
            answerable = next(child for child in children if child["request"]["query_id"] == "answerable")
            flaky = next(child for child in children if child["request"]["query_id"] == "flaky")
            replay = platform.wait(platform.replay(answerable["run_id"])["run_id"])
            analytics = platform.failure_analytics(submitted["run_id"])
            openapi = create_app(platform).openapi()

            id_map = {submitted["run_id"]: "evaluation-1"}
            for index, child in enumerate(children, start=1):
                id_map[child["run_id"]] = f"case-{index}"
            id_map[replay["run_id"]] = "replay-1"
            payload = {
                "schema_version": "finagent-platform-demo-1.0",
                "checks": {
                    "evaluation_completed": evaluation["status"] == "succeeded",
                    "idempotency_deduplicated": (
                        duplicate["deduplicated"]
                        and duplicate["run_id"] == submitted["run_id"]
                    ),
                    "timeout_recovered": flaky["attempts"] == 2,
                    "failure_classified": analytics["categories"] == {"empty_retrieval": 1},
                    "replay_matched": replay["result"]["replay_comparison"]["same_decision"],
                },
                "evaluation": _normalize(evaluation, id_map),
                "failure_analytics": _normalize(analytics, id_map),
                "flaky_trace": _normalize(flaky["trace"], id_map),
                "replay": _normalize(replay, id_map),
                "api_paths": sorted(openapi["paths"]),
            }
            payload["passed"] = all(payload["checks"].values())
            if not payload["passed"]:
                raise AssertionError(f"platform demo checks failed: {payload['checks']}")
        finally:
            platform.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "platform_demo.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "openapi.json").write_text(
        json.dumps(openapi, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    (output_dir / "index.html").write_text(_html(payload), encoding="utf-8")
    return payload


def _normalize(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(item, id_map)
            for key, item in value.items()
            if key
            not in {
                "created_at",
                "updated_at",
                "latency_ms",
                "platform_latency_ms",
                "run_key",
                "request_hash",
            }
        }
    if isinstance(value, list):
        return [_normalize(item, id_map) for item in value]
    if isinstance(value, str):
        return id_map.get(value, value)
    return value


def _markdown(payload: dict[str, Any]) -> str:
    result = payload["evaluation"]["result"]
    checks = "\n".join(
        f"| {name} | {'PASS' if passed else 'FAIL'} |"
        for name, passed in payload["checks"].items()
    )
    return f"""# FinAgent Audit Platform reproducibility report

| Metric | Result |
|---|---:|
| Evaluation cases | {result['total']} |
| Answered / Abstained | {result['answered']} / {result['abstained']} |
| Recovered timeout attempts | 2 |
| Classified failures | {payload['failure_analytics']['classified_cases']} |

| Check | Status |
|---|---|
{checks}

The demo is deterministic and uses an injected local answer service. A separate
integration test runs the real offline ATLAS-RAG pipeline through the same SQLite
job, trace, and replay layer.
"""


def _html(payload: dict[str, Any]) -> str:
    result = payload["evaluation"]["result"]
    flaky_events = payload["flaky_trace"]["execution_events"]
    event_rows = "".join(
        f"<tr><td>{index}</td><td>{html.escape(event['type'])}</td>"
        f"<td>{html.escape(str(event.get('error_type') or event.get('agent_action') or '—'))}</td>"
        f"<td>{html.escape(str(event.get('retrying', '—')))}</td></tr>"
        for index, event in enumerate(flaky_events, start=1)
    )
    api_items = "".join(f"<li><code>{html.escape(path)}</code></li>" for path in payload["api_paths"])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>FinAgent Audit Platform · Runs, Trace, Replay</title><style>
:root{{--ink:#172033;--muted:#697386;--line:#dce3ed;--blue:#3157d5;--cyan:#20aeb5;--green:#138a61;--bg:#f4f7fb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}.wrap{{width:min(1100px,calc(100% - 32px));margin:auto}}
header{{padding:68px 0 110px;background:radial-gradient(circle at 75% 10%,#294da9 0,transparent 35%),linear-gradient(135deg,#10172b,#19284d);color:white}}.eyebrow{{color:#7ee4e8;font-size:12px;font-weight:900;letter-spacing:.15em;text-transform:uppercase}}h1{{font-size:clamp(44px,7vw,74px);line-height:1.03;letter-spacing:-.05em;margin:18px 0}}header p{{max-width:780px;color:#cbd5ed;font-size:19px}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:-58px}}.card,.panel,.stage{{background:white;border:1px solid var(--line);border-radius:17px;box-shadow:0 14px 36px #17203312}}.card{{padding:22px}}.card b{{display:block;color:var(--blue);font-size:30px}}.card span{{color:var(--muted);font-size:13px}}main{{padding-bottom:70px}}h2{{font-size:32px;line-height:1.2;margin:56px 0 14px}}.copy{{color:var(--muted);max-width:830px}}
.flow{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.stage{{padding:18px;border-top:4px solid var(--cyan)}}.stage small{{color:var(--blue);font-weight:900}}.stage h3{{margin:6px 0}}.stage p{{margin:0;color:var(--muted);font-size:14px}}.grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:18px}}.panel{{padding:24px;overflow:auto}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{font-size:12px;color:var(--muted);text-transform:uppercase}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}
.failure b{{font-size:44px;color:var(--green)}}.failure ul,.api-list{{padding-left:20px}}.api-list code{{font-size:13px}}.boundary{{background:#fff8df;border:1px solid #e8cc78;border-radius:13px;padding:16px;margin-top:22px}}.links{{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}}.links a{{text-decoration:none;color:#2749ae;background:#e7eeff;padding:8px 12px;border-radius:9px;font-weight:750}}
@media(max-width:800px){{.metrics,.flow{{grid-template-columns:1fr 1fr}}.grid{{grid-template-columns:1fr}}}}@media(max-width:480px){{.metrics,.flow{{grid-template-columns:1fr}}}}
</style></head><body><header><div class="wrap"><span class="eyebrow">Persistent runs · Async jobs · Failure analytics</span>
<h1>FinAgent<br>Audit Platform</h1><p>把 ATLAS-RAG 从一次性实验函数升级为可排队、可查询、可诊断、可重放的平台。每个 Run 保存请求、配置版本、结果、失败标签和完整 Agent trace。</p></div></header>
<main class="wrap"><section class="metrics"><div class="card"><b>{result['total']}</b><span>批评测案例</span></div><div class="card"><b>{result['answered']} / {result['abstained']}</b><span>回答 / 拒答</span></div>
<div class="card"><b>2</b><span>超时后恢复尝试</span></div><div class="card"><b>PASS</b><span>确定性 Replay</span></div></section>
<h2>一个工程闭环，而不只是模型调用。</h2><section class="flow"><article class="stage"><small>01</small><h3>提交</h3><p>Query 或 Evaluation 立即返回 run_id。</p></article><article class="stage"><small>02</small><h3>幂等</h3><p>请求、数据集和Pipeline版本共同去重。</p></article>
<article class="stage"><small>03</small><h3>执行</h3><p>后台Worker有限重试，不占用HTTP连接。</p></article><article class="stage"><small>04</small><h3>诊断</h3><p>失败标签下钻到具体问题和trace。</p></article><article class="stage"><small>05</small><h3>回放</h3><p>新Run与旧Run比较答案、证据和轨迹。</p></article></section>
<h2>故障不会让整个 Batch 消失。</h2><div class="grid"><section class="panel"><h3>注入一次上游 Timeout</h3><table><thead><tr><th>#</th><th>Event</th><th>Outcome</th><th>Retrying</th></tr></thead><tbody>{event_rows}</tbody></table></section>
<aside class="panel failure"><h3>Failure Analytics</h3><b>1</b><p>缺失证据案例被分类为 <code>empty_retrieval</code>，而不是让模型编答案。</p><ul><li>Evaluation 仍成功完成</li><li>Case Run 可单独查看</li><li>失败可重放并进入回归集</li></ul></aside></div>
<h2>FastAPI 契约</h2><div class="panel"><ul class="api-list">{api_items}</ul></div>
<div class="boundary"><b>工程取舍：</b>当前规模使用 FastAPI + SQLite WAL + 进程内线程池。进程退出后的任务恢复需要数据库 lease/heartbeat worker；在达到多进程和多机需求前，不引入 Redis、Celery 或 Kafka。</div>
<div class="links"><a href="platform_demo.json">Demo JSON</a><a href="openapi.json">OpenAPI JSON</a><a href="README.md">复现报告</a><a href="../index.html">返回研究首页</a></div></main></body></html>"""


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Generate the offline platform demo")
    value.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "site" / "platform",
    )
    return value


def main() -> None:
    args = parser().parse_args()
    result = generate_demo(args.output)
    print(
        "FinAgent platform demo passed: "
        f"{result['evaluation']['result']['total']} cases, timeout recovered, replay matched"
    )


if __name__ == "__main__":
    main()
