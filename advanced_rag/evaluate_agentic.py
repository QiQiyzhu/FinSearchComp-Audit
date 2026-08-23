"""Reproduce controlled experiments E4 (gap planning), E5 (gate), E6 (budget)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
import html
import json
from pathlib import Path
import shutil
from statistics import mean
from typing import Iterable, Sequence

from .agentic import (
    AgenticDocument,
    AgenticQuery,
    AgenticRetriever,
    DeterministicCalculator,
    GapPlan,
    StructuredGapPlanner,
    SufficiencyDecision,
    SufficiencyGate,
    slot_recall,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(__file__).with_name("agentic_cases.json")
DEFAULT_OUTPUT = Path(__file__).with_name("results") / "agentic"
DEFAULT_SITE = ROOT / "site" / "agentic-eval"
BUDGETS = (1, 2, 3, 5, 8)
ONE_SHOT_TOP_K = 3


@dataclass(frozen=True)
class AgenticCase:
    query: AgenticQuery
    gold_answer: str
    documents: tuple[AgenticDocument, ...]


def load_cases(path: Path = DATASET_PATH) -> tuple[AgenticCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for raw in payload["cases"]:
        cases.append(
            AgenticCase(
                query=AgenticQuery(
                    query_id=raw["query_id"],
                    question=raw["question"],
                    as_of=date.fromisoformat(raw["as_of"]),
                ),
                gold_answer=raw["gold_answer"],
                documents=tuple(AgenticDocument.from_dict(item) for item in raw["documents"]),
            )
        )
    return tuple(cases)


def _cost_proxy(search_calls: int, documents: Iterable[AgenticDocument]) -> int:
    """Deterministic token-equivalent proxy; not an API billing estimate."""

    document_cost = sum(max(12, len(document.text.split()) * 4) for document in documents)
    return search_calls * 120 + document_cost


def _structured_retrieve(
    plan: GapPlan,
    case: AgenticCase,
    budget: int,
    retriever: AgenticRetriever,
    gate: SufficiencyGate,
) -> tuple[list[AgenticDocument], SufficiencyDecision, int]:
    selected: list[AgenticDocument] = []
    search_calls = 0
    decision = gate.assess(plan, selected, case.query.as_of)
    for slot in plan.slots:
        if search_calls >= budget or decision.answerable:
            break
        document = retriever.search_slot(slot, case.documents, case.query.as_of)
        search_calls += 1
        if document is not None and document.doc_id not in {item.doc_id for item in selected}:
            selected.append(document)
        decision = gate.assess(plan, selected, case.query.as_of)
    return selected, decision, search_calls


def run_gap_ablation(cases: Sequence[AgenticCase]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    planner = StructuredGapPlanner()
    retriever = AgenticRetriever()
    gate = SufficiencyGate()
    detail: list[dict[str, object]] = []

    for case in cases:
        plan = planner.plan(case.query)
        for method in ("plain_one_shot", "query_rewrite", "structured_gap_planner"):
            if method == "plain_one_shot":
                selected = retriever.search_text(
                    case.query.question, case.documents, case.query.as_of, limit=ONE_SHOT_TOP_K
                )
                calls = 1
                decision = gate.assess(plan, selected, case.query.as_of)
            elif method == "query_rewrite":
                selected = retriever.search_text(
                    retriever.rewrite(plan), case.documents, case.query.as_of, limit=ONE_SHOT_TOP_K
                )
                calls = 1
                decision = gate.assess(plan, selected, case.query.as_of)
            else:
                selected, decision, calls = _structured_retrieve(plan, case, 4, retriever, gate)
            answer = DeterministicCalculator.answer(plan, decision)
            detail.append(
                {
                    "query_id": case.query.query_id,
                    "method": method,
                    "required_slots": len(plan.slots),
                    "retrieved_docs": len(selected),
                    "search_calls": calls,
                    "slot_recall": slot_recall(plan, decision),
                    "answerable": decision.answerable,
                    "answer": answer or "ABSTAIN",
                    "correct": answer == case.gold_answer,
                    "cost_proxy": _cost_proxy(calls, selected),
                }
            )

    aggregate = []
    for method in ("plain_one_shot", "query_rewrite", "structured_gap_planner"):
        rows = [row for row in detail if row["method"] == method]
        aggregate.append(
            {
                "method": method,
                "cases": len(rows),
                "answer_accuracy": mean(float(row["correct"]) for row in rows),
                "mean_slot_recall": mean(float(row["slot_recall"]) for row in rows),
                "mean_search_calls": mean(int(row["search_calls"]) for row in rows),
                "mean_cost_proxy": mean(int(row["cost_proxy"]) for row in rows),
            }
        )
    return aggregate, detail


def _matches_slot(document: AgenticDocument, plan: GapPlan) -> bool:
    return any(
        document.company == slot.company
        and document.metric == slot.metric
        and document.period == slot.period
        and document.unit == slot.unit
        for slot in plan.slots
    )


def _condition_documents(case: AgenticCase, plan: GapPlan, condition: str) -> tuple[AgenticDocument, ...]:
    if condition == "complete":
        return case.documents
    if condition == "partial":
        omitted = plan.slots[-1]
        return tuple(
            document
            for document in case.documents
            if not (
                document.company == omitted.company
                and document.metric == omitted.metric
                and document.period == omitted.period
                and document.unit == omitted.unit
            )
        )
    if condition == "empty":
        return tuple(document for document in case.documents if not _matches_slot(document, plan))
    if condition == "conflict":
        target = plan.slots[0]
        source = next(
            document
            for document in case.documents
            if document.company == target.company
            and document.metric == target.metric
            and document.period == target.period
            and document.version == "final"
            and document.is_visible_at(case.query.as_of)
        )
        conflict_value = source.value + Decimal("17")
        conflict_text = (
            f"{source.company} {source.period} final filing {source.metric} "
            f"{conflict_value} {source.unit} conflicting controlled source"
        )
        conflict = replace(
            source,
            doc_id=f"{source.doc_id}-conflict",
            value=conflict_value,
            text=conflict_text,
            content_hash="conflict-" + source.content_hash,
        )
        return (*case.documents, conflict)
    raise ValueError(f"Unknown evidence condition: {condition}")


def run_sufficiency_gate(cases: Sequence[AgenticCase]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    planner = StructuredGapPlanner()
    gate = SufficiencyGate()
    detail: list[dict[str, object]] = []
    conditions = ("complete", "partial", "empty", "conflict")

    for case in cases:
        plan = planner.plan(case.query)
        for condition in conditions:
            documents = _condition_documents(case, plan, condition)
            gated = gate.assess(plan, documents, case.query.as_of)
            visible_matches = [
                document
                for document in documents
                if _matches_slot(document, plan)
                and document.version == "final"
                and document.is_visible_at(case.query.as_of)
            ]
            should_answer = condition == "complete"
            for method in ("must_answer_baseline", "sufficiency_gate"):
                if method == "sufficiency_gate":
                    answers = gated.answerable
                    reason = gated.reason
                else:
                    answers = bool(visible_matches)
                    reason = "answer_if_any_evidence" if answers else "no_evidence"
                correct_answer = answers and should_answer and gated.answerable and (
                    DeterministicCalculator.answer(plan, gated) == case.gold_answer
                )
                detail.append(
                    {
                        "query_id": case.query.query_id,
                        "condition": condition,
                        "method": method,
                        "should_answer": should_answer,
                        "decision": "ANSWER" if answers else "ABSTAIN",
                        "correct_answer": correct_answer,
                        "correct_abstention": (not should_answer) and (not answers),
                        "false_abstention": should_answer and (not answers),
                        "unsupported_answer": (not should_answer) and answers,
                        "reason": reason,
                        "missing_slots": ";".join(gated.missing_slots),
                        "conflicting_slots": ";".join(gated.conflicting_slots),
                    }
                )

    aggregate = []
    for method in ("must_answer_baseline", "sufficiency_gate"):
        rows = [row for row in detail if row["method"] == method]
        abstain_cases = [row for row in rows if not row["should_answer"]]
        answer_cases = [row for row in rows if row["should_answer"]]
        aggregate.append(
            {
                "method": method,
                "cases": len(rows),
                "answer_coverage": mean(float(row["decision"] == "ANSWER") for row in rows),
                "correct_answer_rate": mean(float(row["correct_answer"]) for row in answer_cases),
                "correct_abstention_rate": mean(float(row["correct_abstention"]) for row in abstain_cases),
                "false_abstention_rate": mean(float(row["false_abstention"]) for row in answer_cases),
                "unsupported_answer_rate": mean(float(row["unsupported_answer"]) for row in abstain_cases),
            }
        )
    return aggregate, detail


def run_budget_sweep(cases: Sequence[AgenticCase]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    planner = StructuredGapPlanner()
    retriever = AgenticRetriever()
    gate = SufficiencyGate()
    detail: list[dict[str, object]] = []
    for budget in BUDGETS:
        for case in cases:
            plan = planner.plan(case.query)
            selected, decision, calls = _structured_retrieve(plan, case, budget, retriever, gate)
            answer = DeterministicCalculator.answer(plan, decision)
            detail.append(
                {
                    "budget": budget,
                    "query_id": case.query.query_id,
                    "required_slots": len(plan.slots),
                    "search_calls": calls,
                    "stopped_early": calls < budget,
                    "slot_recall": slot_recall(plan, decision),
                    "answerable": decision.answerable,
                    "correct": answer == case.gold_answer,
                    "cost_proxy": _cost_proxy(calls, selected),
                }
            )
    aggregate = []
    for budget in BUDGETS:
        rows = [row for row in detail if row["budget"] == budget]
        aggregate.append(
            {
                "budget": budget,
                "cases": len(rows),
                "answer_accuracy": mean(float(row["correct"]) for row in rows),
                "mean_slot_recall": mean(float(row["slot_recall"]) for row in rows),
                "mean_search_calls": mean(int(row["search_calls"]) for row in rows),
                "mean_cost_proxy": mean(int(row["cost_proxy"]) for row in rows),
                "early_stop_rate": mean(float(row["stopped_early"]) for row in rows),
            }
        )
    return aggregate, detail


def _write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _percent(value: object) -> str:
    return f"{float(value) * 100:.1f}%"


def _budget_svg(rows: Sequence[dict[str, object]]) -> str:
    width, height = 760, 330
    left, top, chart_w, chart_h = 70, 35, 640, 220
    max_cost = max(float(row["mean_cost_proxy"]) for row in rows)
    points_acc = []
    points_cost = []
    for index, row in enumerate(rows):
        x = left + index * chart_w / (len(rows) - 1)
        acc_y = top + chart_h * (1 - float(row["answer_accuracy"]))
        cost_y = top + chart_h * (1 - float(row["mean_cost_proxy"]) / max_cost)
        points_acc.append(f"{x:.1f},{acc_y:.1f}")
        points_cost.append(f"{x:.1f},{cost_y:.1f}")
    x_labels = "".join(
        f'<text x="{left + i * chart_w / (len(rows) - 1):.1f}" y="282" text-anchor="middle">{row["budget"]}</text>'
        for i, row in enumerate(rows)
    )
    grid = "".join(
        f'<line x1="{left}" y1="{top + i * chart_h / 4:.1f}" x2="{left + chart_w}" y2="{top + i * chart_h / 4:.1f}" />'
        for i in range(5)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Budget sweep accuracy and cost proxy">
<style>text{{font:13px system-ui;fill:#263238}}.grid line{{stroke:#dfe7ea}}.axis{{stroke:#78909c;stroke-width:1.5}}.acc{{fill:none;stroke:#00796b;stroke-width:4}}.cost{{fill:none;stroke:#ef6c00;stroke-width:4;stroke-dasharray:8 6}}</style>
<rect width="100%" height="100%" rx="16" fill="#ffffff"/><g class="grid">{grid}</g>
<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}"/><line class="axis" x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}"/>
<polyline class="acc" points="{' '.join(points_acc)}"/><polyline class="cost" points="{' '.join(points_cost)}"/>
{x_labels}<text x="390" y="315" text-anchor="middle">Maximum retrieval-call budget</text>
<text x="78" y="20">Accuracy / normalized cost</text><text x="510" y="20" fill="#00796b">— accuracy</text><text x="610" y="20" fill="#ef6c00">-- cost proxy</text>
</svg>'''


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{html.escape(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(item)}</td>" for item in row) + "</tr>" for row in rows
    )
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _write_site(
    site_dir: Path,
    gap: Sequence[dict[str, object]],
    gate: Sequence[dict[str, object]],
    budget: Sequence[dict[str, object]],
) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "budget-curve.svg").write_text(_budget_svg(budget), encoding="utf-8")
    gap_table = _table(
        ("Method", "Accuracy", "Slot recall", "Search calls", "Cost proxy"),
        [
            (
                str(row["method"]),
                _percent(row["answer_accuracy"]),
                _percent(row["mean_slot_recall"]),
                f'{float(row["mean_search_calls"]):.2f}',
                f'{float(row["mean_cost_proxy"]):.0f}',
            )
            for row in gap
        ],
    )
    gate_table = _table(
        ("Policy", "Answer coverage", "Correct abstention", "Unsupported answers", "False abstention"),
        [
            (
                str(row["method"]),
                _percent(row["answer_coverage"]),
                _percent(row["correct_abstention_rate"]),
                _percent(row["unsupported_answer_rate"]),
                _percent(row["false_abstention_rate"]),
            )
            for row in gate
        ],
    )
    budget_table = _table(
        ("Budget", "Accuracy", "Slot recall", "Actual calls", "Cost proxy", "Early stop"),
        [
            (
                str(row["budget"]),
                _percent(row["answer_accuracy"]),
                _percent(row["mean_slot_recall"]),
                f'{float(row["mean_search_calls"]):.2f}',
                f'{float(row["mean_cost_proxy"]):.0f}',
                _percent(row["early_stop_rate"]),
            )
            for row in budget
        ],
    )
    page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>E4–E6 Agentic Evaluation · FinSearchComp-Audit</title>
<style>
:root{{--ink:#17242b;--muted:#5f6f76;--teal:#00796b;--orange:#ef6c00;--paper:#f4f7f6;--line:#dce5e3}}
*{{box-sizing:border-box}}body{{margin:0;font:16px/1.65 Inter,ui-sans-serif,system-ui;color:var(--ink);background:var(--paper)}}
main{{max-width:1080px;margin:auto;padding:34px 22px 72px}}a{{color:var(--teal)}}.hero{{background:linear-gradient(135deg,#102a2b,#17484a);color:white;padding:42px;border-radius:24px;box-shadow:0 18px 50px #16383b28}}
.eyebrow{{letter-spacing:.12em;text-transform:uppercase;color:#90e0d2;font-weight:750;font-size:.78rem}}h1{{font-size:clamp(2rem,5vw,4rem);line-height:1.03;margin:.3rem 0 1rem}}h2{{margin-top:2.6rem;font-size:1.75rem}}h3{{margin:.2rem 0}}.lead{{max-width:780px;color:#d8eeee;font-size:1.08rem}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:24px}}.card,.panel{{background:white;border:1px solid var(--line);border-radius:18px;padding:22px}}.card{{color:var(--ink)}}.card strong{{display:block;color:var(--teal);font-size:1.7rem}}.panel{{margin-top:16px}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:680px}}th,td{{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line)}}th{{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}}code{{background:#e8efed;padding:.15em .35em;border-radius:5px}}img{{max-width:100%;height:auto}}.note{{color:var(--muted);font-size:.92rem}}.back{{display:inline-block;margin-bottom:18px;color:#d8eeee}}@media(max-width:720px){{.cards{{grid-template-columns:1fr}}.hero{{padding:28px}}}}
</style></head><body><main>
<section class="hero"><a class="back" href="../index.html">← 返回项目总览</a><div class="eyebrow">Controlled agent evaluation · E4 / E5 / E6</div>
<h1>从“搜一次”到“知道还缺什么”</h1><p class="lead">8 个冻结的合成金融问题，验证结构化证据槽、回答/拒答闸门，以及检索预算的收益递减。所有结果都由仓库脚本生成，不调用外部 LLM。</p>
<div class="cards"><div class="card"><strong>{_percent(gap[-1]["answer_accuracy"])}</strong>E4 structured planner accuracy</div><div class="card"><strong>{_percent(gate[-1]["correct_abstention_rate"])}</strong>E5 correct abstention</div><div class="card"><strong>{next(row["budget"] for row in budget if float(row["answer_accuracy"]) == 1.0)} calls</strong>E6 first 100% budget</div></div></section>
<h2>E4 · Structured Gap Planner</h2><div class="panel"><h3>问题先编译成证据槽，再逐槽检索</h3><p>对照组是原问题单次检索和 query rewrite；实验组显式列出公司、指标、期间、单位及计算操作，并只为尚未满足的槽继续检索。</p>{gap_table}</div>
<h2>E5 · Sufficiency Gate</h2><div class="panel"><h3>缺证据或证据冲突时，拒答也是正确行为</h3><p>每个问题派生 complete / partial / empty / conflict 四种证据条件。闸门只在全部槽具备同期间、同单位、时间可见且无冲突的 final 证据时放行。</p>{gate_table}<p class="note">“Unsupported answers”指在本应拒答的 partial、empty 或 conflict 条件下仍回答；不是通用 LLM 幻觉率。</p></div>
<h2>E6 · Budget Sweep</h2><div class="panel"><h3>充分即停，预算上限不等于实际消耗</h3><p>扫描 1 / 2 / 3 / 5 / 8 次最大检索调用。两槽问题在 2 次停止，四槽问题在 4 次停止，因此更高预算不再增加成本。</p><img src="budget-curve.svg" alt="Budget sweep curve">{budget_table}<p class="note">Cost proxy 是固定公式（每次检索 120 + 文档词数×4），用于相对比较，不代表 API token 账单或线上延迟。</p></div>
<h2>复现与下载</h2><div class="panel"><p><code>python -m advanced_rag.evaluate_agentic</code></p><p><a href="summary.json">JSON 摘要</a> · <a href="e4_gap_detail.csv">E4 逐题</a> · <a href="e5_sufficiency_detail.csv">E5 逐题</a> · <a href="e6_budget_detail.csv">E6 逐题</a></p><p>仓库内的原始结果位于 <code>advanced_rag/results/agentic/</code>。合成数据只用于机制验证，不宣称代表真实发行人事实或生产流量。</p></div>
</main></body></html>'''
    (site_dir / "index.html").write_text(page, encoding="utf-8")


def evaluate(output_dir: Path = DEFAULT_OUTPUT, site_dir: Path = DEFAULT_SITE) -> dict[str, object]:
    cases = load_cases()
    gap, gap_detail = run_gap_ablation(cases)
    gate, gate_detail = run_sufficiency_gate(cases)
    budget, budget_detail = run_budget_sweep(cases)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "e4_gap_ablation.csv", gap)
    _write_csv(output_dir / "e4_gap_detail.csv", gap_detail)
    _write_csv(output_dir / "e5_sufficiency_gate.csv", gate)
    _write_csv(output_dir / "e5_sufficiency_detail.csv", gate_detail)
    _write_csv(output_dir / "e6_budget_sweep.csv", budget)
    _write_csv(output_dir / "e6_budget_detail.csv", budget_detail)
    summary = {
        "protocol": {
            "dataset": "agentic-evidence-v1",
            "cases": len(cases),
            "e5_conditions_per_case": 4,
            "budgets": list(BUDGETS),
            "one_shot_top_k": ONE_SHOT_TOP_K,
            "external_llm_calls": 0,
            "cost_proxy": "120 per retrieval call + max(12, whitespace-token-count * 4) per returned document",
        },
        "e4_gap_planner": gap,
        "e5_sufficiency_gate": gate,
        "e6_budget_sweep": budget,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Agentic evaluation results (E4-E6)\n\n"
        "Generated by `python -m advanced_rag.evaluate_agentic`. The fixture contains eight "
        "synthetic financial cases. E5 expands each case into four evidence conditions. "
        "The cost proxy is deterministic and is not an API billing estimate.\n",
        encoding="utf-8",
    )
    _write_site(site_dir, gap, gate, budget)
    for filename in (
        "e4_gap_ablation.csv",
        "e4_gap_detail.csv",
        "e5_sufficiency_gate.csv",
        "e5_sufficiency_detail.csv",
        "e6_budget_sweep.csv",
        "e6_budget_detail.csv",
        "summary.json",
    ):
        shutil.copy2(output_dir / filename, site_dir / filename)
    return summary


def main() -> int:
    summary = evaluate()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
