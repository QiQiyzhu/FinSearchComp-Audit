from decimal import Decimal
import json
import unittest
from unittest.mock import MagicMock, patch

from research_workbench.answers import build_answers, compute_metric
from research_workbench.config import Settings
from research_workbench.engine import ResearchEngine, plan_question, synthesize, validate_citations
from research_workbench.sources import Source, SourceClient, digest, fiscal_year_map, select_facts


def run(question, ticker="MSFT", as_of="2024-11-01"):
    return ResearchEngine(Settings()).run({"question": question, "ticker": ticker, "as_of": as_of, "mode": "demo"})


def answer(report, metric, operation="value", year=2024):
    return next(item for item in report["answers"] if item["metric_id"] == metric and item["operation"] == operation and item["fiscal_year"] == year)


def row(value, year, identifier):
    return {"value": str(value), "start": f"{year}-01-01", "end": f"{year}-12-31", "fiscal_year": year, "evidence_id": identifier}


class DirectAnswerPrecisionTests(unittest.TestCase):
    def test_ocf_alone_does_not_require_capex(self):
        report = run("NVIDIA FY2024 经营现金流是多少？", "NVDA")
        self.assertEqual(report["plan"]["requested_metrics"], ["operating_cash_flow"])
        self.assertEqual(report["plan"]["metric_slots"], ["operating_cash_flow"])
        self.assertTrue(report["coverage"]["question_supported"])
        self.assertEqual(answer(report, "operating_cash_flow")["value"], "28090000000")
        self.assertEqual(report["coverage"]["missing_requested_metrics"], [])

    def test_other_cashflow_categories_never_become_operating_cashflow(self):
        for question in ["MSFT FY2024 financing cash flow", "MSFT FY2024 cash flow from investing activities", "微软FY2024筹资活动现金流是多少？"]:
            report = run(question)
            self.assertFalse(any(item["metric_id"] == "operating_cash_flow" for item in report["answers"]))
            self.assertFalse(report["coverage"]["question_supported"])
        mixed = run("MSFT FY2024 operating cash flow and financing cash flow")
        self.assertEqual(answer(mixed, "operating_cash_flow")["answerability"], "answered")
        self.assertTrue(any(item["answerability"] == "unsupported" for item in mixed["answers"]))

    def test_english_rd_percentage_requests_compile_to_ratio(self):
        for question in ["MSFT FY2024 R&D as a percentage of revenue", "What percentage of Microsoft's FY2024 revenue was spent on R&D?"]:
            report = run(question)
            self.assertEqual(report["plan"]["requested_metrics"], ["rd_ratio"])
            self.assertEqual(answer(report, "rd_ratio")["value"], "12.04")
        # Post-evaluation regression: this wording is now a disclosed case.
        apple = run("What percentage of Apple's FY2024 revenue was spent on R&D?", "AAPL")
        self.assertEqual(apple["plan"]["requested_metrics"], ["rd_ratio"])
        self.assertEqual(answer(apple, "rd_ratio")["value"], "8.02")

    def test_how_many_dollars_requests_absolute_growth(self):
        # Post-evaluation grammar repair, intentionally not held-out evidence.
        for ticker, company, delta in [("MSFT", "Microsoft", "33207000000"), ("AAPL", "Apple", "7750000000")]:
            report = run(f"How many dollars did {company}'s revenue increase from FY2023 to FY2024?", ticker)
            result = answer(report, "revenue", "growth_amount")
            self.assertEqual(result["value"], delta)
            self.assertEqual(result["unit"], "USD")
            self.assertEqual(result["answerability"], "answered")

    def test_multi_intent_keeps_missing_fcf_and_valid_ocf_independent(self):
        report = run("NVIDIA FY2024 经营现金流和自由现金流分别是多少？", "NVDA")
        self.assertEqual(answer(report, "operating_cash_flow")["answerability"], "answered")
        self.assertEqual(answer(report, "free_cash_flow")["answerability"], "missing_evidence")
        self.assertFalse(report["coverage"]["question_supported"])
        self.assertIn("28.09", report["summary"])

    def test_growth_percentage_and_absolute_growth_are_distinct(self):
        report = run("MSFT FY2024 相比 FY2023 收入增长多少美元，收入同比增长率是多少？")
        self.assertEqual(answer(report, "revenue", "growth_amount")["value"], "33207000000")
        self.assertEqual(answer(report, "revenue", "growth_amount")["unit"], "USD")
        self.assertEqual(answer(report, "revenue", "growth_pct")["value"], "15.67")
        self.assertEqual(answer(report, "revenue", "growth_pct")["comparison_fiscal_year"], 2023)

    def test_margin_change_is_percentage_points_and_uses_unrounded_inputs(self):
        report = run("MSFT FY2024 营业利润率比 FY2023 提高多少个百分点？")
        result = answer(report, "operating_margin", "change_pp")
        self.assertEqual(result["value"], "2.87")
        self.assertEqual(result["unit"], "percentage_points")
        self.assertEqual(len(result["evidence_ids"]), 4)
        periods = {2024: {"operating_income": row(1, 2024, "E1"), "revenue": row(6, 2024, "E2")},
                   2023: {"operating_income": row(1, 2023, "E3"), "revenue": row(3, 2023, "E4")}}
        plan = {"requests": [{"metric_id": "operating_margin", "operation": "change_pp", "fiscal_years": [2023, 2024]}], "gaps": []}
        result = build_answers(plan, periods, 2024)[0]
        self.assertEqual(result["value"], "-16.67")  # subtracting rounded ratios would incorrectly give -16.66

    def test_new_ratios_have_explicit_definitions_and_exact_dependencies(self):
        report = run("微软FY2024净利润率、研发费用占收入比例和现金转换率分别是多少？")
        self.assertEqual(answer(report, "net_margin")["value"], "35.96")
        self.assertEqual(answer(report, "rd_ratio")["value"], "12.04")
        conversion = answer(report, "cash_conversion")
        self.assertEqual(conversion["value"], "134.51")
        self.assertIn("118548000000 / 88136000000", conversion["formula"])
        self.assertEqual(len(conversion["evidence_ids"]), 2)

    def test_each_metric_keeps_its_requested_year(self):
        report = run("MSFT FY2023 revenue; FY2024 operating cash flow")
        self.assertEqual(answer(report, "revenue", year=2023)["value"], "211915000000")
        self.assertEqual(answer(report, "operating_cash_flow")["value"], "118548000000")
        self.assertFalse(any(item["metric_id"] == "revenue" and item["fiscal_year"] == 2024 for item in report["answers"]))

    def test_unavailable_explicit_year_does_not_hide_available_year(self):
        report = run("MSFT FY2022 revenue; FY2024 operating cash flow")
        self.assertEqual(answer(report, "revenue", year=2022)["answerability"], "missing_evidence")
        self.assertEqual(answer(report, "operating_cash_flow")["answerability"], "answered")

    def test_local_growth_does_not_modify_unrequested_margin_operation(self):
        report = run("分析微软FY2024收入增长与营业利润率。")
        requests = {(item["metric_id"], item["operation"]) for item in report["plan"]["requests"]}
        self.assertIn(("revenue", "growth_pct"), requests)
        self.assertIn(("operating_margin", "value"), requests)
        self.assertNotIn(("operating_margin", "change_pp"), requests)

    def test_shared_trailing_growth_applies_to_both_list_items(self):
        report = run("微软FY2024收入与经营现金流同比增长率分别是多少？")
        self.assertEqual(answer(report, "revenue", "growth_pct")["value"], "15.67")
        self.assertEqual(answer(report, "operating_cash_flow", "growth_pct")["value"], "35.36")

    def test_overview_includes_cashflow_even_when_fcf_is_also_named(self):
        plan = plan_question("分析微软FY2024相比FY2023的收入、盈利与现金流，资本支出增加后还有多少自由现金流？", "MSFT")
        self.assertTrue({"revenue", "operating_margin", "net_margin", "operating_cash_flow", "capital_expenditure", "free_cash_flow"}.issubset(plan["requested_metrics"]))

    def test_more_than_six_requested_metrics_remain_in_direct_answers(self):
        report = run("MSFT FY2024收入、营业利润、净利润、经营现金流、资本支出、研发费用、营业利润率、净利润率、研发强度和现金转换率是多少？")
        self.assertGreater(len(report["answers"]), 6)
        self.assertTrue(all(item["answerability"] == "answered" for item in report["answers"]))
        for item in report["answers"]:
            self.assertIn(item["text"], report["summary"])

    def test_unsupported_metric_does_not_erase_supported_subquestion(self):
        report = run("微软FY2024收入是多少，EBITDA 是多少？")
        self.assertEqual(answer(report, "revenue")["answerability"], "answered")
        self.assertTrue(any(item["answerability"] == "unsupported" for item in report["answers"]))

    def test_nonpositive_growth_base_abstains_but_absolute_delta_works(self):
        periods = {2023: {"net_income": row(-5, 2023, "E1")}, 2024: {"net_income": row(10, 2024, "E2")}}
        tasks = [{"metric_id": "net_income", "operation": op, "fiscal_years": [2023, 2024]} for op in ["growth_pct", "growth_amount"]]
        results = build_answers({"requests": tasks, "gaps": []}, periods, 2024)
        self.assertEqual(results[0]["answerability"], "missing_evidence")
        self.assertIsNone(results[0]["value"])
        self.assertEqual(results[1]["value"], "15")
        self.assertEqual(results[1]["answerability"], "answered")

    def test_nonpositive_ratio_denominator_rejected(self):
        result = compute_metric("cash_conversion", {"operating_cash_flow": row(10, 2024, "E1"), "net_income": row(0, 2024, "E2")})
        self.assertTrue(result["reason"])
        self.assertNotIn("value", result)

    def test_nonadjacent_periods_cannot_be_called_yoy(self):
        report = run("MSFT FY2024 对比 FY2022 收入同比增长率是多少？")
        self.assertEqual(answer(report, "revenue", "growth_pct")["answerability"], "unsupported")

    def test_evidence_findings_have_valid_citations(self):
        report = run("研究微软FY2024经营现金流和资本支出。")
        self.assertTrue(report["strengths"])
        self.assertTrue(report["risk_findings"])
        self.assertTrue(validate_citations(report["strengths"] + report["risk_findings"], report["evidence"], "2024-11-01"))
        self.assertEqual(report["verdict"]["stance"], "mixed")

    def test_reverse_comparison_preserves_direction(self):
        report = run("微软FY2023相比FY2024收入变化额是多少？")
        result = answer(report, "revenue", "growth_amount", year=2023)
        self.assertEqual(result["comparison_fiscal_year"], 2024)
        self.assertEqual(result["value"], "-33207000000")
        self.assertIn("FY2023 相比 FY2024", result["text"])
        fewer = run("微软FY2023比FY2024收入少多少？")
        self.assertEqual(answer(fewer, "revenue", "growth_amount", year=2023)["value"], "-33207000000")

    def test_model_can_cover_more_than_six_without_replacing_direct_answers(self):
        metrics = ["revenue", "operating_income", "net_income", "operating_cash_flow", "capital_expenditure", "research_and_development", "free_cash_flow"]
        claims = [{"id": f"C{i}", "metric_id": metric, "text": "verified", "evidence_ids": [f"E{i}"]} for i, metric in enumerate(metrics)]
        selected = {"claim_ids": [item["id"] for item in claims], "watch_ids": ["cashflow"]}
        response = MagicMock(status_code=200, content=b"small")
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(selected)}}]}
        with patch("research_workbench.engine.httpx.Client") as factory:
            factory.return_value.__enter__.return_value.post.return_value = response
            result = synthesize(Settings(), "all metrics", {"requested_metrics": metrics}, claims)
        self.assertEqual(len(result["selection"]["claim_ids"]), 7)


