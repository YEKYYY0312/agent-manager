"""Tests for the loopback local Trace API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent_devtools import Step, new_run
import agent_devtools.codex_sessions as codex_sessions
from agent_devtools.codex_sessions import CodexSessionWatcher
from agent_devtools.local import import_new_traces, initialize_workspace
from agent_devtools.local_api import LiveTraceHub, create_server
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


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        assert response.status == 202
        return json.loads(response.read())


def test_live_trace_hub_keeps_only_latest_snapshot_for_each_run() -> None:
    hub = LiveTraceHub(max_events=10)

    hub.publish({"run_id": "run-a", "trace": {"version": 1}})
    hub.publish({"run_id": "run-b", "trace": {"version": 1}})
    latest_id = hub.publish({"run_id": "run-a", "trace": {"version": 2}})

    events = hub.wait_after(0, timeout=0)

    assert [(event_id, payload["run_id"]) for event_id, payload in events] == [
        (2, "run-b"),
        (latest_id, "run-a"),
    ]
    assert events[-1][1]["trace"] == {"version": 2}


def test_codex_watcher_memory_error_does_not_stop_local_api(tmp_path: Path) -> None:
    class FailingWatcher:
        def __init__(self) -> None:
            self.called = threading.Event()

        def poll_once(self) -> None:
            self.called.set()
            raise MemoryError("oversized live trace")

    workspace = initialize_workspace(tmp_path)
    server = create_server(workspace, port=0)
    watcher = FailingWatcher()
    server.codex_watcher = watcher  # type: ignore[assignment]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        assert watcher.called.wait(timeout=1)
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/health", timeout=1) as response:
            health = json.loads(response.read())

        assert health["ok"] is True
        assert thread.is_alive()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _append_jsonl(path: Path, *events: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def _wait_for_trace(base_url: str, run_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/api/traces/{run_id}", timeout=1) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            if exc.code != 404:
                raise
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for Trace {run_id}")


def test_codex_session_jsonl_builds_visible_trace_without_hidden_context(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "workspace")
    sessions_dir = tmp_path / "codex-home" / "sessions"
    rollout = sessions_dir / "2026" / "07" / "28" / "rollout-session-1.jsonl"
    _append_jsonl(
        rollout,
        {
            "timestamp": "2026-07-28T01:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": "session-1",
                "session_id": "session-1",
                "cwd": str(tmp_path),
                "base_instructions": {"text": "hidden system instructions"},
            },
        },
        {
            "timestamp": "2026-07-28T01:00:01Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-1"},
        },
        {
            "timestamp": "2026-07-28T01:00:02Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": (
                    '<in-app-browser-context source="ambient-ui-state">\n'
                    "Current URL: http://127.0.0.1:5175/\n"
                    "</in-app-browser-context>\n\n"
                    "## My request for Codex:\n"
                    "Fix the Codex watcher"
                ),
            },
        },
        {
            "timestamp": "2026-07-28T01:00:03Z",
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": "hidden reasoning"},
        },
        {
            "timestamp": "2026-07-28T01:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "hidden developer message"}],
            },
        },
        {
            "timestamp": "2026-07-28T01:00:05Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "Checking files"}],
            },
        },
        {
            "timestamp": "2026-07-28T01:00:06Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call-1",
                "name": "exec",
                "input": "rg TODO",
                "status": "completed",
            },
        },
        {
            "timestamp": "2026-07-28T01:00:07Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-1",
                "output": [
                    {"type": "input_text", "text": "Script completed"},
                    {"type": "input_text", "text": "src/app.py:12: TODO"},
                ],
            },
        },
        {
            "timestamp": "2026-07-28T01:00:08Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "total_tokens": 150,
                    }
                },
            },
        },
        {
            "timestamp": "2026-07-28T01:00:09Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "last_agent_message": "Watcher fixed",
                "duration_ms": 8000,
            },
        },
    )

    server = create_server(workspace, port=0, codex_sessions_dir=sessions_dir)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        trace = _wait_for_trace(base_url, "codex-session-1")

        assert trace["run"]["task"] == "Fix the Codex watcher"
        assert trace["run"]["final_output"] == "Watcher fixed"
        assert trace["run"]["ended_at"] == "2026-07-28T01:00:09Z"
        assert trace["run"]["labels"] == {
            "source": "codex-session-jsonl",
            "capture_scope": "visible-session-events",
        }
        assert trace["run"]["cost"] == {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "amount_usd": 0.0,
        }
        assert [step["name"] for step in trace["steps"]] == [
            "User prompt",
            "Assistant commentary",
            "exec",
        ]
        assert trace["steps"][2]["tool"]["args"] == "rg TODO"
        assert trace["steps"][2]["tool"]["result"] == "Script completed\nsrc/app.py:12: TODO"
        serialized = json.dumps(trace, ensure_ascii=False)
        assert "hidden reasoning" not in serialized
        assert "hidden system instructions" not in serialized
        assert "hidden developer message" not in serialized
        assert "ambient-ui-state" not in serialized
        assert len(list(workspace.trace_dir.glob("codex-*.trace.json"))) == 1
        assert import_new_traces(workspace) == []
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_codex_session_append_is_published_over_sse(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "workspace")
    sessions_dir = tmp_path / "codex-home" / "sessions"
    rollout = sessions_dir / "2026" / "07" / "28" / "rollout-session-live.jsonl"
    _append_jsonl(
        rollout,
        {
            "timestamp": "2026-07-28T02:00:00Z",
            "type": "session_meta",
            "payload": {"id": "session-live", "session_id": "session-live"},
        },
    )

    server = create_server(workspace, port=0, codex_sessions_dir=sessions_dir)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    received: list[dict[str, object]] = []
    listener_ready = threading.Event()

    def listen() -> None:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/live/traces", timeout=4) as response:
            listener_ready.set()
            while True:
                line = response.readline()
                if line.startswith(b"data: "):
                    received.append(json.loads(line.removeprefix(b"data: ")))
                    return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    try:
        assert listener_ready.wait(timeout=1)
        _append_jsonl(
            rollout,
            {
                "timestamp": "2026-07-28T02:00:01Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "Stream Codex live"},
            },
        )
        listener.join(timeout=3)

        assert received
        assert received[0]["source_event"] == "codex-session-jsonl"
        assert received[0]["run_id"] == "codex-session-live"
        assert received[0]["trace"]["run"]["task"] == "Stream Codex live"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_codex_watcher_does_not_leave_live_trace_for_duplicate_file_import(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path / "workspace")
    sessions_dir = tmp_path / "codex-home" / "sessions"
    rollout = sessions_dir / "2026" / "07" / "28" / "rollout-session-import-state.jsonl"
    _append_jsonl(
        rollout,
        {
            "timestamp": "2026-07-28T03:00:00Z",
            "type": "session_meta",
            "payload": {"id": "session-import-state", "session_id": "session-import-state"},
        },
        {
            "timestamp": "2026-07-28T03:00:01Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Avoid duplicate import"},
        },
    )
    published: list[dict[str, object]] = []
    watcher = CodexSessionWatcher(
        workspace,
        sessions_dir,
        lambda payload: published.append(payload) or len(published),
    )

    watcher.poll_once()

    assert published
    assert "codex-session-import-state" not in import_new_traces(workspace)


def test_codex_watcher_limits_initial_active_file_backfill(tmp_path: Path, monkeypatch) -> None:
    workspace = initialize_workspace(tmp_path / "workspace")
    sessions_dir = tmp_path / "codex-home" / "sessions"
    rollout = sessions_dir / "rollout-00000000-0000-0000-0000-000000000123.jsonl"
    _append_jsonl(
        rollout,
        {
            "timestamp": "2026-07-28T04:00:00Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Old prompt outside backfill window"},
        },
        {
            "timestamp": "2026-07-28T04:00:01Z",
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": "x" * 2000},
        },
        {
            "timestamp": "2026-07-28T04:00:02Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Latest prompt inside backfill window"},
        },
    )
    monkeypatch.setattr(codex_sessions, "MAX_INITIAL_BACKFILL_BYTES", 500, raising=False)
    published: list[dict[str, object]] = []
    watcher = CodexSessionWatcher(workspace, sessions_dir, lambda payload: published.append(payload) or 1)

    watcher.poll_once()

    assert published
    trace = published[-1]["trace"]
    assert isinstance(trace, dict)
    assert trace["run"]["task"] == "Latest prompt inside backfill window"
    assert [step["input"] for step in trace["steps"] if step["name"] == "User prompt"] == [
        "Latest prompt inside backfill window"
    ]


def test_claude_code_http_hooks_build_one_live_trace(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        prompt_result = _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": "claude-session-1",
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(tmp_path),
                "prompt": "Fix the failing local API test",
            },
        )
        run_id = str(prompt_result["run_id"])

        _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": "claude-session-1",
                "hook_event_name": "PreToolUse",
                "tool_use_id": "tool-1",
                "tool_name": "Bash",
                "tool_input": {"command": "py -m pytest"},
            },
        )
        _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": "claude-session-1",
                "hook_event_name": "PostToolUse",
                "tool_use_id": "tool-1",
                "tool_name": "Bash",
                "tool_input": {"command": "py -m pytest"},
                "tool_response": {"output": "1 passed"},
            },
        )
        _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": "claude-session-1",
                "hook_event_name": "Stop",
                "last_assistant_message": "The test now passes.",
            },
        )

        with urlopen(f"{base_url}/api/traces/{run_id}", timeout=3) as response:
            trace = json.loads(response.read())

        assert trace["run"]["task"] == "Fix the failing local API test"
        assert trace["run"]["ended_at"] is not None
        assert trace["run"]["final_output"] == "The test now passes."
        assert trace["run"]["labels"]["source"] == "claude-code-http-hooks"
        assert [step["name"] for step in trace["steps"]] == ["User prompt", "Bash"]
        assert [step["name"] for step in trace["steps"] if step["replayable"]] == ["User prompt"]
        assert trace["steps"][1]["tool"]["result"] == {"output": "1 passed"}
        assert len(list(workspace.trace_dir.glob("claude-code-*.trace.json"))) == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_claude_code_followup_prompt_updates_visible_task(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        first = _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": "claude-session-followup",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Read README first",
            },
        )
        _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": "claude-session-followup",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Now inspect job scraper workflow",
            },
        )

        with urlopen(f"{base_url}/api/traces/{first['run_id']}", timeout=3) as response:
            trace = json.loads(response.read())
        with urlopen(f"{base_url}/api/traces", timeout=3) as response:
            listing = json.loads(response.read())

        assert trace["run"]["task"] == "Now inspect job scraper workflow"
        assert [step["input"] for step in trace["steps"] if step["name"] == "User prompt"] == [
            "Read README first",
            "Now inspect job scraper workflow",
        ]
        assert listing["traces"][0]["run_id"] == first["run_id"]
        assert listing["traces"][0]["task"] == "Now inspect job scraper workflow"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_claude_code_hook_is_published_over_sse(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    received: list[dict[str, object]] = []
    listener_errors: list[Exception] = []
    listener_ready = threading.Event()

    def listen() -> None:
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/live/traces", timeout=3) as response:
                listener_ready.set()
                while True:
                    line = response.readline()
                    if line.startswith(b"data: "):
                        received.append(json.loads(line.removeprefix(b"data: ")))
                        return
        except Exception as exc:  # pragma: no cover - asserted by the main test thread
            listener_errors.append(exc)
            listener_ready.set()

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    try:
        assert listener_ready.wait(timeout=1)
        assert not listener_errors
        _post_json(
            f"http://127.0.0.1:{server.server_port}/api/hooks/claude-code",
            {
                "session_id": "claude-session-live",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Stream this run",
            },
        )
        listener.join(timeout=2)
        assert received
        assert received[0]["hook_event_name"] == "UserPromptSubmit"
        assert received[0]["trace"]["run"]["task"] == "Stream this run"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_claude_code_replay_registration_labels_the_new_trace(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        _post_json(
            f"{base_url}/api/replay/claude-code/register",
            {
                "session_id": "5fb61304-1df2-4a28-8f02-034c1c693131",
                "source_run_id": "claude-code-source",
                "source_start_step_id": "prompt-step-1",
                "source_run_status": "success",
            },
        )
        hook_result = _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": "5fb61304-1df2-4a28-8f02-034c1c693131",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Replay this task",
            },
        )

        with urlopen(f"{base_url}/api/traces/{hook_result['run_id']}", timeout=3) as response:
            trace = json.loads(response.read())

        assert trace["run"]["labels"]["replay"] == "true"
        assert trace["run"]["labels"]["replay_mode"] == "claude_code_execution"
        assert trace["run"]["labels"]["source_run_id"] == "claude-code-source"
        assert trace["run"]["labels"]["source_start_step_id"] == "prompt-step-1"
        assert trace["run"]["labels"]["source_run_status"] == "success"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_claude_code_replay_finalization_marks_budget_failure_as_partial_error(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        session_id = "be240876-351c-426d-a41c-e48c05cbb3ae"
        _post_json(
            f"{base_url}/api/replay/claude-code/register",
            {
                "session_id": session_id,
                "source_run_id": "claude-code-source",
                "source_start_step_id": "prompt-step-1",
                "source_run_status": "success",
            },
        )
        hook_result = _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": session_id,
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Replay this task",
            },
        )
        _post_json(
            f"{base_url}/api/hooks/claude-code",
            {
                "session_id": session_id,
                "hook_event_name": "Stop",
                "last_assistant_message": "Partial text before budget stopped the run",
            },
        )

        _post_json(
            f"{base_url}/api/replay/claude-code/finalize",
            {
                "session_id": session_id,
                "status": "error",
                "result_subtype": "error_max_budget_usd",
                "stop_reason": "tool_use",
                "claude_session_id": session_id,
                "total_cost_usd": 0.397472,
                "usage": {"input_tokens": 28785, "output_tokens": 213},
                "partial": True,
            },
        )

        with urlopen(f"{base_url}/api/traces/{hook_result['run_id']}", timeout=3) as response:
            trace = json.loads(response.read())

        assert trace["run"]["status"] == "error"
        assert trace["run"]["labels"]["claude_result_subtype"] == "error_max_budget_usd"
        assert trace["run"]["labels"]["claude_stop_reason"] == "tool_use"
        assert trace["run"]["labels"]["claude_session_id"] == session_id
        assert trace["run"]["labels"]["partial"] == "true"
        assert trace["run"]["cost"] == {
            "input_tokens": 28785,
            "output_tokens": 213,
            "total_tokens": 28998,
            "amount_usd": 0.397472,
        }
        assert "final_output" not in trace["run"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_cursor_hooks_build_one_live_trace_and_publish_over_sse(tmp_path: Path) -> None:
    workspace = initialize_workspace(tmp_path)
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    received: list[dict[str, object]] = []
    listener_ready = threading.Event()

    def listen() -> None:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/live/traces", timeout=3) as response:
            listener_ready.set()
            while True:
                line = response.readline()
                if line.startswith(b"data: "):
                    received.append(json.loads(line.removeprefix(b"data: ")))
                    return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    try:
        assert listener_ready.wait(timeout=1)
        base_url = f"http://127.0.0.1:{server.server_port}"
        common = {
            "conversation_id": "cursor-conversation-1",
            "generation_id": "cursor-generation-1",
            "cursor_version": "3.12.17",
            "model": "test-model",
            "workspace_roots": [str(tmp_path / "private-workspace")],
            "user_email": "developer@example.com",
            "transcript_path": str(tmp_path / "private-transcript.jsonl"),
        }
        prompt_result = _post_json(
            f"{base_url}/api/hooks/cursor",
            {**common, "hook_event_name": "beforeSubmitPrompt", "prompt": "Fix the Cursor integration test"},
        )
        run_id = str(prompt_result["run_id"])

        _post_json(
            f"{base_url}/api/hooks/cursor",
            {
                **common,
                "hook_event_name": "preToolUse",
                "tool_use_id": "cursor-tool-1",
                "tool_name": "Shell",
                "tool_input": {"command": "py -m pytest"},
                "cwd": str(tmp_path),
            },
        )
        _post_json(
            f"{base_url}/api/hooks/cursor",
            {
                **common,
                "hook_event_name": "postToolUse",
                "tool_use_id": "cursor-tool-1",
                "tool_name": "Shell",
                "tool_input": {"command": "py -m pytest"},
                "tool_output": '{"exitCode":0,"stdout":"1 passed"}',
                "duration": 42,
            },
        )
        _post_json(
            f"{base_url}/api/hooks/cursor",
            {**common, "hook_event_name": "afterAgentResponse", "text": "The Cursor test now passes."},
        )
        _post_json(
            f"{base_url}/api/hooks/cursor",
            {**common, "hook_event_name": "stop", "status": "completed", "loop_count": 0},
        )

        listener.join(timeout=2)
        assert received
        assert received[0]["source_event"] == "cursor-http-hooks"
        assert received[0]["hook_event_name"] == "beforeSubmitPrompt"

        with urlopen(f"{base_url}/api/traces/{run_id}", timeout=3) as response:
            trace = json.loads(response.read())

        assert run_id.startswith("cursor-")
        assert trace["run"]["task"] == "Fix the Cursor integration test"
        assert trace["run"]["status"] == "success"
        assert trace["run"]["ended_at"] is not None
        assert trace["run"]["final_output"] == "The Cursor test now passes."
        assert trace["run"]["labels"] == {
            "source": "cursor-http-hooks",
            "capture_scope": "visible-hook-events",
            "cursor_version": "3.12.17",
            "model": "test-model",
        }
        assert [step["name"] for step in trace["steps"]] == ["User prompt", "Shell", "Assistant answer"]
        assert trace["steps"][1]["tool"]["result"] == {"exitCode": 0, "stdout": "1 passed"}
        assert trace["steps"][1]["duration_ms"] == 42
        serialized = json.dumps(trace, ensure_ascii=False)
        assert "private-workspace" not in serialized
        assert "private-transcript" not in serialized
        assert "developer@example.com" not in serialized
        assert len(list(workspace.trace_dir.glob("cursor-*.trace.json"))) == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_cursor_hook_forwarder_fails_open_when_api_is_offline(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "cursor_hook_forward.py"
    log_path = tmp_path / "cursor-hook-forward.log"
    env = os.environ.copy()
    env["AGENT_DEVTOOLS_CURSOR_HOOK_URL"] = "http://127.0.0.1:1/api/hooks/cursor"
    env["AGENT_DEVTOOLS_CURSOR_HOOK_LOG"] = str(log_path)

    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(
            {
                "conversation_id": "offline-conversation",
                "hook_event_name": "beforeSubmitPrompt",
                "prompt": "Cursor must continue",
            }
        ),
        text=True,
        capture_output=True,
        timeout=5,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"continue": True}
    assert result.stderr == ""

    tool_result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(
            {
                "conversation_id": "offline-conversation",
                "hook_event_name": "preToolUse",
                "tool_name": "Shell",
                "tool_use_id": "offline-tool",
            }
        ),
        text=True,
        capture_output=True,
        timeout=5,
        env=env,
        check=False,
    )

    assert tool_result.returncode == 0
    assert json.loads(tool_result.stdout) == {}
    assert tool_result.stderr == ""
    diagnostics = log_path.read_text(encoding="utf-8")
    assert "beforeSubmitPrompt" in diagnostics
    assert "preToolUse" in diagnostics
    assert "http://127.0.0.1:1/api/hooks/cursor" in diagnostics
    assert "Cursor must continue" not in diagnostics


def test_cursor_hook_forwarder_accepts_utf8_bom_input() -> None:
    received: list[dict[str, object]] = []

    class CaptureHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP handler interface
            length = int(self.headers.get("Content-Length", "0"))
            received.append(json.loads(self.rfile.read(length)))
            body = b"{}"
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["AGENT_DEVTOOLS_CURSOR_HOOK_URL"] = f"http://127.0.0.1:{server.server_port}/api/hooks/cursor"
    try:
        payload = {
            "conversation_id": "utf8-conversation",
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": "只回复：最终验收",
        }
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "cursor_hook_forward.py")],
            input=b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=5,
            env=env,
            check=False,
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {"continue": True}
        assert received == [payload]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.skipif(os.name != "nt", reason="Windows mbcs behavior")
def test_cursor_hook_forwarder_repairs_windows_utf8_mojibake() -> None:
    received: list[dict[str, object]] = []

    class CaptureHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP handler interface
            length = int(self.headers.get("Content-Length", "0"))
            received.append(json.loads(self.rfile.read(length)))
            body = b"{}"
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["AGENT_DEVTOOLS_CURSOR_HOOK_URL"] = f"http://127.0.0.1:{server.server_port}/api/hooks/cursor"
    try:
        prompt = "只回复：最终验收通过"
        payload = {
            "conversation_id": "mojibake-conversation",
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": prompt.encode("utf-8").decode("mbcs"),
        }
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "cursor_hook_forward.py")],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=5,
            env=env,
            check=False,
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {"continue": True}
        assert received[0]["prompt"] == prompt
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_cursor_hook_forwarder_waits_for_a_busy_local_api(tmp_path: Path) -> None:
    delivered = threading.Event()

    class DelayedHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP handler interface
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            time.sleep(1)
            body = b"{}"
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                delivered.set()
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), DelayedHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["AGENT_DEVTOOLS_CURSOR_HOOK_URL"] = f"http://127.0.0.1:{server.server_port}/api/hooks/cursor"
    try:
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "cursor_hook_forward.py")],
            input=json.dumps(
                {
                    "conversation_id": "busy-api-conversation",
                    "hook_event_name": "beforeSubmitPrompt",
                    "prompt": "Wait for persistence",
                }
            ),
            text=True,
            capture_output=True,
            timeout=4,
            env=env,
            check=False,
        )

        assert result.returncode == 0
        assert delivered.wait(timeout=1)
        assert json.loads(result.stdout) == {"continue": True}
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
