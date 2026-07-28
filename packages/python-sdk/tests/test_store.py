"""Tests for the local SQLite trace store."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import types
from pathlib import Path

import pytest

from agent_devtools import Cost, PostgresTraceStore, Step, TraceStore, create_trace_store, new_run


def _trace(task: str = "Find customer") :
    trace = new_run(task, labels={"env": "test"})
    trace.run.complete(status="success", final_output="done", duration_ms=120)
    step = Step(
        type="model_call",
        name="llm",
        model="gpt-4.1-mini",
        cost=Cost(input_tokens=10, output_tokens=5, total_tokens=15, amount_usd=0.00002),
    )
    step.complete(output="done", duration_ms=100)
    trace.add_step(step)
    return trace


def test_trace_store_upserts_and_lists_trace_summaries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        trace = _trace()

        store.upsert_trace(trace, source_path="traces/run.trace.json")
        rows = store.list_traces()

    assert len(rows) == 1
    assert rows[0].run_id == trace.run.id
    assert rows[0].task == "Find customer"
    assert rows[0].step_count == 1
    assert rows[0].total_tokens == 15
    assert rows[0].source_path == "traces/run.trace.json"


def test_trace_store_lists_recently_active_trace_first() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        older_session = _trace("Original Claude prompt")
        older_session.run.labels["source"] = "claude-code-http-hooks"
        older_session.run.started_at = "2026-07-26T00:00:00Z"
        older_session.run.ended_at = "2026-07-26T00:00:01Z"
        older_session.steps[0].started_at = "2026-07-26T00:00:00Z"
        older_session.steps[0].ended_at = "2026-07-26T00:00:01Z"
        followup = Step(type="custom", name="User prompt", input="Latest Claude prompt in old session")
        followup.started_at = "2026-07-28T00:00:00Z"
        followup.complete()
        followup.ended_at = "2026-07-28T00:00:01Z"
        older_session.add_step(followup)
        newer_session = _trace("Newer started run")
        newer_session.run.started_at = "2026-07-27T00:00:00Z"
        newer_session.run.ended_at = "2026-07-27T00:00:01Z"
        newer_session.steps[0].started_at = "2026-07-27T00:00:00Z"
        newer_session.steps[0].ended_at = "2026-07-27T00:00:01Z"

        store.upsert_trace(older_session)
        store.upsert_trace(newer_session)

        rows = store.list_traces()

    assert [row.task for row in rows[:2]] == ["Latest Claude prompt in old session", "Newer started run"]


def test_trace_store_promotes_latest_claude_prompt_for_imported_traces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        trace = new_run("Original Claude prompt", labels={"source": "claude-code-http-hooks"})
        first = Step(type="custom", name="User prompt", input="Original Claude prompt", replayable=True)
        first.complete()
        second = Step(type="custom", name="User prompt", input="Latest Claude prompt", replayable=True)
        second.complete()
        trace.add_step(first)
        trace.add_step(second)

        store.upsert_trace(trace)
        rows = store.list_traces()
        loaded = store.get_trace(trace.run.id)

    assert rows[0].task == "Latest Claude prompt"
    assert loaded.run.task == "Latest Claude prompt"


def test_trace_store_roundtrips_full_trace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        trace = _trace()

        store.upsert_trace(trace)
        loaded = store.get_trace(trace.run.id)

    assert loaded.run.id == trace.run.id
    assert loaded.steps[0].name == "llm"


def test_trace_store_rejects_non_finite_trace_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        trace = _trace()
        trace.run.final_output = math.nan

        with pytest.raises(ValueError):
            store.upsert_trace(trace)


def test_trace_store_imports_trace_file_and_searches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        trace = _trace("Refund customer order")
        source = Path(tmp) / "refund.trace.json"
        source.write_text(json.dumps(trace.to_dict()), encoding="utf-8")
        store = TraceStore(Path(tmp) / "traces.db")

        run_id = store.import_file(source)
        rows = store.search("refund")

    assert run_id == trace.run.id
    assert len(rows) == 1
    assert rows[0].task == "Refund customer order"


def test_trace_store_search_applies_limit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        for index in range(5):
            store.upsert_trace(_trace(f"Refund customer order {index}"))

        rows = store.search("Refund", limit=2)

    assert len(rows) == 2


def test_trace_store_search_escapes_like_wildcards() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        literal = _trace("Discount 100% confirmed")
        wildcard_match = _trace("Discount 100x confirmed")
        store.upsert_trace(literal)
        store.upsert_trace(wildcard_match)

        rows = store.search("100%")

    assert [row.run_id for row in rows] == [literal.run.id]


def test_trace_store_returns_none_for_missing_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")

        loaded = store.get_trace("missing")

    assert loaded is None


def test_trace_store_redacts_automatically_when_env_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DEVTOOLS_REDACT_ON_WRITE", "true")
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        trace = _trace("Email alice@example.com")
        trace.steps[0].input = {"api_key": "sk-live-secret123"}

        store.upsert_trace(trace)
        loaded = store.get_trace(trace.run.id)

    assert loaded.run.task == "Email [REDACTED]"
    assert loaded.steps[0].input["api_key"] == "[REDACTED]"


def test_create_trace_store_uses_postgres_when_database_url_is_set(monkeypatch) -> None:
    fake = _install_fake_psycopg(monkeypatch)

    store = create_trace_store(database_url="postgresql://agent:secret@db.example/prod")

    assert isinstance(store, PostgresTraceStore)
    assert store.location == "postgresql://agent:***@db.example/prod"
    assert fake.connect_calls[0]["dsn"] == "postgresql://agent:secret@db.example/prod"


def test_postgres_trace_store_upserts_lists_and_loads_trace(monkeypatch) -> None:
    fake = _install_fake_psycopg(monkeypatch)
    store = PostgresTraceStore("postgresql://agent:secret@db.example/prod")
    trace = _trace("Postgres trace")

    run_id = store.upsert_trace(trace, source_path="traces/postgres.trace.json")
    rows = store.list_traces()
    loaded = store.get_trace(trace.run.id)

    assert run_id == trace.run.id
    assert rows[0].run_id == trace.run.id
    assert rows[0].source_path == "traces/postgres.trace.json"
    assert loaded is not None
    assert loaded.run.id == trace.run.id
    assert any("%s" in call["query"] for call in fake.execute_calls)
    assert all("Postgres trace" not in call["query"] for call in fake.execute_calls)


def test_postgres_trace_store_reports_missing_psycopg(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "psycopg.rows", None)

    with pytest.raises(RuntimeError, match="psycopg"):
        PostgresTraceStore("postgresql://agent:secret@db.example/prod")


class _FakePsycopg:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.connect_calls: list[dict[str, object]] = []
        self.execute_calls: list[dict[str, object]] = []

    def connect(self, dsn: str, row_factory=None):
        self.connect_calls.append({"dsn": dsn, "row_factory": row_factory})
        return _FakePostgresConnection(self)


class _FakePostgresConnection:
    def __init__(self, driver: _FakePsycopg) -> None:
        self.driver = driver

    def execute(self, query: str, params: tuple[object, ...] = ()):
        self.driver.execute_calls.append({"query": query, "params": params})
        normalized = " ".join(query.lower().split())
        if normalized.startswith("insert into traces"):
            row = {
                "run_id": params[0],
                "task": params[1],
                "status": params[2],
                "started_at": params[3],
                "duration_ms": params[4],
                "step_count": params[5],
                "total_tokens": params[6],
                "cost_usd": params[7],
                "source_path": params[8],
                "trace_json": params[9],
                "imported_at": params[10],
            }
            self.driver.rows[str(params[0])] = row
            return _FakePostgresCursor([])
        if normalized.startswith("select trace_json from traces where run_id"):
            row = self.driver.rows.get(str(params[0]))
            return _FakePostgresCursor([{"trace_json": row["trace_json"]}] if row else [])
        if normalized.startswith("select run_id"):
            rows = sorted(self.driver.rows.values(), key=lambda row: (str(row["imported_at"]), str(row["run_id"])), reverse=True)
            return _FakePostgresCursor(rows[: int(params[-1])])
        return _FakePostgresCursor([])

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakePostgresCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _install_fake_psycopg(monkeypatch) -> _FakePsycopg:
    fake = _FakePsycopg()
    rows_module = types.SimpleNamespace(dict_row=object())
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows_module)
    return fake
