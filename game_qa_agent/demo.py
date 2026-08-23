from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from .workflow import GameQAWorkflow


PACKAGE_DIR = Path(__file__).parent
DEFAULT_SCENARIOS = PACKAGE_DIR / "scenarios.json"


def load_scenario(scenario_id: str, path: Path = DEFAULT_SCENARIOS) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "game-qa-scenarios-1.0":
        raise ValueError("unsupported scenario schema")
    for scenario in payload.get("scenarios", []):
        if scenario.get("id") == scenario_id:
            return scenario
    raise ValueError(f"unknown scenario: {scenario_id}")


def run_demo(output_dir: Path, *, scenario_id: str = "playable-seed-7") -> dict[str, Any]:
    scenario = load_scenario(scenario_id)
    regression = scenario.get("regression_move")
    if not regression:
        raise ValueError(f"scenario has no regression move: {scenario_id}")

    workflow = GameQAWorkflow(
        scenario["board"],
        seed=int(scenario["seed"]),
        budget=12,
    )
    outputs = workflow.run_plan(
        [
            {"skill": "inspect_board"},
            {"skill": "build_qa_report"},
            {
                "skill": "apply_swap",
                "arguments": {
                    "first": regression["first"],
                    "second": regression["second"],
                    "seed": scenario["seed"],
                },
            },
            {"skill": "verify_replay"},
        ]
    )
    inspection, report, move, replay = outputs
    checks = {
        "scenario_expectations": all(
            inspection.get(key) == value
            for key, value in scenario["expected"].items()
        ),
        "regression_move": all(
            move.get(key) == regression[f"expected_{key}"]
            for key in ("cascades", "cleared_total", "score")
        ),
        "deterministic_replay": replay["verified"],
        "no_simulation_failures": not report["simulation_failures"],
    }
    payload = {
        "schema_version": "game-qa-demo-1.0",
        "scenario": scenario,
        "checks": checks,
        "passed": all(checks.values()),
        "initial_report": report,
        "applied_move": move,
        "replay": replay,
        "skill_catalog": workflow.skill_catalog(),
    }
    if not payload["passed"]:
        raise AssertionError(f"game QA demo failed: {checks}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace = workflow.export_trace()
    (output_dir / "trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        _render_summary(payload),
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(
        _render_html(payload, trace),
        encoding="utf-8",
    )
    return payload


def _render_summary(payload: dict[str, Any]) -> str:
    scenario = payload["scenario"]
    analysis = payload["initial_report"]["analysis"]
    move = payload["applied_move"]
    checks = payload["checks"]
    check_rows = "\n".join(
        f"| {name} | {'PASS' if passed else 'FAIL'} |"
        for name, passed in checks.items()
    )
    return f"""# Match-3 Agent QA reproducibility report

This report is generated offline from `{scenario['id']}`. The Agent chooses only
allow-listed skills; the deterministic rules engine owns legality, cascade, score,
and replay verification.

| Metric | Result |
|---|---:|
| Existing matched cells | {analysis['existing_match_cells']} |
| Legal moves | {analysis['legal_move_count']} |
| Difficulty proxy (triage only) | {analysis['difficulty_proxy']} |
| Regression cascades | {move['cascades']} |
| Regression cleared cells | {move['cleared_total']} |
| Regression score | {move['score']} |

| Reproducibility check | Status |
|---|---|
{check_rows}

The difficulty proxy is intentionally not presented as player difficulty. It is a
transparent mobility/balance heuristic for QA triage and must be calibrated with
telemetry or play-test data before product use.
"""


def _render_html(payload: dict[str, Any], trace: dict[str, Any]) -> str:
    analysis = payload["initial_report"]["analysis"]
    move = payload["applied_move"]

    def board_markup(rows: list[str]) -> str:
        cells = "".join(
            f'<span class="cell token-{html.escape(cell.lower())}">{html.escape(cell)}</span>'
            for row in rows
            for cell in row
        )
        return f'<div class="board" style="--columns:{len(rows[0])}">{cells}</div>'

    trace_rows = "".join(
        "<tr><td>{step}</td><td><code>{skill}</code></td><td>{cost}</td>"
        "<td>{remaining}</td><td><code>{before}</code></td><td><code>{after}</code></td></tr>".format(
            step=event["step"],
            skill=html.escape(event["skill"]),
            cost=event["cost"],
            remaining=event["remaining_budget"],
            before=event["before_hash"],
            after=event["after_hash"],
        )
        for event in trace["events"]
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="可复现的三消Agent QA：白名单Skill、固定种子、确定性规则Oracle和事件回放。">
<link rel="icon" href="data:,"><title>Match-3 Agent QA · Deterministic Replay</title><style>
:root{{--ink:#172033;--muted:#657086;--line:#dfe5ee;--blue:#3157d5;--cyan:#35b6bd;--bg:#f4f7fb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{width:min(1080px,calc(100% - 32px));margin:auto}}header{{padding:66px 0 112px;background:radial-gradient(circle at 75% 20%,#324ca8 0,transparent 34%),linear-gradient(135deg,#11182c,#1a2852);color:white}}
.eyebrow{{font-size:12px;font-weight:850;letter-spacing:.16em;text-transform:uppercase;color:#82e2e5}}h1{{font-size:clamp(42px,7vw,76px);line-height:1.02;letter-spacing:-.05em;margin:18px 0}}header p{{max-width:760px;color:#c9d3ec;font-size:19px}}
.hero-metrics,.roles{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.hero-metrics{{margin-top:-62px}}.metric,.panel,.role{{background:white;border:1px solid var(--line);border-radius:17px;box-shadow:0 14px 38px #17203312}}
.metric{{padding:22px}}.metric b{{display:block;color:var(--blue);font-size:30px}}.metric span{{color:var(--muted);font-size:13px}}main{{padding-bottom:72px}}h2{{font-size:32px;line-height:1.2;margin:58px 0 12px}}.copy{{color:var(--muted);max-width:820px}}
.boards{{display:grid;grid-template-columns:1fr 72px 1fr;gap:22px;align-items:center}}.panel{{padding:24px;overflow:auto}}.panel h3{{margin-top:0}}.arrow{{text-align:center;font-size:34px;color:var(--cyan)}}
.board{{display:grid;grid-template-columns:repeat(var(--columns),minmax(38px,64px));gap:7px}}.cell{{aspect-ratio:1;border-radius:13px;display:grid;place-items:center;color:white;font-weight:900;font-size:20px;box-shadow:inset 0 -5px 0 #0002}}
.token-a{{background:#ff6b6b}}.token-b{{background:#5c7cfa}}.token-c{{background:#22b8a7}}.token-d{{background:#f59f00}}.token-e{{background:#9c5de5}}
.roles{{margin-top:20px}}.role{{padding:20px;border-top:4px solid var(--cyan)}}.role h3{{margin:0 0 6px}}.role p{{margin:0;color:var(--muted);font-size:14px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}th{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}}
.boundary{{margin-top:22px;padding:17px;border:1px solid #e9cc7a;background:#fff8df;border-radius:13px}}.links{{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}}.links a{{text-decoration:none;background:#e8efff;color:#2648ae;padding:8px 12px;border-radius:9px;font-weight:750}}
@media(max-width:760px){{.hero-metrics,.roles{{grid-template-columns:1fr 1fr}}.boards{{grid-template-columns:1fr}}.arrow{{transform:rotate(90deg)}}}}@media(max-width:480px){{.hero-metrics,.roles{{grid-template-columns:1fr}}}}
</style></head><body><header><div class="wrap"><span class="eyebrow">Engineering transfer · Offline · No API key</span>
<h1>Match-3<br>Agent QA</h1><p>Agent 负责选择受限 Skill，确定性规则引擎拥有交换、级联、计分和 pass/fail 的最终判定权。每个失败 seed 都能精确重放并进入回归集。</p></div></header>
<main class="wrap"><section class="hero-metrics"><div class="metric"><b>{analysis['legal_move_count']}</b><span>合法动作</span></div>
<div class="metric"><b>{move['cascades']}</b><span>固定级联</span></div><div class="metric"><b>{move['cleared_total']}</b><span>消除格数</span></div>
<div class="metric"><b>PASS</b><span>逐事件确定性回放</span></div></section>
<h2>同一个动作，重放到同一个状态。</h2><p class="copy">回归动作交换 (3,3) 与 (4,3)，使用 seed {move['seed']}。xorshift32 补充序列、每次级联位置和棋盘哈希都写入 trace。</p>
<section class="boards"><div class="panel"><h3>初始棋盘 · {trace['initial_board_hash']}</h3>{board_markup(trace['initial_board'])}</div>
<div class="arrow">→</div><div class="panel"><h3>最终棋盘 · {trace['final_board_hash']}</h3>{board_markup(trace['final_board'])}</div></section>
<h2>四个岗位视角，一份可运行证据。</h2><section class="roles"><article class="role"><h3>客户端</h3><p>纯状态转换、邻接交换、重力补充、多级联和事件回放。</p></article>
<article class="role"><h3>测试</h3><p>死局检测、属性扫描、固定 seed、非法动作失败闭锁。</p></article>
<article class="role"><h3>产品</h3><p>可玩步数、符号分布和透明的难度代理指标。</p></article>
<article class="role"><h3>AI Agent</h3><p>Skill schema、读写分离、成本预算和全链路 trace。</p></article></section>
<h2>Skill 执行轨迹</h2><div class="panel"><table><thead><tr><th>Step</th><th>Skill</th><th>Cost</th><th>Budget</th><th>Before</th><th>After</th></tr></thead>
<tbody>{trace_rows}</tbody></table></div><div class="boundary"><b>结论边界：</b>难度代理 {analysis['difficulty_proxy']} 只用于 QA 排序，不等价于玩家真实难度；当前模块是离线规则与 Agent 工具边界 MVP，不是完整 Unity 客户端。</div>
<div class="links"><a href="report.json">完整报告 JSON</a><a href="trace.json">完整 Trace JSON</a><a href="README.md">复现说明</a><a href="../index.html">返回 ATLAS-PIT-XBRL</a></div></main></body></html>"""


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run the offline Match-3 Agent QA demo")
    value.add_argument("--scenario", default="playable-seed-7")
    value.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "site" / "game-qa",
    )
    return value


def main() -> None:
    args = parser().parse_args()
    result = run_demo(args.output, scenario_id=args.scenario)
    print(
        "Match-3 Agent QA demo passed: "
        f"{result['initial_report']['analysis']['legal_move_count']} legal moves, "
        f"{result['applied_move']['cascades']} cascades, replay verified"
    )


if __name__ == "__main__":
    main()
