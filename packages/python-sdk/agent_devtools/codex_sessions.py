"""Translate visible Codex session JSONL events into local traces."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .local import LocalWorkspace, mark_trace_imported
from .store import TraceStore
from .trace import Cost, Error, Step, ToolCall, Trace, new_run
from .writer import TraceWriter

MAX_VISIBLE_TEXT = 20_000
ACTIVE_SESSION_WINDOW_SECONDS = 15 * 60
MAX_INITIAL_BACKFILL_BYTES = 2 * 1024 * 1024
_SESSION_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.IGNORECASE)


@dataclass(frozen=True)
class CodexSessionResult:
    run_id: str
    event_name: str
    trace: Trace


def discover_codex_sessions_dir() -> Path | None:
    """Find the active Codex session directory without requiring configuration."""
    candidates: list[Path] = []
    configured_home = os.getenv("CODEX_HOME", "").strip()
    if configured_home:
        candidates.append(Path(configured_home).expanduser() / "sessions")
    candidates.append(Path.home() / ".codex" / "sessions")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


class CodexSessionIngestor:
    """Persist one Codex session as an incrementally updated visible trace."""

    def __init__(self, config: LocalWorkspace) -> None:
        self.config = config
        self.store = TraceStore(config.db_path, redaction=True)
        self.writer = TraceWriter(config.trace_dir, redaction=True)

    def ingest_records(
        self,
        session_id: str,
        records: list[tuple[int, dict[str, Any]]],
    ) -> CodexSessionResult | None:
        run_id = f"codex-{session_id}"
        trace = self.store.get_trace(run_id)
        changed = False
        last_event = ""
        for source_offset, record in records:
            event_name = _visible_event_name(record)
            if not event_name:
                continue
            if trace is None:
                trace = _new_codex_trace(run_id, record)
            if _apply_event(trace, session_id, source_offset, record):
                changed = True
                last_event = event_name
        if trace is None or not changed:
            return None

        path = self.writer.write_atomic(trace)
        self.store.upsert_trace(trace, source_path=path)
        mark_trace_imported(self.config, path)
        safe_trace = self.store.get_trace(run_id)
        if safe_trace is None:  # pragma: no cover - SQLite upsert is synchronous
            raise OSError("Unable to reload the stored Codex trace")
        return CodexSessionResult(run_id, last_event, safe_trace)


class CodexSessionWatcher:
    """Incrementally read current and newly-created Codex rollout files."""

    def __init__(
        self,
        config: LocalWorkspace,
        sessions_dir: str | Path,
        publish: Callable[[dict[str, Any]], int],
    ) -> None:
        self.sessions_dir = Path(sessions_dir).resolve()
        self.ingestor = CodexSessionIngestor(config)
        self.publish = publish
        self._offsets: dict[Path, int] = {}
        self._session_ids: dict[Path, str] = {}
        self._started_at = time.time()

    def poll_once(self) -> None:
        if not self.sessions_dir.is_dir():
            return
        for path in sorted(self.sessions_dir.rglob("rollout-*.jsonl")):
            try:
                self._poll_file(path)
            except OSError:
                continue

    def _poll_file(self, path: Path) -> None:
        stat = path.stat()
        if path not in self._offsets:
            is_active = stat.st_mtime >= self._started_at - ACTIVE_SESSION_WINDOW_SECONDS
            self._offsets[path] = max(0, stat.st_size - MAX_INITIAL_BACKFILL_BYTES) if is_active else stat.st_size
            self._session_ids[path] = _session_id_from_filename(path)
        offset = self._offsets[path]
        if stat.st_size < offset:
            offset = 0
        if stat.st_size == offset:
            return

        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
        complete_length = chunk.rfind(b"\n") + 1
        if complete_length <= 0:
            return

        records: list[tuple[int, dict[str, Any]]] = []
        cursor = offset
        for raw_line in chunk[:complete_length].splitlines(keepends=True):
            line_offset = cursor
            cursor += len(raw_line)
            try:
                record = json.loads(raw_line.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            records.append((line_offset, record))
            session_id = _session_id_from_record(record)
            if session_id:
                self._session_ids[path] = session_id
        self._offsets[path] = offset + complete_length

        session_id = self._session_ids[path]
        result = self.ingestor.ingest_records(session_id, records)
        if result is not None:
            self.publish(
                {
                    "run_id": result.run_id,
                    "source_event": "codex-session-jsonl",
                    "codex_event_type": result.event_name,
                    "trace": result.trace.to_dict(),
                }
            )


def _new_codex_trace(run_id: str, record: dict[str, Any]) -> Trace:
    trace = new_run(
        "Codex session",
        labels={
            "source": "codex-session-jsonl",
            "capture_scope": "visible-session-events",
        },
    )
    trace.run.id = run_id
    trace.run.started_at = _timestamp(record)
    return trace


def _visible_event_name(record: dict[str, Any]) -> str:
    outer_type = record.get("type")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    payload_type = payload.get("type")
    if outer_type == "event_msg" and payload_type in {
        "task_started",
        "user_message",
        "token_count",
        "task_complete",
        "turn_aborted",
    }:
        return str(payload_type)
    if outer_type == "response_item" and payload_type in {
        "message",
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
    }:
        if payload_type != "message" or payload.get("role") == "assistant":
            return str(payload_type)
    return ""


def _apply_event(
    trace: Trace,
    session_id: str,
    source_offset: int,
    record: dict[str, Any],
) -> bool:
    payload = record["payload"]
    event_name = str(payload["type"])
    timestamp = _timestamp(record)

    if event_name == "task_started":
        _reopen(trace)
        return True

    if event_name == "user_message":
        message = _visible_user_message(payload.get("message"))
        if not message:
            return False
        _reopen(trace)
        trace.run.task = _truncate(message)
        step = Step(
            id=_step_id(session_id, source_offset, event_name),
            type="custom",
            name="User prompt",
            input=_truncate(message),
            started_at=timestamp,
            replayable=False,
        )
        _finish_step(step, timestamp)
        return _add_step_once(trace, step)

    if event_name == "message":
        text = _content_text(payload.get("content"))
        if not text:
            return False
        phase = str(payload.get("phase", ""))
        name = "Assistant answer" if phase == "final_answer" else "Assistant commentary"
        step = Step(
            id=_step_id(session_id, source_offset, event_name),
            type="model_call",
            name=name,
            output=text,
            started_at=timestamp,
            replayable=False,
            metadata={"codex_phase": phase} if phase else {},
        )
        _finish_step(step, timestamp, output=text)
        added = _add_step_once(trace, step)
        if phase == "final_answer":
            trace.run.final_output = text
        return added

    if event_name in {"function_call", "custom_tool_call"}:
        call_id = str(payload.get("call_id", ""))
        name = str(payload.get("name", "Codex tool"))
        raw_input = payload.get("arguments") if event_name == "function_call" else payload.get("input")
        tool_input = _tool_input(raw_input)
        step = Step(
            id=_step_id(session_id, source_offset, event_name),
            type="tool_call",
            name=name,
            input=tool_input,
            tool=ToolCall(name=name, args=tool_input),
            started_at=timestamp,
            replayable=False,
            metadata={"codex_call_id": call_id, "codex_item_type": event_name},
        )
        return _add_step_once(trace, step)

    if event_name in {"function_call_output", "custom_tool_call_output"}:
        call_id = str(payload.get("call_id", ""))
        step = _find_tool_step(trace, call_id)
        if step is None or step.ended_at is not None:
            return False
        output = _content_text(payload.get("output"))
        status = "error" if _tool_output_failed(payload.get("output")) else "success"
        error = Error(type="CodexToolError", message=output or "Codex tool call failed") if status == "error" else None
        if step.tool is not None:
            step.tool.result = output
        _finish_step(step, timestamp, status=status, output=output, error=error)
        return True

    if event_name == "token_count":
        info = payload.get("info")
        usage = info.get("total_token_usage") if isinstance(info, dict) else None
        if not isinstance(usage, dict):
            return False
        trace.run.cost = Cost(
            input_tokens=_non_negative_int(usage.get("input_tokens")),
            output_tokens=_non_negative_int(usage.get("output_tokens")),
            total_tokens=_non_negative_int(usage.get("total_tokens")),
        )
        return True

    if event_name == "task_complete":
        trace.run.status = "success"
        trace.run.ended_at = timestamp
        trace.run.duration_ms = _non_negative_float(payload.get("duration_ms"))
        final_output = payload.get("last_agent_message")
        if isinstance(final_output, str) and final_output.strip():
            trace.run.final_output = _truncate(final_output.strip())
        return True

    if event_name == "turn_aborted":
        trace.run.status = "cancelled"
        trace.run.ended_at = timestamp
        trace.run.duration_ms = _non_negative_float(payload.get("duration_ms"))
        reason = payload.get("reason")
        if isinstance(reason, str) and reason.strip():
            trace.run.final_output = _truncate(reason.strip())
        return True

    return False


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return _truncate(value)
    if not isinstance(value, list):
        return ""
    parts = [str(item.get("text", "")) for item in value if isinstance(item, dict) and item.get("text")]
    return _truncate("\n".join(parts))


def _visible_user_message(value: Any) -> str:
    message = str(value or "").strip()
    marker = "## My request for Codex:"
    if marker in message:
        message = message.rsplit(marker, 1)[1].strip()
    return _truncate(message)


def _tool_input(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return _truncate(value)


def _tool_output_failed(value: Any) -> bool:
    first = value[0].get("text", "") if isinstance(value, list) and value and isinstance(value[0], dict) else value
    return isinstance(first, str) and first.lstrip().lower().startswith(("script failed", "script error"))


def _find_tool_step(trace: Trace, call_id: str) -> Step | None:
    if not call_id:
        return None
    for step in reversed(trace.steps):
        if step.type == "tool_call" and step.metadata.get("codex_call_id") == call_id:
            return step
    return None


def _add_step_once(trace: Trace, step: Step) -> bool:
    if any(existing.id == step.id for existing in trace.steps):
        return False
    trace.add_step(step)
    return True


def _finish_step(
    step: Step,
    timestamp: str,
    *,
    status: str = "success",
    output: Any = None,
    error: Error | None = None,
) -> None:
    step.status = status  # type: ignore[assignment]
    step.ended_at = timestamp
    step.duration_ms = _duration_ms(step.started_at, timestamp)
    if output is not None:
        step.output = output
    if error is not None:
        step.error = error


def _reopen(trace: Trace) -> None:
    trace.run.status = "success"
    trace.run.ended_at = None
    trace.run.duration_ms = None


def _session_id_from_record(record: dict[str, Any]) -> str:
    if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
        return ""
    payload = record["payload"]
    value = payload.get("session_id") or payload.get("id")
    return str(value).strip() if value else ""


def _session_id_from_filename(path: Path) -> str:
    match = _SESSION_ID_RE.search(path.stem)
    if match:
        return match.group(1)
    digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:24]
    return digest


def _step_id(session_id: str, source_offset: int, event_name: str) -> str:
    value = f"{session_id}:{source_offset}:{event_name}"
    return f"codex-step-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _timestamp(record: dict[str, Any]) -> str:
    value = record.get("timestamp")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _duration_ms(started_at: str, ended_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        return max(0.0, (ended - started).total_seconds() * 1000)
    except ValueError:
        return 0.0


def _truncate(value: str) -> str:
    if len(value) <= MAX_VISIBLE_TEXT:
        return value
    return value[:MAX_VISIBLE_TEXT] + "\n[TRUNCATED]"


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _non_negative_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None
