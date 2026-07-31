"""Generate a self-contained GitHub Pages report for the temporal audit."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any

from .evaluate_baselines import (
    DETECTOR_PREDICTIONS_JSONL,
    DETECTOR_SUMMARY_CSV,
    PREDICTIONS_JSONL,
    SUMMARY_CSV,
)
from .policies import POLICIES


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _metric_rows(bundle: dict[str, Any]) -> str:
    rows = []
    for key, (label, _) in POLICIES.items():
        selection = bundle["selection"][key]
        detection = bundle["detection"][key]
        rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{_pct(selection['decision_accuracy'])}</td>"
            f"<td>{_pct(selection['challenge_accuracy'])}</td>"
            f"<td>{_pct(selection['temporal_robustness_gap'])}</td>"
            f"<td>{_pct(detection['f1'])}</td>"
            f"<td>{_pct(detection['safe_evidence_retention_rate'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _sample_cards(bundle: dict[str, Any]) -> str:
    wanted = ("future_only", "period_conflict", "unit_conflict", "version_conflict")
    labels = {
        "future_only": "未来证据",
        "period_conflict": "错期间",
        "unit_conflict": "错单位",
        "version_conflict": "错版本",
    }
    samples: dict[str, dict[str, Any]] = {}
    for record in bundle["detector_predictions"]:
        condition = record["condition"]
        if (
            record["method"] == "teg_validator"
            and record["is_perturbed"]
            and condition in wanted
            and condition not in samples
        ):
            samples[condition] = record

    cards = []
    for condition in wanted:
        record = samples[condition]
        check_items = "".join(
            "<li class='{}'><b>{}</b>：{}</li>".format(
                "pass" if check["passed"] else "fail",
                html.escape(check["label"]),
                html.escape(check["reason"]),
            )
            for check in record["checks"]
        )
        cards.append(
            f"""<article class="trace-card">
