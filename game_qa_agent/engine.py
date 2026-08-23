from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Sequence


EMPTY = "."
DEFAULT_SYMBOLS = ("A", "B", "C", "D", "E")
Position = tuple[int, int]
Board = tuple[tuple[str, ...], ...]


class CascadeLimitError(RuntimeError):
    """Raised when a refill loop does not settle within the configured limit."""


def parse_board(rows: Sequence[str | Sequence[str]]) -> Board:
    """Validate and freeze a rectangular board.

    String rows are convenient for fixtures (``"ABC"``); lists are convenient
    for JSON tool calls. Cells are deliberately one-character tokens so traces
    stay compact and language-neutral.
    """

    if isinstance(rows, (str, bytes)) or not rows:
        raise ValueError("board must be a non-empty sequence of rows")
    normalized: list[tuple[str, ...]] = []
    width: int | None = None
    for row in rows:
        cells = tuple(row) if isinstance(row, str) else tuple(str(cell) for cell in row)
        if not cells:
            raise ValueError("board rows must not be empty")
        if any(len(cell) != 1 for cell in cells):
            raise ValueError("each board cell must be a one-character token")
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            raise ValueError("board must be rectangular")
        normalized.append(cells)
    if len(normalized) < 3 or (width or 0) < 3:
        raise ValueError("board must be at least 3x3")
    return tuple(normalized)


def board_to_rows(board: Board) -> list[str]:
    return ["".join(row) for row in board]


