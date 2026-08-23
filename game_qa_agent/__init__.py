"""Deterministic match-3 QA engine and auditable Agent skill workflow."""

from .engine import (
    Board,
    CascadeLimitError,
    Match3Engine,
    MoveResult,
    Position,
    board_hash,
    board_to_rows,
    parse_board,
)
from .workflow import AgentBudgetExceeded, GameQAWorkflow, SkillExecutionError

__all__ = [
    "AgentBudgetExceeded",
    "Board",
    "CascadeLimitError",
    "GameQAWorkflow",
    "Match3Engine",
    "MoveResult",
    "Position",
    "SkillExecutionError",
    "board_hash",
    "board_to_rows",
    "parse_board",
]
