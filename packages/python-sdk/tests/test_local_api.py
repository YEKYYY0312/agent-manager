"""Tests for the loopback local Trace API."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

from agent_devtools import Step, new_run
from agent_devtools.local import initialize_workspace
from agent_devtools.local_api import create_server
from agent_devtools.writer import TraceWriter


def _write_trace(path: Path) -> str:
    trace = new_run("Local API trace")
    step = Step(type="tool_call", name="local.command", input={"token": "secret-value"})
    step.complete(status="success", output="ok")
    trace.add_step(step)
    trace.run.complete(status="success", final_output="done")
    TraceWriter(path, redaction=True).write(trace)
    return trace.run.id


def test_loopback_api_imports_lists_and_reads_redacted_traces(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path)
    run_id = _write_trace(workspace.trace_dir)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(f"{base_url}/api/health") as response:
            health = json.loads(response.read())
        assert health["ok"] is True
        assert health["trace_count"] == 2

        with urlopen(f"{base_url}/api/traces") as response:
            listing = json.loads(response.read())
        assert {item["run_id"] for item in listing["traces"]} == {"agent-devtools-example", run_id}

        with urlopen(f"{base_url}/api/traces/{run_id}") as response:
            trace = json.loads(response.read())
        assert trace["run"]["id"] == run_id
        assert trace["steps"][0]["input"]["token"] == "[REDACTED]"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
