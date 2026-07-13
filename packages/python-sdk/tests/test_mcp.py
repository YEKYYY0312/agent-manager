from __future__ import annotations

from pathlib import Path

from agent_devtools import Step, TraceWriter, new_run
from agent_devtools.local import initialize_workspace
from agent_devtools.mcp_server import handle_request


def _seed_trace(directory: Path) -> str:
    trace = new_run("MCP test")
    trace.run.id = "mcp-run"
    step = Step(type="custom", name="seed")
    step.complete(output="ok", duration_ms=2)
    trace.add_step(step)
    trace.run.complete(status="success", final_output="ok", duration_ms=2)
    TraceWriter(directory).write(trace)
    return trace.run.id


def test_mcp_lists_and_analyzes_imported_traces(tmp_path: Path) -> None:
    config = initialize_workspace(tmp_path)
    run_id = _seed_trace(config.trace_dir)

    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_recent_traces", "arguments": {}}}, config)
    assert listed["result"]["content"][0]["type"] == "text"
    assert run_id in listed["result"]["content"][0]["text"]

    analyzed = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "analyze_trace", "arguments": {"run_id": run_id}}}, config)
    assert "all passed" in analyzed["result"]["content"][0]["text"]
