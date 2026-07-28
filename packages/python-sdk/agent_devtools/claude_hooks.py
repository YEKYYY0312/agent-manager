"""Translate visible Claude Code HTTP hook events into local traces."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any

from .local import LocalWorkspace
from .store import TraceStore
from .trace import Cost, Error, Step, ToolCall, Trace, new_run
from .writer import TraceWriter


SUPPORTED_HOOK_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
}


@dataclass(frozen=True)
class ClaudeHookResult:
    run_id: str
    hook_event_name: str
    trace: Trace


class ClaudeHookIngestor:
    """Persist one Claude Code session as an incrementally updated trace."""

    def __init__(self, config: LocalWorkspace) -> None:
        self.store = TraceStore(config.db_path, redaction=True)
        self.writer = TraceWriter(config.trace_dir, redaction=True)
        self._lock = threading.Lock()
        self._replay_labels: dict[str, dict[str, str]] = {}

    def register_replay(self, payload: dict[str, Any]) -> None:
        session_id = _required_string(payload, "session_id")
        with self._lock:
            self._replay_labels[session_id] = {
                "replay": "true",
                "replay_mode": "claude_code_execution",
                "source_run_id": _required_string(payload, "source_run_id"),
                "source_start_step_id": _required_string(payload, "source_start_step_id"),
                "source_run_status": _required_string(payload, "source_run_status"),
            }

    def ingest(self, payload: dict[str, Any]) -> ClaudeHookResult:
        session_id = _required_string(payload, "session_id")
        event_name = _required_string(payload, "hook_event_name")
        if event_name not in SUPPORTED_HOOK_EVENTS:
            raise ValueError(f"Unsupported Claude Code hook event: {event_name}")

        run_id = _run_id(session_id)
        with self._lock:
            trace = self.store.get_trace(run_id) or _new_claude_trace(run_id, payload)
            replay_labels = self._replay_labels.get(session_id)
            if replay_labels:
                trace.run.labels.update(replay_labels)
            _apply_event(trace, event_name, payload)
            path = self.writer.write_atomic(trace)
            self.store.upsert_trace(trace, source_path=path)
            self._replay_labels.pop(session_id, None)
            safe_trace = self.store.get_trace(run_id)
            if safe_trace is None:  # pragma: no cover - SQLite upsert is synchronous
                raise OSError("Unable to reload the stored Claude Code trace")
        return ClaudeHookResult(run_id, event_name, safe_trace)

    def finalize_replay(self, payload: dict[str, Any]) -> ClaudeHookResult:
        session_id = _required_string(payload, "session_id")
        status = _required_string(payload, "status")
        if status not in {"success", "error", "cancelled", "timeout"}:
            raise ValueError(f"Unsupported Claude Code replay status: {status}")

        run_id = _run_id(session_id)
        with self._lock:
            trace = self.store.get_trace(run_id) or _new_claude_trace(run_id, payload)
            replay_labels = self._replay_labels.get(session_id)
            if replay_labels:
                trace.run.labels.update(replay_labels)
            _apply_replay_result(trace, payload, status)
            path = self.writer.write_atomic(trace)
            self.store.upsert_trace(trace, source_path=path)
            self._replay_labels.pop(session_id, None)
            safe_trace = self.store.get_trace(run_id)
            if safe_trace is None:  # pragma: no cover - SQLite upsert is synchronous
                raise OSError("Unable to reload the finalized Claude Code replay trace")
        return ClaudeHookResult(run_id, "ReplayFinalize", safe_trace)


def _new_claude_trace(run_id: str, payload: dict[str, Any]) -> Trace:
    trace = new_run(
        "Claude Code session",
        labels={
            "source": "claude-code-http-hooks",
            "capture_scope": "visible-hook-events",
            "cwd_present": "true" if str(payload.get("cwd", "")).strip() else "false",
        },
    )
    trace.run.id = run_id
    return trace


def _apply_event(trace: Trace, event_name: str, payload: dict[str, Any]) -> None:
    if event_name in {"SessionStart", "UserPromptSubmit", "PreToolUse"}:
        _reopen(trace)

    if event_name == "SessionStart":
        step = Step(type="control", name="Claude Code session started", replayable=False)
        step.complete(output={"source": payload.get("source"), "model": payload.get("model")})
        trace.add_step(step)
        return

    if event_name == "UserPromptSubmit":
        prompt = str(payload.get("prompt", ""))
        if prompt:
            trace.run.task = prompt[:20000]
        step = Step(type="custom", name="User prompt", input=prompt, replayable=True)
        step.complete()
        trace.add_step(step)
        return

    if event_name == "PreToolUse":
        trace.add_step(_tool_step(payload))
        return

    if event_name in {"PostToolUse", "PostToolUseFailure"}:
        step = _find_tool_step(trace, str(payload.get("tool_use_id", ""))) or _tool_step(payload)
        if step not in trace.steps:
            trace.add_step(step)
        step.metadata["claude_hook_event"] = event_name
        if event_name == "PostToolUseFailure":
            message = str(payload.get("error", "Claude Code tool call failed"))
            step.complete(status="error", error=Error(type="ClaudeToolError", message=message))
        else:
            result = payload.get("tool_response")
            if step.tool is not None:
                step.tool.result = result
            step.complete(output=result)
        return

    if event_name in {"Stop", "SessionEnd"}:
        output = payload.get("last_assistant_message")
        trace.run.complete(final_output=output if output is not None else trace.run.final_output)


def _apply_replay_result(trace: Trace, payload: dict[str, Any], status: str) -> None:
    label_fields = {
        "result_subtype": "claude_result_subtype",
        "stop_reason": "claude_stop_reason",
        "claude_session_id": "claude_session_id",
    }
    for payload_key, label_key in label_fields.items():
        value = payload.get(payload_key)
        if isinstance(value, str) and value.strip():
            trace.run.labels[label_key] = value.strip()
    if payload.get("partial") is True:
        trace.run.labels["partial"] = "true"

    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _non_negative_int(usage.get("input_tokens", 0), "usage.input_tokens")
    output_tokens = _non_negative_int(usage.get("output_tokens", 0), "usage.output_tokens")
    amount_usd = _non_negative_float(payload.get("total_cost_usd", 0), "total_cost_usd")
    cost = Cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        amount_usd=amount_usd,
    )
    if status != "success":
        trace.run.final_output = None
    final_output = payload.get("result") if status == "success" else None
    trace.run.complete(status=status, final_output=final_output, cost=cost)


def _tool_step(payload: dict[str, Any]) -> Step:
    name = str(payload.get("tool_name", "Claude Code tool"))
    tool_input = payload.get("tool_input")
    return Step(
        type="tool_call",
        name=name,
        input=tool_input,
        tool=ToolCall(name=name, args=tool_input),
        replayable=False,
        metadata={
            "tool_use_id": str(payload.get("tool_use_id", "")),
            "claude_hook_event": str(payload.get("hook_event_name", "")),
        },
    )


def _find_tool_step(trace: Trace, tool_use_id: str) -> Step | None:
    if not tool_use_id:
        return None
    for step in reversed(trace.steps):
        if step.type == "tool_call" and step.metadata.get("tool_use_id") == tool_use_id:
            return step
    return None


def _reopen(trace: Trace) -> None:
    trace.run.ended_at = None
    trace.run.duration_ms = None


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Claude Code hook payload requires {key}")
    return value.strip()


def _non_negative_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Claude Code replay result requires a non-negative integer {key}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Claude Code replay result requires a non-negative integer {key}") from exc
    if parsed < 0:
        raise ValueError(f"Claude Code replay result requires a non-negative integer {key}")
    return parsed


def _non_negative_float(value: Any, key: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Claude Code replay result requires a non-negative number {key}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Claude Code replay result requires a non-negative number {key}") from exc
    if parsed < 0:
        raise ValueError(f"Claude Code replay result requires a non-negative number {key}")
    return parsed


def _run_id(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return f"claude-code-{digest}"