<div class="tag">{html.escape(labels[condition])}</div>
<h3>{html.escape(record['candidate_id'])}</h3>
<p><b>判定：</b><span class="reject">REJECT</span> · 风险分数 {record['risk_score']:.2f}</p>
<ul>{check_items}</ul>
</article>"""
        )
    return "".join(cards)


def temporal_html(cases: list[dict[str, Any]], bundle: dict[str, Any]) -> str:
    base_count = len({case["base_question_id"] for case in cases})
    perturbation_count = sum(
        candidate["is_perturbed"]
        for case in cases
        for candidate in case["candidates"]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="金融搜索 Agent 的时间泄露、期间、版本与单位冲突审计">
<title>FinTemporal Audit</title>
<style>
:root{{--ink:#152238;--muted:#65758b;--navy:#102a56;--blue:#2764c4;--cyan:#2fa7a0;--red:#b42318;--green:#147d52;--bg:#f3f6fb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}}
a{{color:var(--blue)}}.wrap{{max-width:1120px;margin:auto}}header{{padding:64px 24px 74px;background:linear-gradient(130deg,#0d2347,#184d84 62%,#167f83);color:white}}
.kicker{{font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#a8e2df}}h1{{font-size:clamp(34px,6vw,60px);line-height:1.08;margin:8px 0 18px}}header p{{max-width:820px;color:#e4edfb;font-size:18px}}nav a{{color:white;margin-right:20px}}
main{{padding:0 24px 80px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:-42px}}.stat,.panel,.trace-card{{background:white;border:1px solid #e3e8f0;border-radius:16px;box-shadow:0 10px 30px #102a5612}}
.stat{{padding:20px}}.stat b{{display:block;font-size:29px;color:var(--blue)}}h2{{margin:48px 0 14px;font-size:28px}}h3{{margin:10px 0}}.panel{{padding:24px;overflow:auto}}
.lead{{font-size:18px;border-left:5px solid var(--cyan);padding:12px 18px;background:#edf9f8;border-radius:0 12px 12px 0}}
table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{padding:12px 10px;border-bottom:1px solid #e5e9f0;text-align:left}}th{{background:#eef3f8;color:#214b7b}}
.traces{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}.trace-card{{padding:22px}}.tag{{display:inline-block;background:#e7f0ff;color:#245aa5;border-radius:99px;padding:3px 10px;font-weight:800}}
.trace-card ul{{padding-left:22px}}.pass{{color:var(--green)}}.fail{{color:var(--red)}}.reject{{color:var(--red);font-weight:800}}
.formula{{font:600 18px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;background:#101f3a;color:#eef5ff;border-radius:12px;padding:16px;overflow:auto}}
.warning{{background:#fff7df;border:1px solid #f0d98d;border-radius:12px;padding:16px}}footer{{margin-top:48px;color:var(--muted)}}
@media(max-width:780px){{.stats,.traces{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{.stats,.traces{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header><div class="wrap">
<div class="kicker">FYP Prototype · Temporal Reliability</div>
<h1>FinTemporal Audit</h1>
<p>在金融搜索 Agent 生成答案之前，审计证据是否晚于截止日、是否属于正确期间、版本与单位；用受控冲突测试把“答案对不对”扩展为“证据在当时是否可用”。</p>
<nav><a href="index.html">搜索审计主页</a><a href="#results">实验表</a><a href="#traces">逐证据 trace</a><a href="temporal-detector-trace.jsonl">下载 trace</a></nav>
</div></header>
<main class="wrap">
<section class="stats">
<div class="stat"><b>{len(cases)}</b>受控实例</div>
<div class="stat"><b>{base_count}</b>真实金融问题</div>
<div class="stat"><b>{perturbation_count}</b>人工冲突候选</div>
<div class="stat"><b>4</b>审计方法</div>
</section>
<h2>研究问题</h2>
<p class="lead">当检索结果包含未来信息、错期间、错版本或错单位时，显式的证据审计能否降低 Agent 采用错误证据的概率，同时保留安全证据？</p>
<h2 id="results">初步实验</h2>
<div class="panel"><table><thead><tr><th>方法</th><th>总体决策准确率</th><th>挑战条件准确率</th><th>TRG ↓</th><th>候选检测 F1</th><th>安全证据保留率</th></tr></thead>
<tbody>{_metric_rows(bundle)}</tbody></table></div>
<p><b>TRG（Temporal Robustness Gap）</b>把 Look-Ahead-Bench 的“跨时期衰减”思想迁移到证据审计：</p>
<div class="formula">TRG = Accuracy(clean) - Accuracy(temporal / period / version / unit challenges)</div>
<p>越接近 0，说明方法从干净证据切换到受控冲突时越稳定。</p>
<h2 id="traces">可解释的逐证据审计</h2>
<div class="traces">{_sample_cards(bundle)}</div>
<h2>结论边界</h2>
<div class="warning"><b>当前结果是确定性协议验证，不是真实 LLM 排名。</b> 数据中的时间、期间、版本与单位均已显式标注，因此完整检测器达到满分是设计上限。<a href="live-pilot.html">已完成的 20题×4策略真实 pilot</a> 显示，开放网页的日期缺失会让严格 Gate 过度拒答，因此真实结果必须与受控结果分开报告。</div>
<h2>论文连接</h2>
<p>本原型受 <a href="https://arxiv.org/abs/2601.13770">Look-Ahead-Bench</a> 启发，但研究对象从“交易收益是否跨时期衰减”改为“搜索证据是否满足 point-in-time 约束”，更贴合 FinSearchComp 的搜索与引用任务。</p>
<footer>生成文件：temporal-metrics.csv · temporal-detector-metrics.csv · temporal-predictions.jsonl · temporal-detector-trace.jsonl</footer>
</main></body></html>"""


def write_temporal_site(
    cases: list[dict[str, Any]], bundle: dict[str, Any], output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "temporal-audit.html").write_text(
        temporal_html(cases, bundle), encoding="utf-8"
    )
    copies = {
        SUMMARY_CSV: "temporal-metrics.csv",
        DETECTOR_SUMMARY_CSV: "temporal-detector-metrics.csv",
        PREDICTIONS_JSONL: "temporal-predictions.jsonl",
        DETECTOR_PREDICTIONS_JSONL: "temporal-detector-trace.jsonl",
    }
    for source, filename in copies.items():
        shutil.copyfile(source, output_dir / filename)

    manifest = {
        "cases": len(cases),
        "base_questions": len({case["base_question_id"] for case in cases}),
        "methods": list(POLICIES),
        "artifacts": ["temporal-audit.html", *copies.values()],
    }
    (output_dir / "temporal-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
