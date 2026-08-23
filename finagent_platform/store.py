from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import uuid4


TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
RUN_STATUSES = frozenset({"queued", "running", *TERMINAL_STATUSES})
RUN_KINDS = frozenset({"query", "evaluation"})


class RunNotFound(KeyError):
    pass


class IdempotencyConflict(ValueError):
    pass


class InvalidTransition(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class RunStore:
    """Small SQLite run store with per-operation connections and WAL mode."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    run_key TEXT NOT NULL UNIQUE,
                    parent_run_id TEXT REFERENCES runs(run_id),
                    kind TEXT NOT NULL CHECK(kind IN ('query', 'evaluation')),
                    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed')),
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    result_json TEXT,
                    trace_json TEXT,
                    failure_category TEXT,
                    error_type TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id);
                CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
                """
            )

    def create_or_get(
        self,
        *,
        run_key: str,
        kind: str,
        request: dict[str, Any],
        config: dict[str, Any],
        parent_run_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if kind not in RUN_KINDS:
            raise ValueError(f"unsupported run kind: {kind}")
        request_digest = payload_hash({"kind": kind, "request": request, "config": config})
        now = datetime.now(timezone.utc).isoformat()
        run_id = f"run_{uuid4().hex[:20]}"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM runs WHERE run_key = ?", (run_key,)
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_digest:
                    raise IdempotencyConflict(
                        "idempotency key was already used with a different request"
                    )
                return self._row_to_dict(existing), False
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, run_key, parent_run_id, kind, status, request_hash,
                    request_json, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run_key,
                    parent_run_id,
                    kind,
                    request_digest,
                    canonical_json(request),
                    canonical_json(config),
                    now,
                    now,
                ),
            )
            created = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            return self._row_to_dict(created), True

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def require(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run is None:
            raise RunNotFound(run_id)
        return run

    def transition(
        self,
        run_id: str,
        *,
        from_statuses: Iterable[str],
        to_status: str,
        result: dict[str, Any] | list[Any] | None = None,
        trace: dict[str, Any] | list[Any] | None = None,
        failure_category: str | None = None,
        error_type: str | None = None,
        attempts: int | None = None,
    ) -> dict[str, Any]:
        allowed = tuple(dict.fromkeys(from_statuses))
        if not allowed or any(status not in RUN_STATUSES for status in allowed):
            raise ValueError("from_statuses contains an invalid status")
        if to_status not in RUN_STATUSES:
            raise ValueError(f"invalid target status: {to_status}")
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [to_status, datetime.now(timezone.utc).isoformat()]
        optional = {
            "result_json": canonical_json(result) if result is not None else None,
            "trace_json": canonical_json(trace) if trace is not None else None,
            "failure_category": failure_category,
            "error_type": error_type,
            "attempts": attempts,
        }
        for column, value in optional.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        placeholders = ",".join("?" for _ in allowed)
        values.extend([run_id, *allowed])
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE runs SET {', '.join(assignments)} "
                f"WHERE run_id = ? AND status IN ({placeholders})",
                values,
            )
            if cursor.rowcount != 1:
                current = connection.execute(
                    "SELECT status FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if current is None:
                    raise RunNotFound(run_id)
                raise InvalidTransition(
                    f"cannot transition {run_id} from {current['status']} to {to_status}"
                )
        return self.require(run_id)

    def list_children(self, parent_run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE parent_run_id = ? ORDER BY created_at, run_id",
                (parent_run_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def count(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for column in ("request_json", "config_json", "result_json", "trace_json"):
            raw = value.pop(column)
            value[column.removesuffix("_json")] = json.loads(raw) if raw else None
        return value
