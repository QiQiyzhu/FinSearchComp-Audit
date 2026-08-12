from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .controller import AtlasRAG
from .dataset import benchmark_labels, benchmark_queries, build_corpus, load_cases
from .models import QuerySpec, RetrievalHit
from .retrieval import HybridTemporalRetriever
from .router import AdaptiveRouter


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "results"


def reciprocal_rank(hits: list[RetrievalHit], relevant_ids: set[str]) -> float:
    for hit in hits:
        if hit.document.document_id in relevant_ids:
            return 1.0 / hit.rank
    return 0.0


def recall_at(hits: list[RetrievalHit], relevant_ids: set[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    retrieved = {hit.document.document_id for hit in hits[:k]}
    return len(retrieved & relevant_ids) / len(relevant_ids)


def evaluate(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    cases = load_cases()
    labels = benchmark_labels(cases)
    corpus = build_corpus(cases)
    queries = benchmark_queries(cases)
    router = AdaptiveRouter()
    retriever = HybridTemporalRetriever(corpus, router=router)
    atlas = AtlasRAG(retriever, router=router)

    output_dir.mkdir(parents=True, exist_ok=True)
    modes = ("bm25", "hybrid", "temporal")
    retrieval_rows: list[dict[str, Any]] = []
    query_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []

    for query, clean_case in queries:
        plan = router.plan(query)
        relevant_ids = {
            document_id
            for document_id, label in labels.items()
            if label["base_question_id"] == query.query_id
            and label["supports_gold"]
            and not label["is_perturbed"]
        }
        mode_hits: dict[str, list[RetrievalHit]] = {}
        for mode in modes:
            hits = retriever.retrieve(
                query,
                plan,
                limit=10,
                mode=mode,
                corrective=False,
            )
            mode_hits[mode] = hits
            retrieval_rows.append(
                {
                    "query_id": query.query_id,
                    "method": mode,
                    "recall_at_5": recall_at(hits, relevant_ids, 5),
                    "recall_at_10": recall_at(hits, relevant_ids, 10),
                    "mrr_at_10": reciprocal_rank(hits, relevant_ids),
                    "future_at_5": sum(
                        labels[hit.document.document_id]["is_perturbed"]
                        and hit.document.published_at > query.cutoff_date
                        for hit in hits[:5]
                    )
                    / min(5, len(hits)),
                    "preferred_source_at_5": sum(
                        hit.document.source_authority in plan.preferred_authorities
                        for hit in hits[:5]
                    )
                    / min(5, len(hits)),
                    "top_ids": ";".join(
                        hit.document.document_id for hit in hits[:5]
                    ),
                }
            )

        decision = atlas.answer(query)
        expected_answer = str(clean_case["gold_answer"])
        query_rows.append(
            {
                "query_id": query.query_id,
                "complexity": plan.complexity,
                "preferred_authorities": ";".join(plan.preferred_authorities),
                "action": decision.action,
                "answer_correct": (
                    decision.action == "answer"
                    and decision.answer_value == expected_answer
                    and decision.unit == query.canonical_unit
                ),
                "confidence": decision.confidence,
                "selected_evidence_id": decision.selected_evidence_id or "",
                "corrective_retrieval": decision.used_corrective_retrieval,
                "conflict_count": len(decision.conflicts),
            }
        )
        trace_rows.append(decision.to_dict())

    retrieval_summary: dict[str, dict[str, float]] = {}
    for mode in modes:
        rows = [row for row in retrieval_rows if row["method"] == mode]
        retrieval_summary[mode] = {
            metric: sum(float(row[metric]) for row in rows) / len(rows)
            for metric in (
                "recall_at_5",
                "recall_at_10",
                "mrr_at_10",
                "future_at_5",
                "preferred_source_at_5",
            )
        }

    answers = [row for row in query_rows if row["action"] == "answer"]
    system_summary = {
        "queries": len(query_rows),
        "decision_accuracy": sum(row["answer_correct"] for row in query_rows)
        / len(query_rows),
        "coverage": len(answers) / len(query_rows),
        "selective_accuracy": (
            sum(row["answer_correct"] for row in answers) / len(answers)
            if answers
            else 0.0
        ),
        "corrective_retrieval_rate": sum(
            row["corrective_retrieval"] for row in query_rows
        )
        / len(query_rows),
        "mean_confidence": sum(row["confidence"] for row in query_rows)
        / len(query_rows),
    }

    _write_csv(output_dir / "retrieval_per_query.csv", retrieval_rows)
    _write_csv(output_dir / "system_per_query.csv", query_rows)
    _write_csv(
        output_dir / "retrieval_summary.csv",
        [
            {"method": method, **metrics}
            for method, metrics in retrieval_summary.items()
        ],
    )
    _write_csv(output_dir / "system_summary.csv", [system_summary])
    (output_dir / "traces.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in trace_rows),
        encoding="utf-8",
    )
    _write_report(output_dir / "README.md", retrieval_summary, system_summary)
    return {"retrieval": retrieval_summary, "system": system_summary}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_report(
    path: Path,
    retrieval: dict[str, dict[str, float]],
    system: dict[str, Any],
) -> None:
    names = {"bm25": "BM25", "hybrid": "Hybrid RRF", "temporal": "ATLAS temporal"}
    lines = [
        "# ATLAS-RAG 离线检索与决策结果",
        "",
        "> 这是基于 20 个真实金融问题和 100 个唯一证据文档的确定性离线实验，不是实时 Web Search，也不是论文原方法复现。",
        "",
        "## 检索层",
        "",
        "| 方法 | Recall@5 | Recall@10 | MRR@10 | Top-5 未来证据率 | Top-5 路由来源率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method, metrics in retrieval.items():
        lines.append(
            f"| {names[method]} | {metrics['recall_at_5']:.1%} | "
            f"{metrics['recall_at_10']:.1%} | {metrics['mrr_at_10']:.3f} | "
            f"{metrics['future_at_5']:.1%} | {metrics['preferred_source_at_5']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## 端到端决策层",
            "",
            f"- 问题数：{system['queries']}；",
            f"- 决策准确率：{system['decision_accuracy']:.1%}；",
            f"- 回答覆盖率：{system['coverage']:.1%}；",
            f"- 已回答样本准确率：{system['selective_accuracy']:.1%}；",
            f"- 触发纠错检索比例：{system['corrective_retrieval_rate']:.1%}；",
            "",
            "## 解释边界",
            "",
            "- 检索模块只读取问题、证据正文和运行时元数据；不会读取 `gold_answer`、`supports_gold` 或 `is_perturbed`；",
            "- 评测脚本才读取隐藏标签计算 Recall、MRR、未来证据率和最终正确率；",
            "- 当前 HashVector 是无模型下载的确定性向量基线，不能冒充训练得到的 dense embedding；",
            "- 下一阶段应在独立真实网页语料和多次实网运行上验证泛化。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Evaluate ATLAS-RAG offline")
    value.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return value


def main() -> None:
    args = parser().parse_args()
    result = evaluate(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
