"""Local SQLite storage for trace files."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Iterable

from .redaction import RedactionConfig, redact_trace
from .trace import Trace


@dataclass(frozen=True)
class StoredTraceSummary:
    run_id: str
    task: str
    status: str
    started_at: str
    duration_ms: float | None
    step_count: int
    total_tokens: int
    cost_usd: float
    source_path: str


class TraceStore:
    """A small local SQLite index for `.trace.json` files."""

    def __init__(self, db_path: str | Path = ".agent-devtools/traces.db", redaction: bool | RedactionConfig | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.redaction = _normalize_redaction(redaction)
        self._init_db()

    def upsert_trace(self, trace: Trace, source_path: str | Path = "") -> str:
        trace = self._to_storable_trace(trace)
        total = trace.total_cost()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO traces (
                  run_id, task, status, started_at, duration_ms, step_count,
                  total_tokens, cost_usd, source_path, trace_json, imported_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  task=excluded.task,
                  status=excluded.status,
                  started_at=excluded.started_at,
                  duration_ms=excluded.duration_ms,
                  step_count=excluded.step_count,
                  total_tokens=excluded.total_tokens,
                  cost_usd=excluded.cost_usd,
                  source_path=excluded.source_path,
                  trace_json=excluded.trace_json,
                  imported_at=excluded.imported_at
                """,
                (
                    trace.run.id,
                    trace.run.task,
                    trace.run.status,
                    trace.run.started_at,
                    trace.run.duration_ms,
                    len(trace.steps),
                    total.total_tokens,
                    total.amount_usd,
                    str(source_path) if source_path else "",
                    json.dumps(trace.to_dict(), ensure_ascii=False, default=str),
                    _utc_now(),
                ),
            )
        return trace.run.id

    def import_file(self, path: str | Path) -> str:
        trace_path = Path(path)
        trace = Trace.from_file(str(trace_path))
        return self.upsert_trace(trace, source_path=trace_path)

    def import_files(self, paths: Iterable[str | Path]) -> list[str]:
        return [self.import_file(path) for path in paths]

    def list_traces(self, query: str | None = None, limit: int = 100) -> list[StoredTraceSummary]:
        if query:
            return self.search(query, limit=limit)
        limit = _safe_limit(limit)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, task, status, started_at, duration_ms, step_count,
                       total_tokens, cost_usd, source_path
                FROM traces
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_summary(row) for row in rows]

    def search(self, query: str, limit: int = 100) -> list[StoredTraceSummary]:
        limit = _safe_limit(limit)
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, task, status, started_at, duration_ms, step_count,
                       total_tokens, cost_usd, source_path
                FROM traces
                WHERE run_id LIKE ?
                   OR task LIKE ?
                   OR status LIKE ?
                   OR source_path LIKE ?
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, limit),
            ).fetchall()
        return [_summary(row) for row in rows]

    def get_trace(self, run_id: str) -> Trace | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT trace_json FROM traces WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return Trace.from_dict(json.loads(row["trace_json"]))

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                  run_id TEXT PRIMARY KEY,
                  task TEXT NOT NULL,
                  status TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  duration_ms REAL,
                  step_count INTEGER NOT NULL,
                  total_tokens INTEGER NOT NULL,
                  cost_usd REAL NOT NULL,
                  source_path TEXT NOT NULL,
                  trace_json TEXT NOT NULL,
                  imported_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_task ON traces(task)")

    def _to_storable_trace(self, trace: Trace) -> Trace:
        if self.redaction is None:
            return trace
        return redact_trace(trace, self.redaction)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _summary(row: sqlite3.Row) -> StoredTraceSummary:
    return StoredTraceSummary(
        run_id=row["run_id"],
        task=row["task"],
        status=row["status"],
        started_at=row["started_at"],
        duration_ms=row["duration_ms"],
        step_count=row["step_count"],
        total_tokens=row["total_tokens"],
        cost_usd=row["cost_usd"],
        source_path=row["source_path"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_limit(value: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 100
    return max(1, min(limit, 1000))


def _normalize_redaction(redaction: bool | RedactionConfig | None) -> RedactionConfig | None:
    if redaction is True:
        return RedactionConfig()
    if isinstance(redaction, RedactionConfig):
        return redaction
    if redaction is None and _env_redaction_enabled():
        return RedactionConfig()
    return None


def _env_redaction_enabled() -> bool:
    return os.getenv("AGENT_DEVTOOLS_REDACT_ON_WRITE", "").strip().lower() in {"1", "true", "yes", "on"}
