from __future__ import annotations

import json
from pathlib import Path

from agent_devtools import Step, TraceWriter, new_run
from agent_devtools.local import doctor, import_new_traces, initialize_workspace, record_external_audit
from agent_devtools.store import TraceStore


def _write_trace(directory: Path, run_id: str = "local-run") -> None:
    trace = new_run("local test")
    trace.run.id = run_id
    step = Step(type="custom", name="record")
    step.complete(output="ok", duration_ms=1)
    trace.add_step(step)
    trace.run.complete(status="success", final_output="ok", duration_ms=1)
    TraceWriter(directory).write(trace)


def test_initialize_workspace_creates_config_and_doctor_reports_ready(tmp_path: Path) -> None:
    config = initialize_workspace(tmp_path)

    assert config.trace_dir == tmp_path / "traces"
    assert config.db_path == tmp_path / ".agent-devtools" / "traces.db"
    assert config.config_path.exists()
    assert json.loads(config.config_path.read_text(encoding="utf-8"))["version"] == 1
    samples = list(config.trace_dir.glob("*.trace.json"))
    assert len(samples) == 1
    sample = json.loads(samples[0].read_text(encoding="utf-8"))
    assert sample["run"]["labels"]["source"] == "agent-devtools-example"
    assert doctor(tmp_path).ready is True


def test_import_new_traces_skips_unchanged_files(tmp_path: Path) -> None:
    config = initialize_workspace(tmp_path)
    store = TraceStore(config.db_path, redaction=True)
    assert len(import_new_traces(config, store)) == 1
    _write_trace(config.trace_dir)

    assert import_new_traces(config, store) == ["local-run"]
    assert import_new_traces(config, store) == []


def test_import_new_traces_skips_malformed_files_without_blocking_valid_traces(tmp_path: Path) -> None:
    config = initialize_workspace(tmp_path)
    store = TraceStore(config.db_path, redaction=True)
    import_new_traces(config, store)
    (config.trace_dir / "broken.trace.json").write_text("{not-json", encoding="utf-8")
    _write_trace(config.trace_dir)

    assert import_new_traces(config, store) == ["local-run"]
    assert import_new_traces(config, store) == []


def test_record_external_audit_persists_only_explicit_visible_events(tmp_path: Path) -> None:
    config = initialize_workspace(tmp_path)
    trace = record_external_audit(
        config,
        task="Codex audit",
        events=[{"name": "run command", "status": "success", "output": "ok"}],
    )

    assert trace.run.labels["capture_scope"] == "external-audit-only"
    assert trace.steps[0].name == "run command"
    assert len(list(config.trace_dir.glob("*.trace.json"))) == 2
