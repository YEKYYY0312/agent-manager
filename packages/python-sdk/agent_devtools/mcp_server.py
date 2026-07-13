"""Small stdio MCP server for local Agent DevTools trace queries."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from .analysis import analyze
from .experiment import compare_experiment
from .local import LocalWorkspace, import_new_traces, record_external_audit
from .store import TraceStore

TOOLS = [
    {"name": "list_recent_traces", "description": "List locally recorded traces.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "analyze_trace", "description": "Analyze one recorded trace by run ID.", "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "string"}}}},
    {"name": "compare_traces", "description": "Compare two recorded traces by run ID.", "inputSchema": {"type": "object", "required": ["left_run_id", "right_run_id"], "properties": {"left_run_id": {"type": "string"}, "right_run_id": {"type": "string"}}}},
    {"name": "record_external_audit", "description": "Record explicit visible external operations only.", "inputSchema": {"type": "object", "required": ["task", "events"], "properties": {"task": {"type": "string"}, "events": {"type": "array"}}}},
]


def handle_request(request: dict[str, Any], config: LocalWorkspace) -> dict[str, Any]:
    request_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        return _result(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "agent-devtools", "version": "0.1.0"}})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method != "tools/call":
        return _error(request_id, -32601, "Method not found")
    params = request.get("params", {})
    name = params.get("name")
    arguments = params.get("arguments", {})
    store = TraceStore(config.db_path, redaction=True)
    import_new_traces(config, store)
    try:
        if name == "list_recent_traces":
            limit = max(1, min(int(arguments.get("limit", 20)), 100))
            payload = [asdict(row) for row in store.list_traces(limit=limit)]
        elif name == "analyze_trace":
            trace = _required_trace(store, str(arguments.get("run_id", "")))
            payload = asdict(analyze(trace))
        elif name == "compare_traces":
            left = _required_trace(store, str(arguments.get("left_run_id", "")))
            right = _required_trace(store, str(arguments.get("right_run_id", "")))
            payload = asdict(compare_experiment(left, right))
        elif name == "record_external_audit":
            trace = record_external_audit(config, task=str(arguments.get("task", "")), events=list(arguments.get("events", [])))
            payload = {"run_id": trace.run.id, "capture_scope": trace.run.labels["capture_scope"]}
        else:
            return _error(request_id, -32602, f"Unknown tool: {name}")
    except (TypeError, ValueError) as exc:
        return _error(request_id, -32602, str(exc))
    return _result(request_id, {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]})


def serve(config: LocalWorkspace) -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle_request(request, config)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError:
            print(json.dumps(_error(None, -32700, "Parse error")), flush=True)
    return 0


def _required_trace(store: TraceStore, run_id: str):
    trace = store.get_trace(run_id)
    if trace is None:
        raise ValueError(f"Trace not found: {run_id}")
    return trace


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
