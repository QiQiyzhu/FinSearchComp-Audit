from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .config import Settings
from .engine import ResearchEngine
from .sources import ResearchError, canonical, digest, now


class AdmissionError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = Path(settings.database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, request_json TEXT NOT NULL,
                    request_hash TEXT NOT NULL, idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    result_json TEXT, error_json TEXT
                );
                CREATE INDEX IF NOT EXISTS research_status ON research_jobs(status);
                CREATE TABLE IF NOT EXISTS research_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    event_json TEXT NOT NULL, FOREIGN KEY(job_id) REFERENCES research_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS research_events_job ON research_events(job_id, sequence);
                CREATE TABLE IF NOT EXISTS research_admissions (
                    job_id TEXT PRIMARY KEY, client_hash TEXT NOT NULL, mode TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_admissions_time ON research_admissions(created_at);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                "request": json.loads(row["request_json"]), "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "error": json.loads(row["error_json"]) if row["error_json"] else None}

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM research_jobs WHERE id=?", (job_id,)).fetchone()
        return self.decode(row) if row else None

    def create(self, request: dict[str, Any], client: str, key: str | None) -> tuple[dict[str, Any], bool]:
        request_hash = digest(request)
        scoped_key = digest({"client": client, "key": key}) if key else None
        stamp = now()
        utc = datetime.now(timezone.utc)
        hour = (utc - timedelta(hours=1)).isoformat(timespec="seconds")
        day = (utc - timedelta(days=1)).isoformat(timespec="seconds")
        retention = (utc - timedelta(days=self.settings.retention_days)).isoformat(timespec="seconds")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if scoped_key:
                row = db.execute("SELECT * FROM research_jobs WHERE idempotency_key=?", (scoped_key,)).fetchone()
                if row:
                    if row["request_hash"] != request_hash:
                        raise AdmissionError(409, "此 Idempotency-Key 已用于不同研究请求。")
                    return self.decode(row), False
            active = db.execute("SELECT COUNT(*) FROM research_jobs WHERE status IN ('queued','running')").fetchone()[0]
            if active >= self.settings.max_pending:
                raise AdmissionError(429, "研究队列已满，请稍后重试。")
            count = db.execute("SELECT COUNT(*) FROM research_admissions WHERE client_hash=? AND created_at>=? AND (mode='demo')=?", (client, hour, request["mode"] == "demo")).fetchone()[0]
            limit = self.settings.demo_requests_per_hour if request["mode"] == "demo" else self.settings.live_requests_per_hour
            if count >= limit:
                raise AdmissionError(429, "已达到每小时研究请求额度，请稍后重试。")
            if request["mode"] != "demo":
                count = db.execute("SELECT COUNT(*) FROM research_admissions WHERE mode!='demo' AND created_at>=?", (day,)).fetchone()[0]
                if count >= self.settings.live_global_per_day:
                    raise AdmissionError(429, "此部署过去 24 小时的实时研究额度已用完，离线演示仍可使用。")
            db.execute("DELETE FROM research_admissions WHERE created_at<?", (day,))
            db.execute("DELETE FROM research_jobs WHERE status IN ('completed','failed') AND created_at<?", (retention,))
            db.execute("DELETE FROM research_jobs WHERE id IN (SELECT id FROM research_jobs WHERE status IN ('completed','failed') ORDER BY created_at DESC LIMIT -1 OFFSET ?)", (self.settings.max_history,))
            identifier = "research_" + uuid4().hex
            db.execute("INSERT INTO research_jobs(id,status,request_json,request_hash,idempotency_key,created_at,updated_at) VALUES(?,'queued',?,?,?,?,?)", (identifier, canonical(request), request_hash, scoped_key, stamp, stamp))
            db.execute("INSERT INTO research_admissions VALUES(?,?,?,?)", (identifier, client, request["mode"], stamp))
            row = db.execute("SELECT * FROM research_jobs WHERE id=?", (identifier,)).fetchone()
            return self.decode(row), True

    def claim(self, identifier: str) -> bool:
        with self.connection() as db:
            return db.execute("UPDATE research_jobs SET status='running',updated_at=? WHERE id=? AND status='queued'", (now(), identifier)).rowcount == 1

    def add_event(self, identifier: str, event: dict[str, Any]) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO research_events(job_id,event_json) VALUES(?,?)", (identifier, canonical(event)))
            db.execute("UPDATE research_jobs SET updated_at=? WHERE id=?", (now(), identifier))

    def events(self, identifier: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT event_json FROM research_events WHERE job_id=? ORDER BY sequence", (identifier,))]

    def complete(self, identifier: str, result: dict[str, Any]) -> None:
        with self.connection() as db:
            db.execute("UPDATE research_jobs SET status='completed',result_json=?,updated_at=? WHERE id=? AND status='running'", (canonical(result), now(), identifier))

    def fail(self, identifier: str, code: str, message: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE research_jobs SET status='failed',error_json=?,updated_at=? WHERE id=? AND status IN ('queued','running')", (canonical({"code": code, "message": message}), now(), identifier))
        self.add_event(identifier, {"step": "failed", "label": "研究未完成", "status": "failed", "detail": message, "timestamp": now()})

    def recover(self) -> list[str]:
        """One process per SQLite deployment; never silently rerun billed work."""
        with self.connection() as db:
            running = [row[0] for row in db.execute("SELECT id FROM research_jobs WHERE status='running'")]
            queued = [row[0] for row in db.execute("SELECT id FROM research_jobs WHERE status='queued' ORDER BY created_at")]
        for identifier in running:
            self.fail(identifier, "PROCESS_INTERRUPTED", "服务在执行期间重启；任务已标记中断，未自动重复计费调用。请手动创建新研究。")
        return queued


class JobService:
    def __init__(self, settings: Settings, engine: ResearchEngine | None = None):
        self.store = JobStore(settings)
        self.engine = engine or ResearchEngine(settings)
        self.executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="research")

    def start(self) -> None:
        for identifier in self.store.recover():
            self.executor.submit(self._run, identifier)

    def submit(self, request: dict[str, Any], client: str, key: str | None) -> dict[str, Any]:
        job, created = self.store.create(request, client, key)
        if created:
            self.executor.submit(self._run, job["id"])
        return job

    def _run(self, identifier: str) -> None:
        if not self.store.claim(identifier):
            return
        job = self.store.get(identifier)
        try:
            result = self.engine.run(job["request"], emit=lambda event: self.store.add_event(identifier, event))
            self.store.complete(identifier, result)
        except ResearchError as exc:
            self.store.fail(identifier, exc.code, exc.message)
        except Exception:
            # Provider bodies, arbitrary exception repr, and configuration never
            # enter public errors or logs. The persisted trace locates failure.
            self.store.fail(identifier, "INTERNAL_ERROR", "研究执行遇到内部错误；已保存此前步骤，可重试或检查服务运行环境。")

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)
