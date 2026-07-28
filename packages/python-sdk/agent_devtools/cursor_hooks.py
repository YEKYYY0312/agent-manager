"""Translate visible Cursor command hook events into local traces."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any

from .local import LocalWorkspace
from .store import TraceStore
from .trace import Error, Step, ToolCall, Trace, new_run
from .writer import TraceWriter


SUPPORTED_HOOK_EVENTS = {
    "beforeSubmitPrompt",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "afterAgentResponse",
    "stop",
}


@dataclass(frozen=True)
class CursorHookResult:
    run_id: str
    hook_event_name: str
    trace: Trace


class CursorHookIngestor:
    """Persist one Cursor conversation as an incrementally updated trace."""

    def __init__(self, config: LocalWorkspace) -> None:
        self.store = TraceStore(config.db_path, redaction=True)
        self.writer = TraceWriter(config.trace_dir, redaction=True)
        self._lock = threading.Lock()

    def ingest(self, payload: dict[str, Any]) -> CursorHookResult:
        conversation_id = _required_string(payload, "conversation_id")
        event_name = _required_string(payload, "hook_event_name")
        if event_name not in SUPPORTED_HOOK_EVENTS:
            raise ValueError(f"Unsupported Cursor hook event: {event_name}")

        run_id = _run_id(conversation_id)
        with self._lock:
            trace = self.store.get_trace(run_id) or _new_cursor_trace(run_id, payload)
            _apply_event(trace, event_name, payload)
            path = self.writer.write_atomic(trace)
            self.store.upsert_trace(trace, source_path=path)
            safe_trace = self.store.get_trace(run_id)
            if safe_trace is None:  # pragma: no cover - SQLite upsert is synchronous
                raise OSError("Unable to reload the stored Cursor trace")
        return CursorHookResult(run_id, event_name, safe_trace)


def _new_cursor_trace(run_id: str, payload: dict[str, Any]) -> Trace:
    labels = {
        "source": "cursor-http-hooks",
        "capture_scope": "visible-hook-events",
    }
    for key in ("cursor_version", "model"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            labels[key] = value.strip()
    trace = new_run("Cursor session", labels=labels)
    trace.run.id = run_id
    return trace


def _apply_event(trace: Trace, event_name: str, payload: dict[str, Any]) -> None:
    generation_id = str(payload.get("generation_id", ""))
    metadata = {"cursor_generation_id": generation_id} if generation_id else {}

    if event_name in {"beforeSubmitPrompt", "preToolUse"}:
        _reopen(trace)

    if event_name == "beforeSubmitPrompt":
        prompt = str(payload.get("prompt", ""))
        if prompt:
            trace.run.task = prompt[:20_000]
        step = Step(type="custom", name="User prompt", input=prompt[:20_000], replayable=False, metadata=metadata)
        step.complete()
        trace.add_step(step)
        return

    if event_name == "preToolUse":
        trace.add_step(_tool_step(payload, metadata))
        return

    if event_name in {"postToolUse", "postToolUseFailure"}:
        tool_use_id = str(payload.get("tool_use_id", ""))
        step = _find_tool_step(trace, tool_use_id) or _tool_step(payload, metadata)
        if step not in trace.steps:
            trace.add_step(step)
        if event_name == "postToolUseFailure":
            failure_type = str(payload.get("failure_type", "error"))
            message = str(payload.get("error_message", "Cursor tool call failed"))
            step.complete(
                status="cancelled" if payload.get("is_interrupt") is True else "error",
                error=Error(type=f"CursorTool{failure_type.title()}", message=message),
                duration_ms=_duration(payload.get("duration")),
            )
        else:
            result = _tool_output(payload.get("tool_output"))
            if step.tool is not None:
                step.tool.result = result
            step.complete(output=result, duration_ms=_duration(payload.get("duration")))
        return

    if event_name == "afterAgentResponse":
        text = str(payload.get("text", ""))[:20_000]
        if text:
            step = Step(
                type="model_call",
                name="Assistant answer",
                model=str(payload.get("model", "")),
                output=text,
                replayable=False,
                metadata=metadata,
            )
            step.complete(output=text)
            trace.add_step(step)
            trace.run.final_output = text
        return

    if event_name == "stop":
        status = str(payload.get("status", "completed"))
        trace.run.complete(
            status={"completed": "success", "aborted": "cancelled", "error": "error"}.get(status, "error"),
            final_output=trace.run.final_output,
        )


def _tool_step(payload: dict[str, Any], metadata: dict[str, Any]) -> Step:
    name = str(payload.get("tool_name", "Cursor tool"))
    tool_input = payload.get("tool_input")
    return Step(
        type="tool_call",
        name=name,
        input=tool_input,
        tool=ToolCall(name=name, args=tool_input),
        replayable=False,
        metadata={
            **metadata,
            "cursor_tool_use_id": str(payload.get("tool_use_id", "")),
        },
    )


def _find_tool_step(trace: Trace, tool_use_id: str) -> Step | None:
    if not tool_use_id:
        return None
    for step in reversed(trace.steps):
        if step.type == "tool_call" and step.metadata.get("cursor_tool_use_id") == tool_use_id:
            return step
    return None


def _tool_output(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value[:20_000]


def _duration(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if duration >= 0 else None


def _reopen(trace: Trace) -> None:
    trace.run.ended_at = None
    trace.run.duration_ms = None


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Cursor hook payload requires {key}")
    return value.strip()


def _run_id(conversation_id: str) -> str:
    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:24]
    return f"cursor-{digest}"