class FiscalLabelTests(unittest.TestCase):
    def test_comparative_filing_fy_is_not_the_observation_year(self):
        source = SourceClient(Settings()).snapshot("NVDA")
        facts, evidence, _ = select_facts(source, "NVDA", "2024-11-01", 2023)
        self.assertEqual(facts["revenue"]["fy"], 2024)  # latest comparative disclosure context
        self.assertEqual(facts["revenue"]["fiscal_year"], 2023)
        self.assertEqual(facts["revenue"]["end"], "2023-01-29")
        self.assertEqual(evidence[0]["fiscal_year"], 2023)

    def test_52_week_year_across_calendar_boundary_uses_filing_anchor(self):
        old = {"start": "2021-12-27", "end": "2023-01-01", "filed": "2023-02-01", "fy": 2022, "fp": "FY", "accn": "2022-filing"}
        # 53-week fiscal period ends in the following calendar year.
        self.assertEqual(fiscal_year_map({"revenue": [old]})[(old["start"], old["end"])], 2022)

    def test_ambiguous_fiscal_labels_do_not_guess(self):
        period = {"start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "fp": "FY"}
        rows = [{**period, "fy": 2023, "accn": "a"}, {**period, "fy": 2024, "accn": "b"}]
        self.assertNotIn((period["start"], period["end"]), fiscal_year_map({"revenue": rows}))


if __name__ == "__main__":
    unittest.main()
