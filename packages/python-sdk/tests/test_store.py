"""Tests for the local SQLite trace store."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent_devtools import Cost, Step, TraceStore, new_run


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


def test_trace_store_roundtrips_full_trace() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TraceStore(Path(tmp) / "traces.db")
        trace = _trace()

        store.upsert_trace(trace)
        loaded = store.get_trace(trace.run.id)

    assert loaded.run.id == trace.run.id
    assert loaded.steps[0].name == "llm"


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
