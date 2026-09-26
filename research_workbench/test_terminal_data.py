"""Temporal counterexamples and independent whole-artifact source checks."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import unittest

from research_workbench.terminal_data import (
    ARCHIVE_DIR, DEFAULT_CUBE, build_cube, canonical, evidence_gate,
    load_cube, load_sources, query_metric, select_state,
)


def row(value, year=2023, *, filed=None, accession=None, start=None, end=None, **extras):
    return {"val": value, "start": start or f"{year-1}-07-01", "end": end or f"{year}-06-30",
            "filed": filed or f"{year}-07-27", "accn": accession or f"0000789019-{str(year)[2:]}-000001",
            "fy": year, "fp": "FY", "form": "10-K", **extras}


def fixture(entries):
    payload = {"cik": 789019, "facts": {"us-gaap": {tag: {"units": {"USD": rows}} for tag, rows in entries.items()}}}
    metadata = {"retrieved_at": "2026-09-22T12:00:00+00:00", "canonical_payload_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
                "upstream_response_sha256": "synthetic", "source_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"}
    return build_cube({"MSFT": payload}, {"sources": {"MSFT": metadata}})


def independent_round(value):
    with localcontext() as context:
        context.prec = 70
        return format((Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


class TerminalTemporalTests(unittest.TestCase):
    def test_date_only_filing_waits_until_next_day(self):
        cube = fixture({"Revenues": [row(100)]})
        self.assertEqual(query_metric(cube, "MSFT", "2023-07-27", 2023, "revenue")["status"], "unavailable_as_of")
        value = query_metric(cube, "MSFT", "2023-07-28", 2023, "revenue")
        self.assertEqual(value["value"], "100")
        self.assertTrue(evidence_gate(cube, value["evidence_ids"], "2023-07-28")["passed"])
        self.assertFalse(evidence_gate(cube, value["evidence_ids"], "2023-07-27")["passed"])

    def test_later_comparative_restatement_does_not_backfill_history(self):
        cube = fixture({"Revenues": [row(100), row(110, 2024, start="2022-07-01", end="2023-06-30"), row(140, 2024)]})
        before = query_metric(cube, "MSFT", "2024-01-01", 2023, "revenue")
        after = query_metric(cube, "MSFT", "2024-08-01", 2023, "revenue")
        self.assertEqual(before["value"], "100")
        self.assertEqual(after["value"], "110")
        self.assertNotEqual(before["annual_id"], after["annual_id"])

    def test_instant_balances_use_matching_fiscal_end_without_duration(self):
        balance = row(200)
        balance.pop("start")
        off_period = {**balance, "end": "2023-03-31", "val": 999}
        cube = fixture({"Revenues": [row(100)], "Assets": [balance, off_period]})
        value = query_metric(cube, "MSFT", "2023-08-01", 2023, "assets")
        self.assertEqual((value["value"], value["period_start"], value["period_end"]), ("200", None, "2023-06-30"))

    def test_conflicting_same_date_facts_abstain(self):
        cube = fixture({"Revenues": [row(100), row(101)], "OperatingIncomeLoss": [row(10)]})
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "revenue")["status"], "conflict")
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "operating_margin")["status"], "conflict")

    def test_replacement_tag_conflict_is_not_hidden_by_tag_preference(self):
        cube = fixture({"Revenues": [row(100)], "RevenueFromContractWithCustomerExcludingAssessedTax": [row(110)]})
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "revenue")["status"], "conflict")

    def test_comparative_fy_is_not_period_fy(self):
        cube = fixture({"Revenues": [row(100, 2024, start="2022-01-31", end="2023-01-29", filed="2024-02-21"),
                                    row(140, 2024, start="2023-01-30", end="2024-01-28", filed="2024-02-21")]})
        self.assertEqual(query_metric(cube, "MSFT", "2024-03-01", 2023, "revenue")["value"], "100")
        self.assertEqual(query_metric(cube, "MSFT", "2024-03-01", 2024, "revenue")["value"], "140")

    def test_nonannual_and_quarterly_forms_never_enter_cube(self):
        cube = fixture({"Revenues": [row(100), row(999, start="2023-04-01"), row(888, form="10-Q"), row(777, form="8-K")]})
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "revenue")["value"], "100")

    def test_zero_or_negative_denominators_are_typed(self):
        cube = fixture({"Revenues": [row(100)], "NetIncomeLoss": [row(-5)], "NetCashProvidedByUsedInOperatingActivities": [row(10)]})
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "cash_conversion")["status"], "unsupported")
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "net_margin")["value"], "-5.00")

    def test_future_metric_missing_and_unknown_are_distinct(self):
        cube = fixture({"Revenues": [row(100), row(140, 2024)], "ResearchAndDevelopmentExpense": [row(3, 2024, start="2022-07-01", end="2023-06-30")]})
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "research_and_development")["status"], "unavailable_as_of")
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "capital_expenditure")["status"], "unknown")
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "trading_price")["status"], "unsupported")

    def test_mixed_accession_arithmetic_abstains(self):
        cube = fixture({"Revenues": [row(100), row(110, 2024, start="2022-07-01", end="2023-06-30"), row(140, 2024)], "OperatingIncomeLoss": [row(10)]})
        self.assertEqual(query_metric(cube, "MSFT", "2024-08-01", 2023, "operating_margin")["status"], "unknown")
        self.assertIn("版本", query_metric(cube, "MSFT", "2024-08-01", 2023, "operating_margin")["reason"])

    def test_gross_profit_is_explicit_derived_fallback(self):
        cube = fixture({"Revenues": [row(100)], "CostOfRevenue": [row(60)]})
        gross = query_metric(cube, "MSFT", "2023-08-01", 2023, "gross_profit")
        self.assertEqual(gross["value"], "40")
        self.assertEqual(gross["operation"], "subtract")
        self.assertEqual(len(gross["evidence_ids"]), 2)
        self.assertEqual(query_metric(cube, "MSFT", "2023-08-01", 2023, "gross_margin")["value"], "40.00")

    def test_ratio_changes_use_unrounded_operands(self):
        older = {"start": "2022-07-01", "end": "2023-06-30"}
        cube = fixture({"Revenues": [row(6, 2024, **older), row(3, 2024)], "OperatingIncomeLoss": [row(1, 2024, **older), row(1, 2024)]})
        latest = select_state(cube, "MSFT", "2024-08-01")["annuals"][-1]
        self.assertEqual(latest["changes"]["operating_margin"]["value"], "16.67")
        self.assertEqual(latest["amount_changes"]["revenue"]["value"], "-3")

    def test_nonpositive_growth_base_refuses_percent_but_allows_amount(self):
        older = {"start": "2022-07-01", "end": "2023-06-30"}
        cube = fixture({"Revenues": [row(100, 2024, **older), row(120, 2024)], "NetIncomeLoss": [row(-5, 2024, **older), row(5, 2024)]})
        latest = select_state(cube, "MSFT", "2024-08-01")["annuals"][-1]
        self.assertEqual(latest["changes"]["net_income"]["status"], "unsupported")
        self.assertEqual(latest["amount_changes"]["net_income"]["value"], "10")

    def test_captured_snapshot_does_not_claim_later_completeness(self):
        cube = fixture({"Revenues": [row(100)]})
        self.assertEqual(query_metric(cube, "MSFT", "2026-09-23", 2023, "revenue")["status"], "unknown")

    def test_gate_rejects_empty_unknown_and_wrong_issuer_citations(self):
        cube = fixture({"Revenues": [row(100)]})
        ref = next(iter(cube["evidence"]))
        self.assertFalse(evidence_gate(cube, [], "2023-08-01")["passed"])
        self.assertFalse(evidence_gate(cube, ["made-up"], "2023-08-01")["passed"])
        self.assertFalse(evidence_gate(cube, [ref], "2023-08-01", ticker="AAPL")["passed"])

    def test_post_evaluation_cash_to_assets_contract_regression(self):
        cash, assets = row(25), row(200)
        cash.pop("start")
        assets.pop("start")
        cube = fixture({"Revenues": [row(100)], "CashAndCashEquivalentsAtCarryingValue": [cash], "Assets": [assets]})
        value = query_metric(cube, "MSFT", "2023-08-01", 2023, "cash_to_assets")
        self.assertEqual((value["status"], value["value"], value["period_start"]), ("available", "12.50", None))

    def test_post_evaluation_future_fiscal_year_is_time_unavailable(self):
        cube = fixture({"Revenues": [row(100)]})
        self.assertEqual(query_metric(cube, "MSFT", "2025-04-01", 2027, "revenue")["status"], "unavailable_as_of")
        self.assertEqual(query_metric(cube, "MSFT", "2025-04-01", 2010, "revenue")["status"], "unsupported")


class TerminalArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cube = load_cube()
        cls.payloads, cls.manifest = load_sources()

    def test_every_citation_matches_exact_frozen_source_row(self):
        for item in self.cube["evidence"].values():
            with self.subTest(ref=item["id"]):
                original = self.payloads[item["ticker"]]["facts"]["us-gaap"][item["taxonomy_tag"]]["units"][item["unit"]]
                self.assertIn(item["raw_row"], original)
                self.assertEqual(Fraction(item["value"]), Fraction(str(item["raw_row"]["val"])))
                self.assertEqual(item["available_from"], (date.fromisoformat(item["filed"]) + timedelta(days=1)).isoformat())
                fingerprint = hashlib.sha256(canonical({"taxonomy": "us-gaap", "tag": item["taxonomy_tag"], "unit": "USD", "row": item["raw_row"]})).hexdigest()
                self.assertEqual(item["fact_sha256"], fingerprint)
                self.assertEqual(item["canonical_payload_sha256"], self.manifest["sources"][item["ticker"]]["canonical_payload_sha256"])

    def test_no_event_references_future_evidence(self):
        for ticker, company in self.cube["companies"].items():
            for event in company["events"]:
                for identifier in event["annual_ids"]:
                    annual = company["annuals"][identifier]
                    for section in ["metrics", "changes", "amount_changes"]:
                        for metric in annual[section].values():
                            for ref in metric["evidence_ids"]:
                                item = self.cube["evidence"][ref]
                                self.assertLessEqual(item["available_from"], event["available_from"])
                                self.assertEqual(item["ticker"], ticker)

    def test_all_displayed_values_independently_recalculate(self):
        catalog = self.cube["metric_catalog"]
        for company in self.cube["companies"].values():
            for annual in company["annuals"].values():
                for metric_id, item in annual["metrics"].items():
                    if item["status"] != "available":
                        self.assertIsNone(item["value"])
                        continue
                    if item["operation"] == "reported":
                        value = Fraction(str(self.cube["evidence"][item["evidence_ids"][0]]["raw_row"]["val"]))
                    else:
                        a, b = [Fraction(annual["metrics"][key]["exact_value"]) for key in item["inputs"]]
                        value = a - b if item["operation"] == "subtract" else a / b * (100 if item["unit"] == "%" else 1)
                        rows = [self.cube["evidence"][ref] for ref in item["evidence_ids"]]
                        self.assertEqual(len({row["accession"] for row in rows}), 1)
                    self.assertEqual(Fraction(item["exact_value"]), value)
                    expected = str(value.numerator) if item["unit"] == "USD" and value.denominator == 1 else independent_round(value)
                    self.assertEqual(item["value"], expected)
                    required = {ref for key in item["inputs"] for ref in annual["metrics"][key]["evidence_ids"]}
                    self.assertEqual(set(item["evidence_ids"]), required)

    def test_cube_rebuild_is_byte_deterministic(self):
        rebuilt = build_cube(self.payloads, self.manifest)
        self.assertEqual(canonical(rebuilt) + b"\n", DEFAULT_CUBE.read_bytes())


if __name__ == "__main__":
    unittest.main()
