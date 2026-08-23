from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping, Sequence

from .engine import Board, CascadeLimitError, Match3Engine, MoveResult, Position, board_hash, board_to_rows, parse_board


class SkillExecutionError(ValueError):
    """Raised when an Agent asks for an unknown or malformed skill call."""


class AgentBudgetExceeded(RuntimeError):
    """Raised before a skill runs when its declared cost exceeds the budget."""


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    cost: int
    mutates_state: bool
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "cost": self.cost,
            "mutates_state": self.mutates_state,
            "input_schema": json.loads(json.dumps(self.input_schema)),
        }


class GameQAWorkflow:
    """Budgeted, allow-listed skill runtime around the deterministic game oracle.

    An LLM may produce the JSON plan, but it never decides whether a move is
    legal and cannot call arbitrary Python. Every accepted skill call is
    replayable from the initial board, arguments, seed, and resulting hashes.
    """

    def __init__(
        self,
        board: Board | Sequence[str | Sequence[str]],
        *,
        seed: int = 20270813,
        budget: int = 12,
        engine: Match3Engine | None = None,
    ) -> None:
        if budget <= 0:
            raise ValueError("budget must be positive")
        self.engine = engine or Match3Engine()
        self.initial_board = parse_board(board)
        self.engine.validate(self.initial_board)
        self.board = self.initial_board
        self.seed = seed
        self.initial_budget = budget
        self.remaining_budget = budget
        self.trace: list[dict[str, Any]] = []
        self._applied_moves: list[MoveResult] = []
        self._skills: dict[str, tuple[SkillSpec, Callable[[dict[str, Any]], Any]]] = {}
        self._register_default_skills()

    def skill_catalog(self) -> list[dict[str, Any]]:
        return [self._skills[name][0].to_dict() for name in sorted(self._skills)]

    def execute(self, skill: str, arguments: Mapping[str, Any] | None = None) -> Any:
        args = dict(arguments or {})
        if skill not in self._skills:
            raise SkillExecutionError(f"skill is not allow-listed: {skill}")
        spec, handler = self._skills[skill]
        before_hash = board_hash(self.board)
        step = len(self.trace) + 1
        if spec.cost > self.remaining_budget:
            self.trace.append(
                {
                    "step": step,
                    "skill": skill,
                    "arguments": self._json_safe(args),
                    "status": "rejected",
                    "reason": "budget_exceeded",
                    "cost": spec.cost,
                    "remaining_budget": self.remaining_budget,
                    "before_hash": before_hash,
                    "after_hash": before_hash,
                }
            )
            raise AgentBudgetExceeded(
                f"skill {skill} costs {spec.cost}, only {self.remaining_budget} remains"
            )

        try:
            result = handler(args)
        except (CascadeLimitError, SkillExecutionError, TypeError, ValueError) as error:
            self.trace.append(
                {
                    "step": step,
                    "skill": skill,
                    "arguments": self._json_safe(args),
                    "status": "error",
                    "reason": type(error).__name__,
                    "detail": str(error),
                    "cost": 0,
                    "remaining_budget": self.remaining_budget,
                    "before_hash": before_hash,
                    "after_hash": board_hash(self.board),
                }
            )
            raise

        self.remaining_budget -= spec.cost
        event = {
            "step": step,
            "skill": skill,
            "arguments": self._json_safe(args),
            "status": "ok",
            "cost": spec.cost,
            "remaining_budget": self.remaining_budget,
            "before_hash": before_hash,
            "after_hash": board_hash(self.board),
            "result": self._result_summary(result),
        }
        self.trace.append(event)
        return result

    def run_plan(self, actions: Sequence[Mapping[str, Any]]) -> list[Any]:
        if isinstance(actions, (str, bytes)):
            raise SkillExecutionError("actions must be a sequence of objects")
        outputs: list[Any] = []
        for action in actions:
            if not isinstance(action, Mapping):
                raise SkillExecutionError("each action must be an object")
            unexpected = set(action) - {"skill", "arguments"}
            if unexpected:
                raise SkillExecutionError(f"unexpected action fields: {sorted(unexpected)}")
            if "skill" not in action:
                raise SkillExecutionError("action is missing skill")
            arguments = action.get("arguments", {})
            if not isinstance(arguments, Mapping):
                raise SkillExecutionError("arguments must be an object")
            outputs.append(self.execute(str(action["skill"]), arguments))
        return outputs

    def export_trace(self) -> dict[str, Any]:
        return {
            "schema_version": "game-qa-trace-1.0",
            "initial_board": board_to_rows(self.initial_board),
            "initial_board_hash": board_hash(self.initial_board),
            "final_board": board_to_rows(self.board),
            "final_board_hash": board_hash(self.board),
            "seed": self.seed,
            "initial_budget": self.initial_budget,
            "remaining_budget": self.remaining_budget,
            "events": json.loads(json.dumps(self.trace)),
        }

    def _register_default_skills(self) -> None:
        no_arguments = {"type": "object", "properties": {}, "additionalProperties": False}
        swap_schema = {
            "type": "object",
            "required": ["first", "second"],
            "properties": {
                "first": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                "second": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                "seed": {"type": "integer"},
            },
            "additionalProperties": False,
        }
        self._register(
            SkillSpec("inspect_board", "Inspect board mobility, balance, and QA risk flags.", 1, False, no_arguments),
            self._inspect_board,
        )
        self._register(
            SkillSpec("list_legal_moves", "Return all adjacent swaps accepted by the rules oracle.", 1, False, no_arguments),
            self._list_legal_moves,
        )
        self._register(
            SkillSpec("simulate_swap", "Simulate a seeded swap without mutating session state.", 2, False, swap_schema),
            self._simulate_swap,
        )
        self._register(
            SkillSpec("apply_swap", "Apply a seeded legal swap and record it for deterministic replay.", 3, True, swap_schema),
            self._apply_swap,
        )
        self._register(
            SkillSpec("verify_replay", "Replay all applied moves from the initial board and compare every hash.", 2, False, no_arguments),
            self._verify_replay,
        )
        self._register(
            SkillSpec("build_qa_report", "Build a cross-functional board report with deterministic move samples.", 3, False, no_arguments),
            self._build_qa_report,
        )

    def _register(self, spec: SkillSpec, handler: Callable[[dict[str, Any]], Any]) -> None:
        if spec.name in self._skills or spec.cost <= 0:
            raise ValueError(f"invalid or duplicate skill: {spec.name}")
        self._skills[spec.name] = (spec, handler)

    def _inspect_board(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_keys(arguments, set())
        return self.engine.analyze(self.board)

    def _list_legal_moves(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_keys(arguments, set())
        moves = self.engine.legal_moves(self.board)
        return {
            "board_hash": board_hash(self.board),
            "count": len(moves),
            "moves": [[list(first), list(second)] for first, second in moves],
        }

    def _simulate_swap(self, arguments: dict[str, Any]) -> dict[str, Any]:
        first, second, seed = self._parse_swap(arguments)
        return self.engine.play(self.board, first, second, seed=seed).to_dict()

    def _apply_swap(self, arguments: dict[str, Any]) -> dict[str, Any]:
        first, second, seed = self._parse_swap(arguments)
        result = self.engine.play(self.board, first, second, seed=seed)
        if not result.valid:
            raise SkillExecutionError(f"cannot apply invalid move: {result.reason}")
        self.board = result.board_after
        self._applied_moves.append(result)
        return result.to_dict()

    def _verify_replay(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_keys(arguments, set())
        current = self.initial_board
        verified: list[dict[str, Any]] = []
        for index, original in enumerate(self._applied_moves, start=1):
            replayed = self.engine.play(current, *original.swap, seed=original.seed)
            expected_hash = board_hash(original.board_after)
            actual_hash = board_hash(replayed.board_after)
            same_events = replayed.events == original.events
            verified.append(
                {
                    "move": index,
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash,
                    "same_events": same_events,
                }
            )
            if not replayed.valid or actual_hash != expected_hash or not same_events:
                return {"verified": False, "moves": verified}
            current = replayed.board_after
        return {
            "verified": board_hash(current) == board_hash(self.board),
            "moves": verified,
            "final_board_hash": board_hash(current),
        }

    def _build_qa_report(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._expect_keys(arguments, set())
        analysis = self.engine.analyze(self.board)
        moves = self.engine.legal_moves(self.board)
        samples: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for index, (first, second) in enumerate(moves):
            try:
                result = self.engine.play(
                    self.board,
                    first,
                    second,
                    seed=(self.seed + index) & 0xFFFFFFFF,
                )
                samples.append(
                    {
                        "swap": [list(first), list(second)],
                        "cleared_total": result.cleared_total,
                        "cascades": result.cascades,
                        "score": result.score,
                        "final_board_hash": board_hash(result.board_after),
                    }
                )
            except CascadeLimitError as error:
                failures.append(
                    {"swap": [list(first), list(second)], "reason": str(error)}
                )
        cleared_values = [sample["cleared_total"] for sample in samples]
        return {
            "schema_version": "game-qa-report-1.0",
            "board": board_to_rows(self.board),
            "analysis": analysis,
            "move_sample_count": len(samples),
            "clear_range": (
                [min(cleared_values), max(cleared_values)] if cleared_values else None
            ),
            "moves": samples,
            "simulation_failures": failures,
            "interpretation": {
                "client": "Use hashes and events to reproduce rule or cascade defects.",
                "product": "Use mobility and the transparent difficulty proxy for triage, then validate with player data.",
                "qa": "Turn every failing seed and swap into a permanent regression fixture.",
                "ai": "The Agent selects allow-listed skills; the deterministic oracle owns pass/fail.",
            },
        }

    def _parse_swap(self, arguments: dict[str, Any]) -> tuple[Position, Position, int]:
        self._expect_keys(arguments, {"first", "second", "seed"}, required={"first", "second"})
        first = self._position(arguments["first"], "first")
        second = self._position(arguments["second"], "second")
        seed = arguments.get("seed", self.seed + len(self._applied_moves))
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise SkillExecutionError("seed must be an integer")
        return first, second, seed

    @staticmethod
    def _position(value: Any, name: str) -> Position:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            raise SkillExecutionError(f"{name} must be [row, column]")
        return value[0], value[1]

    @staticmethod
    def _expect_keys(
        arguments: dict[str, Any],
        allowed: set[str],
        *,
        required: set[str] | None = None,
    ) -> None:
        unexpected = set(arguments) - allowed
        missing = (required or set()) - set(arguments)
        if unexpected:
            raise SkillExecutionError(f"unexpected arguments: {sorted(unexpected)}")
        if missing:
            raise SkillExecutionError(f"missing arguments: {sorted(missing)}")

    @staticmethod
    def _result_summary(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            keys = (
                "valid",
                "reason",
                "count",
                "verified",
                "board_hash",
                "final_board_hash",
                "move_sample_count",
                "clear_range",
            )
            return {key: result[key] for key in keys if key in result}
        return {"type": type(result).__name__}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False))