def board_hash(board: Board) -> str:
    payload = json.dumps(board_to_rows(board), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


class _XorShift32:
    """Small cross-language reproducible PRNG for replayable refills."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF or 0x6D2B79F5

    def next(self) -> int:
        value = self.state
        value ^= (value << 13) & 0xFFFFFFFF
        value ^= value >> 17
        value ^= (value << 5) & 0xFFFFFFFF
        self.state = value & 0xFFFFFFFF
        return self.state

    def choice(self, values: Sequence[str]) -> str:
        return values[self.next() % len(values)]


@dataclass(frozen=True)
class MoveResult:
    valid: bool
    reason: str
    swap: tuple[Position, Position]
    seed: int
    board_before: Board
    board_after: Board
    cascades: int = 0
    cleared_total: int = 0
    score: int = 0
    events: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "swap": [list(position) for position in self.swap],
            "seed": self.seed,
            "board_before": board_to_rows(self.board_before),
            "board_after": board_to_rows(self.board_after),
            "cascades": self.cascades,
            "cleared_total": self.cleared_total,
            "score": self.score,
            "events": list(self.events),
        }


class Match3Engine:
    """Pure match-3 rules with seeded refill and deterministic event traces."""

    def __init__(
        self,
        *,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        max_cascades: int = 32,
    ) -> None:
        self.symbols = tuple(symbols)
        if len(self.symbols) < 3 or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must contain at least three unique values")
        if EMPTY in self.symbols or any(len(symbol) != 1 for symbol in self.symbols):
            raise ValueError("symbols must be unique one-character non-empty tokens")
        if max_cascades <= 0:
            raise ValueError("max_cascades must be positive")
        self.max_cascades = max_cascades

    def validate(self, board: Board) -> None:
        frozen = parse_board(board)
        allowed = set(self.symbols) | {EMPTY}
        unknown = sorted({cell for row in frozen for cell in row if cell not in allowed})
        if unknown:
            raise ValueError(f"board contains unknown symbols: {unknown}")

    def find_matches(self, board: Board) -> frozenset[Position]:
        self.validate(board)
        height, width = len(board), len(board[0])
        matches: set[Position] = set()

        for row in range(height):
            start = 0
            for column in range(1, width + 1):
                if column < width and board[row][column] == board[row][start]:
                    continue
                if board[row][start] != EMPTY and column - start >= 3:
                    matches.update((row, item) for item in range(start, column))
                start = column

        for column in range(width):
            start = 0
            for row in range(1, height + 1):
                if row < height and board[row][column] == board[start][column]:
                    continue
                if board[start][column] != EMPTY and row - start >= 3:
                    matches.update((item, column) for item in range(start, row))
                start = row
        return frozenset(matches)

    def legal_moves(self, board: Board) -> tuple[tuple[Position, Position], ...]:
        self.validate(board)
        height, width = len(board), len(board[0])
        moves: list[tuple[Position, Position]] = []
        for row in range(height):
            for column in range(width):
                origin = (row, column)
                for target in ((row, column + 1), (row + 1, column)):
                    if target[0] >= height or target[1] >= width:
                        continue
                    if self._creates_match(board, origin, target):
                        moves.append((origin, target))
        return tuple(moves)

    def play(
        self,
        board: Board,
        first: Position,
        second: Position,
        *,
        seed: int,
    ) -> MoveResult:
        self.validate(board)
        self._validate_position(board, first)
        self._validate_position(board, second)
        before = parse_board(board)
        swap = (first, second)
        if self._distance(first, second) != 1:
            return MoveResult(False, "positions_are_not_adjacent", swap, seed, before, before)
        if before[first[0]][first[1]] == before[second[0]][second[1]]:
            return MoveResult(False, "identical_symbols", swap, seed, before, before)

        current = self._swap(before, first, second)
        initial_matches = self.find_matches(current)
        if not initial_matches.intersection({first, second}):
            return MoveResult(False, "swap_creates_no_match", swap, seed, before, before)

        rng = _XorShift32(seed)
        events: list[dict[str, Any]] = [
            {
                "type": "swap",
                "from": list(first),
                "to": list(second),
                "board_hash": board_hash(current),
            }
        ]
        cleared_total = 0
        cascade = 0
        matches = initial_matches
        while matches:
            cascade += 1
            if cascade > self.max_cascades:
                raise CascadeLimitError(
                    f"board did not settle after {self.max_cascades} cascades"
                )
            cleared_total += len(matches)
            cleared = self._clear(current, matches)
            collapsed = self._collapse(cleared)
            current = self._refill(collapsed, rng)
            events.append(
                {
                    "type": "cascade",
                    "index": cascade,
                    "cleared": len(matches),
                    "positions": [list(position) for position in sorted(matches)],
                    "board_hash": board_hash(current),
                }
            )
            matches = self.find_matches(current)

        score = sum(
            int(event["cleared"]) * 100 * int(event["index"])
            for event in events
            if event["type"] == "cascade"
        )
        events.append(
            {
                "type": "settled",
                "cascades": cascade,
                "cleared_total": cleared_total,
                "score": score,
                "board_hash": board_hash(current),
            }
        )
        return MoveResult(
            True,
            "ok",
            swap,
            seed,
            before,
            current,
            cascade,
            cleared_total,
            score,
            tuple(events),
        )

    def analyze(self, board: Board) -> dict[str, Any]:
        self.validate(board)
        moves = self.legal_moves(board)
        matches = self.find_matches(board)
        counts = {
            symbol: sum(cell == symbol for row in board for cell in row)
            for symbol in self.symbols
        }
        occupied = sum(counts.values())
        probabilities = [count / occupied for count in counts.values() if count and occupied]
        entropy = -sum(value * math.log2(value) for value in probabilities)
        normalized_entropy = entropy / math.log2(len(self.symbols)) if probabilities else 0.0

        flags: list[str] = []
        if matches:
            flags.append("board_contains_unresolved_matches")
        if not moves:
            flags.append("dead_board")
        elif len(moves) <= 2:
            flags.append("low_mobility")
        if normalized_entropy < 0.75:
            flags.append("unbalanced_symbol_distribution")

        # This is a transparent QA triage proxy, not a claim about player difficulty.
        mobility_pressure = max(0.0, min(1.0, (8 - len(moves)) / 8))
        balance_pressure = 1.0 - normalized_entropy
        difficulty_proxy = round(100 * (0.75 * mobility_pressure + 0.25 * balance_pressure), 1)
        return {
            "rows": len(board),
            "columns": len(board[0]),
            "board_hash": board_hash(board),
            "existing_match_cells": len(matches),
            "legal_move_count": len(moves),
            "legal_moves": [[list(first), list(second)] for first, second in moves],
            "dead_board": not moves,
            "normalized_symbol_entropy": round(normalized_entropy, 6),
            "difficulty_proxy": difficulty_proxy,
            "risk_flags": flags,
        }

    def _creates_match(self, board: Board, first: Position, second: Position) -> bool:
        if board[first[0]][first[1]] == board[second[0]][second[1]]:
            return False
        swapped = self._swap(board, first, second)
        return bool(self.find_matches(swapped).intersection({first, second}))

    @staticmethod
    def _swap(board: Board, first: Position, second: Position) -> Board:
        mutable = [list(row) for row in board]
        mutable[first[0]][first[1]], mutable[second[0]][second[1]] = (
            mutable[second[0]][second[1]],
            mutable[first[0]][first[1]],
        )
        return tuple(tuple(row) for row in mutable)

    @staticmethod
    def _clear(board: Board, positions: Iterable[Position]) -> Board:
        mutable = [list(row) for row in board]
        for row, column in positions:
            mutable[row][column] = EMPTY
        return tuple(tuple(row) for row in mutable)

    @staticmethod
    def _collapse(board: Board) -> Board:
        height, width = len(board), len(board[0])
        columns: list[list[str]] = []
        for column in range(width):
            remaining = [board[row][column] for row in range(height) if board[row][column] != EMPTY]
            columns.append([EMPTY] * (height - len(remaining)) + remaining)
        return tuple(
            tuple(columns[column][row] for column in range(width))
            for row in range(height)
        )

    def _refill(self, board: Board, rng: _XorShift32) -> Board:
        return tuple(
            tuple(rng.choice(self.symbols) if cell == EMPTY else cell for cell in row)
            for row in board
        )

    @staticmethod
    def _distance(first: Position, second: Position) -> int:
        return abs(first[0] - second[0]) + abs(first[1] - second[1])

    @staticmethod
    def _validate_position(board: Board, position: Position) -> None:
        if (
            len(position) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in position)
            or not (0 <= position[0] < len(board))
            or not (0 <= position[1] < len(board[0]))
        ):
            raise ValueError(f"position out of bounds: {position}")
