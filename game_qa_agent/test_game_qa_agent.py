from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import unittest

from .demo import load_scenario, run_demo
from .engine import Match3Engine, board_hash, parse_board
from .workflow import AgentBudgetExceeded, GameQAWorkflow, SkillExecutionError


PLAYABLE = parse_board(["BECDC", "CABAB", "DBCBD", "EEADC", "AADBD"])
DEAD = parse_board(["ACBBE", "AACBA", "BBECD", "BAEAD", "EDDAC"])


class Match3EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = Match3Engine(max_cascades=64)

    def test_overlapping_horizontal_and_vertical_matches_are_deduplicated(self) -> None:
        board = parse_board(["BCADB", "CCCCC", "DCADE", "ECADE", "ABDEA"])
        matches = self.engine.find_matches(board)
        self.assertEqual(
            matches,
            frozenset({(1, 0), (1, 1), (1, 2), (1, 3), (1, 4), (0, 1), (2, 1), (3, 1)}),
        )

    def test_legal_moves_are_stable_and_do_not_mutate_board(self) -> None:
        before = board_hash(PLAYABLE)
        first = self.engine.legal_moves(PLAYABLE)
        second = self.engine.legal_moves(PLAYABLE)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)
        self.assertEqual(before, board_hash(PLAYABLE))
        for origin, target in first:
            self.assertEqual(abs(origin[0] - target[0]) + abs(origin[1] - target[1]), 1)

    def test_non_adjacent_and_non_matching_swaps_are_rejected_without_change(self) -> None:
        non_adjacent = self.engine.play(PLAYABLE, (0, 0), (2, 0), seed=1)
        no_match = self.engine.play(PLAYABLE, (0, 0), (0, 1), seed=1)
        self.assertFalse(non_adjacent.valid)
        self.assertEqual(non_adjacent.reason, "positions_are_not_adjacent")
        self.assertFalse(no_match.valid)
        self.assertEqual(no_match.reason, "swap_creates_no_match")
        self.assertEqual(non_adjacent.board_after, PLAYABLE)
        self.assertEqual(no_match.board_after, PLAYABLE)

    def test_seeded_multi_cascade_has_exact_replay_contract(self) -> None:
        first = self.engine.play(PLAYABLE, (3, 3), (4, 3), seed=104)
        second = self.engine.play(PLAYABLE, (3, 3), (4, 3), seed=104)
        self.assertTrue(first.valid)
        self.assertEqual((first.cascades, first.cleared_total, first.score), (3, 12, 2400))
        self.assertEqual(first.board_after, second.board_after)
        self.assertEqual(first.events, second.events)
        self.assertFalse(self.engine.find_matches(first.board_after))

    def test_dead_board_is_reported_without_overclaiming(self) -> None:
        analysis = self.engine.analyze(DEAD)
        self.assertTrue(analysis["dead_board"])
        self.assertEqual(analysis["legal_move_count"], 0)
        self.assertIn("dead_board", analysis["risk_flags"])
        self.assertIn("difficulty_proxy", analysis)

    def test_seeded_property_sweep_accepts_every_reported_move(self) -> None:
        rng = random.Random(2027)
        checked = 0
        attempts = 0
        while checked < 40 and attempts < 2000:
            attempts += 1
            board = parse_board(
                ["".join(rng.choice("ABCDE") for _ in range(5)) for _ in range(5)]
            )
            if self.engine.find_matches(board):
                continue
            for index, move in enumerate(self.engine.legal_moves(board)):
                result = self.engine.play(board, *move, seed=checked * 100 + index + 1)
                self.assertTrue(result.valid)
                self.assertFalse(self.engine.find_matches(result.board_after))
            checked += 1
        self.assertEqual(checked, 40)


class GameQAWorkflowTests(unittest.TestCase):
    def test_catalog_exposes_schema_cost_and_mutation_boundary(self) -> None:
        workflow = GameQAWorkflow(PLAYABLE)
        catalog = {item["name"]: item for item in workflow.skill_catalog()}
        self.assertFalse(catalog["simulate_swap"]["mutates_state"])
        self.assertTrue(catalog["apply_swap"]["mutates_state"])
        self.assertIn("input_schema", catalog["apply_swap"])
        self.assertGreater(catalog["apply_swap"]["cost"], 0)

    def test_plan_is_deterministic_and_replay_is_verified(self) -> None:
        plan = [
            {"skill": "inspect_board"},
            {
                "skill": "apply_swap",
                "arguments": {"first": [3, 3], "second": [4, 3], "seed": 104},
            },
            {"skill": "verify_replay"},
        ]
        first = GameQAWorkflow(PLAYABLE, budget=8)
        second = GameQAWorkflow(PLAYABLE, budget=8)
        first_outputs = first.run_plan(plan)
        second_outputs = second.run_plan(plan)
        self.assertTrue(first_outputs[-1]["verified"])
        self.assertEqual(first.export_trace(), second.export_trace())

    def test_invalid_stateful_call_fails_closed_and_costs_no_budget(self) -> None:
        workflow = GameQAWorkflow(PLAYABLE, budget=5)
        before = board_hash(workflow.board)
        with self.assertRaises(SkillExecutionError):
            workflow.execute(
                "apply_swap", {"first": [0, 0], "second": [0, 1], "seed": 1}
            )
        self.assertEqual(board_hash(workflow.board), before)
        self.assertEqual(workflow.remaining_budget, 5)
        self.assertEqual(workflow.trace[-1]["status"], "error")

    def test_unknown_skill_and_extra_arguments_are_rejected(self) -> None:
        workflow = GameQAWorkflow(PLAYABLE)
        with self.assertRaises(SkillExecutionError):
            workflow.execute("run_arbitrary_python", {"code": "pass"})
        with self.assertRaises(SkillExecutionError):
            workflow.execute("inspect_board", {"hidden": True})
        self.assertEqual(workflow.remaining_budget, workflow.initial_budget)

    def test_budget_rejection_does_not_change_state(self) -> None:
        workflow = GameQAWorkflow(PLAYABLE, budget=3)
        workflow.execute("inspect_board")
        before = board_hash(workflow.board)
        with self.assertRaises(AgentBudgetExceeded):
            workflow.execute(
                "apply_swap", {"first": [3, 3], "second": [4, 3], "seed": 104}
            )
        self.assertEqual(board_hash(workflow.board), before)
        self.assertEqual(workflow.remaining_budget, 2)
        self.assertEqual(workflow.trace[-1]["reason"], "budget_exceeded")

    def test_scenario_contract_and_demo_artifacts(self) -> None:
        scenario = load_scenario("playable-seed-7")
        self.assertEqual(scenario["expected"]["legal_move_count"], 5)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run_demo(output)
            self.assertTrue(result["passed"])
            for name in ("index.html", "report.json", "trace.json", "README.md"):
                self.assertTrue((output / name).is_file())
            trace = json.loads((output / "trace.json").read_text(encoding="utf-8"))
            self.assertEqual(trace["schema_version"], "game-qa-trace-1.0")
            self.assertEqual([event["status"] for event in trace["events"]], ["ok"] * 4)


if __name__ == "__main__":
    unittest.main()
