from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = (
    REPO_ROOT
    / "temporal_clash"
    / "results"
    / "atlas_xbrl_20q_sonnet5_20260813"
)
RESULT_FILES = (
    "README.md",
    "metrics.json",
    "case_outcomes.csv",
    "gold_audit.json",
    "study_manifest.json",
    "exclusions.json",
    "trace.jsonl",
)


def load_xbrl_result(result_dir: Path = RESULT_DIR) -> dict[str, Any]:
    missing = [name for name in RESULT_FILES if not (result_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"ATLAS-XBRL result is incomplete: {missing}")
    with (result_dir / "case_outcomes.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        outcomes = list(csv.DictReader(handle))
    result = {
        "result_dir": result_dir,
        "metrics": json.loads((result_dir / "metrics.json").read_text(encoding="utf-8")),
        "manifest": json.loads(
            (result_dir / "study_manifest.json").read_text(encoding="utf-8")
        ),
        "gold_audit": json.loads(
            (result_dir / "gold_audit.json").read_text(encoding="utf-8")
        ),
        "outcomes": outcomes,
    }
    if len(outcomes) != 20 or result["manifest"]["valid_runs"] != 40:
        raise ValueError("Published ATLAS-XBRL result must be the complete 20x2 study")
    return result


def _pct(value: float) -> str:
    return f"{value:.0%}"


def _shared_css() -> str:
    return """
:root{--ink:#eaf2ff;--muted:#9eb0c7;--night:#07111f;--panel:#101e31;
--line:#263b53;--blue:#6ed8ff;--green:#58e3a1;--lime:#d9ff66;--red:#ff8b8b;
--paper:#f5f7fb;--paper-ink:#172033;--paper-muted:#667085}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--night);
color:var(--ink);font:16px/1.68 Inter,ui-sans-serif,system-ui,"Microsoft YaHei",sans-serif}
a{color:inherit}.wrap{width:min(1160px,calc(100% - 40px));margin:auto}.nav{display:flex;
align-items:center;justify-content:space-between;padding:22px 0}.brand{font-weight:900;letter-spacing:-.03em;
text-decoration:none}.brand i{font-style:normal;color:var(--lime)}.navlinks{display:flex;gap:22px;
color:var(--muted);font-size:14px}.navlinks a{text-decoration:none}.navlinks a:hover{color:white}
.hero{position:relative;overflow:hidden;padding-bottom:88px;background:
radial-gradient(circle at 75% 8%,#143e5f 0,transparent 38%),
radial-gradient(circle at 18% 45%,#143a2d 0,transparent 34%),var(--night)}
.hero:after{content:"";position:absolute;inset:auto -10% -180px;width:65%;height:300px;
background:#6ed8ff1a;filter:blur(90px);border-radius:50%}.eyebrow{display:inline-flex;gap:8px;
align-items:center;border:1px solid #31506f;background:#11243a;border-radius:999px;padding:7px 12px;
color:#b9d9f1;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}
.dot{width:7px;height:7px;background:var(--green);border-radius:99px;box-shadow:0 0 16px var(--green)}
.hero-grid{display:grid;grid-template-columns:1.25fr .75fr;gap:56px;align-items:center;padding-top:72px}
h1{font-size:clamp(46px,7vw,84px);line-height:.98;letter-spacing:-.065em;margin:20px 0 24px;
max-width:820px}.gradient{background:linear-gradient(100deg,var(--lime),var(--blue));color:transparent;
background-clip:text;-webkit-background-clip:text}.lead{max-width:760px;color:#bfd0e4;font-size:clamp(17px,2vw,21px)}
.actions,.chips,.artifact-links{display:flex;gap:12px;flex-wrap:wrap}.actions{margin-top:30px}.button{
display:inline-flex;align-items:center;justify-content:center;text-decoration:none;font-weight:850;border-radius:11px;
padding:12px 17px;background:var(--lime);color:#10170b;border:1px solid var(--lime)}.button.secondary{
background:#ffffff08;color:white;border-color:#3a536d}.score-card{position:relative;background:linear-gradient(150deg,#152a40,#0d1b2b);
border:1px solid #31506b;border-radius:22px;padding:26px;box-shadow:0 30px 70px #0006}.score-card:before{
content:"REAL LLM × REAL SEC";font-size:10px;letter-spacing:.16em;color:var(--blue);font-weight:900}
.score-line{display:flex;justify-content:space-between;align-items:flex-end;margin:24px 0 8px}.score-line span{
color:var(--muted);font-size:13px}.score-line b{font-size:32px}.meter{height:10px;background:#07111f;border-radius:99px;
overflow:hidden}.meter i{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#3b82f6,var(--blue))}
.meter.winner i{background:linear-gradient(90deg,#1ebd75,var(--lime))}.delta{margin-top:26px;border-top:1px solid var(--line);
padding-top:20px;display:flex;justify-content:space-between}.delta b{font-size:28px;color:var(--lime)}
.light{background:var(--paper);color:var(--paper-ink);padding:86px 0}.section-kicker{color:#08775a;
font-size:12px;font-weight:900;letter-spacing:.14em;text-transform:uppercase}.section-title{font-size:clamp(32px,5vw,54px);
letter-spacing:-.045em;line-height:1.08;margin:10px 0 18px;max-width:850px}.section-copy{color:var(--paper-muted);
font-size:18px;max-width:820px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-top:38px}
.stat{background:white;border:1px solid #e1e6ee;border-radius:16px;padding:22px;box-shadow:0 8px 26px #1720330d}
.stat b{display:block;font-size:31px;letter-spacing:-.04em}.stat span{color:var(--paper-muted);font-size:13px}
.flow{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:36px}.flow article{position:relative;
background:white;border:1px solid #dfe6ed;border-radius:14px;padding:18px}.flow article:not(:last-child):after{content:"→";
position:absolute;right:-12px;top:38%;z-index:2;color:#668099;font-weight:900}.flow small{color:#08775a;font-weight:900}
.flow h3{font-size:16px;margin:8px 0}.flow p{font-size:13px;color:var(--paper-muted);margin:0}
.dark{padding:86px 0;background:#091522}.compare-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:32px}
.method{border:1px solid var(--line);background:var(--panel);border-radius:18px;padding:27px}.method.winner{
border-color:#3fa976;box-shadow:inset 0 0 0 1px #3fa97644}.method h3{font-size:24px;margin:0}.method .big{
font-size:56px;line-height:1;font-weight:950;letter-spacing:-.06em;margin:24px 0 12px}.method.winner .big{color:var(--lime)}
.method ul{padding-left:20px;color:var(--muted)}.method strong{color:white}.error-grid{display:grid;
grid-template-columns:repeat(5,1fr);gap:12px;margin-top:34px}.error-card{background:#111f31;border:1px solid var(--line);
border-radius:14px;padding:18px}.error-card b{color:var(--red);font-size:20px}.error-card h3{font-size:15px;
line-height:1.35}.error-card p{color:var(--muted);font-size:13px;margin-bottom:0}.papers{display:grid;
grid-template-columns:repeat(3,1fr);gap:14px;margin-top:34px}.paper{background:white;border:1px solid #e0e6ee;
border-radius:15px;padding:21px;text-decoration:none;transition:.18s ease}.paper:hover{transform:translateY(-3px);
box-shadow:0 14px 32px #17203312}.paper small{color:#08775a;font-weight:900}.paper h3{line-height:1.35;margin:8px 0}
.paper p{color:var(--paper-muted);font-size:14px}.audit{background:#0d1c2c;border:1px solid #29425b;
border-radius:20px;padding:28px;margin-top:36px}.audit code{display:block;overflow-wrap:anywhere;background:#07111f;
border-radius:10px;padding:13px;color:var(--blue);font-size:12px}.audit-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.chips span{background:#14283d;border:1px solid #2d465f;border-radius:999px;padding:6px 10px;color:#bad0e5;font-size:12px}
.artifact-links a{text-decoration:none;background:white;color:#174ea6;border:1px solid #dce4ee;border-radius:11px;
padding:11px 14px;font-weight:800}.boundary{background:#fff8dc;border:1px solid #ead58a;border-radius:16px;
padding:23px;margin-top:30px;color:#5f4b16}.boundary b{color:#493600}.footer{padding:38px 0;color:#8499b2;
border-top:1px solid #20354b;font-size:13px}.footer-row{display:flex;justify-content:space-between;gap:25px}
.study-hero{padding:40px 0 70px;background:radial-gradient(circle at 80% 0,#143e5f,transparent 35%),var(--night)}
.facts-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-top:28px}.fact{background:#102137;
border:1px solid #29445f;border-radius:14px;padding:18px}.fact b{display:block;font-size:29px}.fact span{color:var(--muted);font-size:12px}
.table-shell{overflow:auto;background:white;border:1px solid #e1e6ec;border-radius:16px;margin-top:28px}
table{border-collapse:collapse;width:100%}th,td{padding:13px 14px;border-bottom:1px solid #e8ecf1;text-align:left;
white-space:nowrap}th{background:#eef3f7;font-size:12px;color:#526174;position:sticky;top:0}.question{white-space:normal;
min-width:360px}.ok{color:#08775a;font-weight:900}.bad{color:#b42318;font-weight:900}.tag{display:inline-block;
border-radius:99px;padding:3px 8px;background:#e8f7ef;font-size:11px;color:#08775a;font-weight:900}
@media(max-width:900px){.hero-grid,.audit-grid{grid-template-columns:1fr}.score-card{max-width:620px}.stats,.facts-grid{
grid-template-columns:repeat(2,1fr)}.flow{grid-template-columns:1fr 1fr}.flow article:after{display:none}.error-grid{grid-template-columns:1fr 1fr}
.papers{grid-template-columns:1fr 1fr}}@media(max-width:620px){.wrap{width:min(100% - 26px,1160px)}.navlinks{display:none}
.hero-grid{padding-top:42px}.compare-grid,.stats,.facts-grid,.flow,.error-grid,.papers{grid-template-columns:1fr}.footer-row{display:block}
h1{font-size:48px}.light,.dark{padding:64px 0}}
"""


def build_homepage(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    plain = metrics["strategies"]["plain_agent"]
    xbrl = metrics["strategies"]["atlas_xbrl"]
    comparison = metrics["comparison"]
    manifest = result["manifest"]
    gold_hash = result["gold_audit"]["case_batch_sha256"]
    losing_rows = [row for row in result["outcomes"] if row["plain_correct"] == "0"]
    error_copy = {
        "tesla_2024_operating_margin_change_xbrl": ("−1.94 → 1.94", "把“下降多少”输出成负数"),
        "meta_vs_alphabet_2023_op_margin_change_gap_xbrl": ("8.88 → 8.87", "中间比率提前舍入"),
        "amd_vs_intel_2023_rd_intensity_change_gap_xbrl": ("2.88 → 2.89", "多步比率累计舍入"),
        "nvidia_vs_tesla_2024_op_margin_change_gap_xbrl": ("46.03 → 40.41", "跨公司公式与方向错误"),
        "alphabet_vs_meta_2023_rd_growth_gap_xbrl": ("6.10 → 6.11", "增长率提前舍入"),
    }
    errors = "".join(
        f"""<article class="error-card"><b>{html.escape(error_copy[row['case_id']][0])}</b>
        <h3>{html.escape(row['question_zh'])}</h3><p>{html.escape(error_copy[row['case_id']][1])}</p></article>"""
        for row in losing_rows
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="ATLAS-XBRL：Claude Sonnet 5语义编译、SEC官方XBRL取数与程序化金融计算。20题100%，普通搜索75%。">
<title>ATLAS-XBRL · 真实金融Agent实验</title><style>{_shared_css()}</style></head><body>
<header class="hero"><nav class="nav wrap"><a class="brand" href="index.html">FinSearchComp<i> / ATLAS</i></a>
<div class="navlinks"><a href="#result">结果</a><a href="#method">方法</a><a href="#failures">错误分析</a>
<a href="#papers">顶会依据</a><a href="xbrl-study.html">20题报告</a></div></nav>
<div class="hero-grid wrap"><div><span class="eyebrow"><i class="dot"></i>Real LLM · Real SEC · 2026-08-13</span>
<h1>从 <span class="gradient">75%</span><br>到 <span class="gradient">100%</span></h1>
<p class="lead">Claude理解金融问题，SEC XBRL提供未经四舍五入的一手事实，确定性程序负责公式与方向。
20道冻结题上，ATLAS-XBRL比同模型普通搜索高25个百分点。</p>
<div class="actions"><a class="button" href="xbrl-study.html">查看20题逐题结果 →</a>
<a class="button secondary" href="atlas-xbrl/trace.jsonl">打开40条Trace</a></div></div>
<aside class="score-card"><div class="score-line"><span>普通 Web Search Agent</span><b>{_pct(plain['decision_accuracy'])}</b></div>
<div class="meter"><i style="width:{plain['decision_accuracy']:.0%}"></i></div>
<div class="score-line"><span>ATLAS-XBRL</span><b>{_pct(xbrl['decision_accuracy'])}</b></div>
<div class="meter winner"><i style="width:{xbrl['decision_accuracy']:.0%}"></i></div>
<div class="delta"><span>配对提升<br><small>95% CI: +5～+45pp</small></span><b>+25pp</b></div></aside></div></header>

<main><section class="light" id="result"><div class="wrap"><span class="section-kicker">Formal live study</span>
<h2 class="section-title">结果不是挑出来的：20题在运行前冻结，40条真实调用全部保留。</h2>
<p class="section-copy">20个gold先由独立程序调用SEC官方Company Facts复算；正式提示词不含gold、参考公式或参考程序。
评分要求两位小数精确相等且单位一致。</p><div class="stats">
<div class="stat"><b>20 / 20</b><span>ATLAS-XBRL准确回答</span></div><div class="stat"><b>15 / 20</b><span>普通搜索准确回答</span></div>
<div class="stat"><b>5 / 15 / 0</b><span>逐题胜 / 平 / 负</span></div><div class="stat"><b>40 / 40</b><span>严格有效真实Trace</span></div></div>
<div class="boundary"><b>结论边界：</b>100%只属于这20道可映射到SEC XBRL的结构化金融数值题；
它不是开放域所有RAG任务、完整FinSearchComp或通用SOTA的100%。</div></div></section>

<section class="dark" id="method"><div class="wrap"><span class="section-kicker" style="color:var(--blue)">System design</span>
<h2 class="section-title">不让一个模型同时承担搜索、抄数、公式和舍入。</h2>
<p class="lead">ATLAS-XBRL把易错的生成链拆成五个可验证阶段，每一步都有明确输入输出。</p>
<div class="flow"><article><small>01</small><h3>语义编译</h3><p>Claude识别公司、指标、期间和公式类型。</p></article>
<article><small>02</small><h3>程序校准</h3><p>Label-free语法固定操作数顺序和正负方向。</p></article>
<article><small>03</small><h3>SEC取数</h3><p>按cutoff、10-K、财年和US-GAAP taxonomy选值。</p></article>
<article><small>04</small><h3>确定性计算</h3><p>Decimal执行白名单公式，最后一步统一舍入。</p></article>
<article><small>05</small><h3>完整审计</h3><p>保存accession、响应哈希、操作数、公式与citation。</p></article></div>
<div class="compare-grid"><article class="method"><h3>普通搜索 Agent</h3><div class="big">75%</div><ul>
<li><strong>40</strong>个模型HTTP阶段</li><li><strong>52</strong>次真实Web Search</li><li><strong>383</strong>个来源、73条原生citations</li>
<li>模型同时承担取数和计算</li></ul></article><article class="method winner"><h3>ATLAS-XBRL</h3><div class="big">100%</div><ul>
<li><strong>20</strong>个模型HTTP阶段</li><li><strong>8</strong>次SEC下载、80次缓存命中</li><li><strong>88</strong>条SEC事实citations</li>
<li>LLM规划，官方数据供事实，程序执行</li></ul></article></div></div></section>

<section class="dark" id="failures" style="padding-top:10px"><div class="wrap"><span class="section-kicker" style="color:#ffb1b1">Failure analysis</span>
<h2 class="section-title">普通搜索错的5题，ATLAS-XBRL全部修复。</h2><p class="lead">错误集中在方向、提前舍入和跨公司多步公式，
说明检索到相关网页不等于完成了可靠的数值推理。</p><div class="error-grid">{errors}</div></div></section>

<section class="light" id="papers"><div class="wrap"><span class="section-kicker">Research grounding</span>
<h2 class="section-title">最新顶会思想，落成可以运行和审计的金融系统。</h2><div class="papers">
<a class="paper" href="https://aclanthology.org/2026.eacl-long.322/"><small>EACL 2026 · Long</small><h3>Query Decomposition for RAG</h3><p>将复杂问题拆成互补子查询；本项目将财务公式拆成2–8个事实请求。</p></a>
<a class="paper" href="https://aclanthology.org/2026.findings-acl.187/"><small>ACL 2026 · Findings</small><h3>FinMRAGBench</h3><p>真实财报需要跨页证据和多步工具推理；本项目保存每个操作数和accession。</p></a>
<a class="paper" href="https://aclanthology.org/2025.acl-long.1089/"><small>ACL 2025 · Long</small><h3>ChainRAG</h3><p>渐进检索避免实体在推理链中丢失；程序显式保留公司、指标、年度和顺序。</p></a>
<a class="paper" href="https://aclanthology.org/2025.findings-emnlp.382/"><small>EMNLP 2025 · Findings</small><h3>FinGEAR</h3><p>金融披露需要领域结构和术语映射；本项目以US-GAAP taxonomy路由取代扁平文本。</p></a>
<a class="paper" href="https://openreview.net/pdf?id=Jjr2Odj8DJ"><small>ICLR 2025</small><h3>Sufficient Context</h3><p>区分上下文不足与使用失败；缺少操作数时切换官方结构化工具。</p></a>
<a class="paper" href="https://aclanthology.org/2025.acl-srw.32/"><small>ACL 2025 · SRW</small><h3>Question Decomposition for RAG</h3><p>分别检索并合并多跳证据；每个财务事实独立校验再统一计算。</p></a></div></div></section>

<section class="dark"><div class="wrap"><span class="section-kicker" style="color:var(--blue)">Auditability</span>
<h2 class="section-title">每个100%都可以追溯。</h2><div class="audit"><div class="audit-grid"><div><h3>运行前Gold审计</h3>
<p style="color:var(--muted)">20/20通过SEC官方Company Facts复算。正式预注册提交：<code>{html.escape(manifest['preregistered_commit'])}</code></p>
<code>{html.escape(gold_hash)}</code></div><div><h3>公开产物</h3><div class="chips"><span>40 traces</span><span>88 SEC citations</span>
<span>0 transport error</span><span>0 invalid record</span><span>Claude Sonnet 5</span></div></div></div></div>
<div class="artifact-links" style="margin-top:18px"><a href="xbrl-study.html">逐题研究报告</a><a href="atlas-xbrl/case_outcomes.csv">结果CSV</a>
<a href="atlas-xbrl/gold_audit.json">Gold审计</a><a href="atlas-xbrl/study_manifest.json">研究清单</a>
<a href="atlas-xbrl/trace.jsonl">完整Trace</a><a href="https://github.com/QiQiyzhu/FinSearchComp-Audit">GitHub源码</a></div></div></section></main>
<footer class="footer"><div class="wrap footer-row"><span>FinSearchComp-Audit · ATLAS-XBRL · 2026</span>
<span>真实模型 · 官方SEC数据 · 无密钥入库 · 不构成投资建议</span></div></footer></body></html>"""


def build_study_page(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    plain = metrics["strategies"]["plain_agent"]
    xbrl = metrics["strategies"]["atlas_xbrl"]
    comparison = metrics["comparison"]
    rows = []
    for index, item in enumerate(result["outcomes"], start=1):
        plain_ok = item["plain_correct"] == "1"
        xbrl_ok = item["xbrl_correct"] == "1"
        rows.append(
            "<tr><td>{}</td><td class=\"question\">{}</td><td>{} {}</td>"
            "<td class=\"{}\">{} {}</td><td class=\"{}\">{} {}</td>"
            "<td><span class=\"tag\">{}</span></td></tr>".format(
                index,
                html.escape(item["question_zh"]),
                html.escape(item["gold_answer"]),
                html.escape(item["canonical_unit"]),
                "ok" if plain_ok else "bad",
                "✓" if plain_ok else "✕",
                html.escape(item["plain_answer"] or "拒答"),
                "ok" if xbrl_ok else "bad",
                "✓" if xbrl_ok else "✕",
                html.escape(item["xbrl_answer"] or "拒答"),
                html.escape(item["operation"]),
            )
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="ATLAS-XBRL 20题真实实验逐题结果">
<title>20题正式实验 · ATLAS-XBRL</title><style>{_shared_css()}</style></head><body>
<header class="study-hero"><nav class="nav wrap"><a class="brand" href="index.html">← FinSearchComp<i> / ATLAS</i></a>
<div class="navlinks"><a href="atlas-xbrl/README.md">研究卡</a><a href="atlas-xbrl/trace.jsonl">Trace</a><a href="atlas-xbrl/gold_audit.json">Gold审计</a></div></nav>
<div class="wrap" style="padding-top:36px"><span class="eyebrow"><i class="dot"></i>20 Questions · 2 Systems · 40 Valid Traces</span>
<h1 style="max-width:920px">ATLAS-XBRL<br><span class="gradient">正式逐题结果</span></h1>
<p class="lead">同一个Claude Sonnet 5，普通Web Search Agent与“LLM编译 + SEC XBRL + Decimal执行”的完整配对对照。</p>
<div class="facts-grid"><div class="fact"><b>{_pct(plain['decision_accuracy'])}</b><span>普通搜索准确率</span></div>
<div class="fact"><b>{_pct(xbrl['decision_accuracy'])}</b><span>ATLAS-XBRL准确率</span></div>
<div class="fact"><b>+{comparison['paired_accuracy_difference'] * 100:.0f}pp</b><span>配对提升（百分点）</span></div>
<div class="fact"><b>5 / 15 / 0</b><span>胜 / 平 / 负</span></div></div></div></header>
<main class="light"><div class="wrap"><span class="section-kicker">Per-question evidence</span>
<h2 class="section-title">20道题，每个结果都可检查。</h2><p class="section-copy">评分要求最终动作是answer、单位完全一致、数值与SEC复算gold在要求的两位小数上精确相等。</p>
<div class="table-shell"><table><thead><tr><th>#</th><th>问题</th><th>Gold</th><th>普通搜索</th><th>ATLAS-XBRL</th><th>程序</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<div class="stats"><div class="stat"><b>52</b><span>普通Agent Web Searches</span></div><div class="stat"><b>383</b><span>普通Agent捕获来源</span></div>
<div class="stat"><b>8 + 80</b><span>SEC下载 + 缓存命中</span></div><div class="stat"><b>88</b><span>SEC事实citations</span></div></div>
<div class="boundary"><b>统计边界：</b>配对bootstrap 95% CI为+5至+45个百分点；20题、单模型、单次运行仍不足以宣称通用SOTA。</div>
<h2 class="section-title" style="margin-top:62px">下载审计产物</h2><div class="artifact-links"><a href="atlas-xbrl/case_outcomes.csv">逐题CSV</a>
<a href="atlas-xbrl/metrics.json">聚合指标</a><a href="atlas-xbrl/gold_audit.json">Gold审计</a><a href="atlas-xbrl/study_manifest.json">协议清单</a>
<a href="atlas-xbrl/exclusions.json">排除记录</a><a href="atlas-xbrl/trace.jsonl">40条Trace</a></div></div></main>
<footer class="footer"><div class="wrap footer-row"><span>ATLAS-XBRL formal live study</span><span>Preregistered · Trace-complete · SEC-grounded</span></div></footer>
</body></html>"""
