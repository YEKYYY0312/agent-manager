"""Local workspace helpers for automatic trace discovery and audit records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import TraceStore
from .trace import Error, Step, Trace, new_run
from .writer import TraceWriter


@dataclass(frozen=True)
class LocalWorkspace:
    root: Path
    trace_dir: Path
    db_path: Path
    config_path: Path
    state_path: Path


@dataclass(frozen=True)
class DoctorReport:
    ready: bool
    trace_dir: Path
    db_path: Path
    trace_count: int


def initialize_workspace(root: str | Path = ".") -> LocalWorkspace:
    root_path = Path(root).resolve()
    state_dir = root_path / ".agent-devtools"
    trace_dir = root_path / "traces"
    state_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    config = LocalWorkspace(root_path, trace_dir, state_dir / "traces.db", state_dir / "config.json", state_dir / "import-state.json")
    if not config.config_path.exists():
        config.config_path.write_text(json.dumps({"version": 1, "trace_dir": "traces", "db_path": ".agent-devtools/traces.db"}, indent=2) + "\n", encoding="utf-8")
    if not any(trace_dir.glob("*.trace.json")):
        _write_example_trace(trace_dir)
    return config


def doctor(root: str | Path = ".") -> DoctorReport:
    config = initialize_workspace(root)
    try:
        TraceStore(config.db_path)
        ready = config.trace_dir.is_dir() and config.config_path.is_file()
    except OSError:
        ready = False
    return DoctorReport(ready, config.trace_dir, config.db_path, len(list(config.trace_dir.glob("*.trace.json"))))


def import_new_traces(config: LocalWorkspace, store: TraceStore | None = None) -> list[str]:
    trace_store = store or TraceStore(config.db_path, redaction=True)
    fingerprints = _read_state(config.state_path)
    imported: list[str] = []
    for path in sorted(config.trace_dir.glob("*.trace.json")):
        stat = path.stat()
        fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
        if fingerprints.get(path.name) == fingerprint:
            continue
        try:
            trace = Trace.from_file(str(path))
        except (OSError, ValueError):
            # A partial or malformed file must not block discovery of later traces.
            fingerprints[path.name] = fingerprint
            continue
        trace_store.upsert_trace(trace, source_path=path)
        fingerprints[path.name] = fingerprint
        imported.append(trace.run.id)
    _write_state(config.state_path, fingerprints)
    return imported


def record_external_audit(config: LocalWorkspace, *, task: str, events: list[dict[str, Any]]) -> Trace:
    trace = new_run(task, labels={"source": "codex-visible-operations", "capture_scope": "external-audit-only"})
    for event in events:
        status = str(event.get("status", "success"))
        step = Step(type="tool_call", name=str(event.get("name", "visible operation")), input=event.get("input"), replayable=False)
        error = Error(type="ExternalAuditError", message=str(event.get("error", "operation failed"))) if status != "success" else None
        step.complete(status=status, output=event.get("output"), error=error, duration_ms=float(event.get("duration_ms", 0)))
        trace.add_step(step)
    trace.run.complete(status="success", final_output="Recorded explicit visible operations.", duration_ms=sum(float(step.duration_ms or 0) for step in trace.steps))
    TraceWriter(config.trace_dir, redaction=True).write(trace)
    return trace


def _read_state(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and all(isinstance(value, str) for value in data.values()) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: Path, fingerprints: dict[str, str]) -> None:
    path.write_text(json.dumps(fingerprints, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_example_trace(trace_dir: Path) -> None:
    trace = new_run(
        "Explore the Agent DevTools example Trace",
        labels={"source": "agent-devtools-example", "sample": "true"},
    )
    trace.run.id = "agent-devtools-example"
    step = Step(type="planner", name="Review example Trace", replayable=False)
    step.complete(status="success", output="Open this Trace in the local workbench to inspect the run.")
    trace.add_step(step)
    trace.run.complete(status="success", final_output="Example Trace ready.")
    TraceWriter(trace_dir, redaction=True).write(trace)
