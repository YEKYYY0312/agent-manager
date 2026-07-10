"""Core data models for the Agent DevTools trace format.

Every model maps 1:1 to schemas/trace.schema.json. All models are dataclasses
with to_dict()/from_dict() for JSON round-tripping.
"""

from __future__ import annotations

import os
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Status = Literal["success", "error", "cancelled", "timeout"]
StepType = Literal["model_call", "tool_call", "retrieval", "memory", "planner", "control", "custom"]

SCHEMA_VERSION = "0.1.0"
DEFAULT_MAX_TRACE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_JSON_DEPTH = 120
DEFAULT_MAX_STEP_EVENTS = 1000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------


@dataclass
class Cost:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    amount_usd: float = 0.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> Cost:
        self.input_tokens = _validate_non_negative_int("input_tokens", self.input_tokens)
        self.output_tokens = _validate_non_negative_int("output_tokens", self.output_tokens)
        self.total_tokens = _validate_non_negative_int("total_tokens", self.total_tokens)
        self.amount_usd = _validate_non_negative_float("amount_usd", self.amount_usd)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "amount_usd": self.amount_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Cost:
        if not data:
            return cls()
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            amount_usd=float(data.get("amount_usd", 0.0)),
        )


@dataclass
class Error:
    message: str
    type: str = ""
    stack: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"message": self.message}
        if self.type:
            d["type"] = self.type
        if self.stack:
            d["stack"] = self.stack
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Error | None:
        if not data:
            return None
        return cls(
            message=data.get("message", ""),
            type=data.get("type", ""),
            stack=data.get("stack", ""),
        )

    @classmethod
    def from_exc(cls, exc: BaseException | None = None, *, include_stack: bool = False) -> Error:
        import traceback as tb

        exc = exc or Exception("unknown error")
        return cls(
            type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
            stack="".join(tb.format_exception(exc)) if include_stack else "",
        )


@dataclass
class Event:
    timestamp: str
    type: str
    message: str = ""
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"timestamp": self.timestamp, "type": self.type}
        if self.message:
            d["message"] = self.message
        if self.data is not None:
            d["data"] = self.data
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            timestamp=data["timestamp"],
            type=data["type"],
            message=data.get("message", ""),
            data=data.get("data"),
        )


@dataclass
class ToolCall:
    name: str
    args: Any = None
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.args is not None:
            d["args"] = self.args
        d["result"] = self.result
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ToolCall | None:
        if not data:
            return None
        return cls(
            name=data.get("name", ""),
            args=data.get("args"),
            result=data.get("result"),
        )


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


@dataclass
class Step:
    id: str = field(default_factory=_new_id)
    type: StepType = "custom"
    name: str = ""
    status: Status = "success"
    started_at: str = field(default_factory=_utc_now)
    ended_at: str | None = None
    duration_ms: float | None = None
    parent_id: str | None = None
    model: str = ""
    input: Any = None
    output: Any = None
    tool: ToolCall | None = None
    cost: Cost | None = None
    error: Error | None = None
    events: list[Event] = field(default_factory=list)
    replayable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.events) > _max_step_events():
            raise ValueError(f"Step exceeds maximum event count of {_max_step_events()}")

    def complete(
        self,
        status: Status = "success",
        output: Any = None,
        error: Error | None = None,
        cost: Cost | None = None,
        duration_ms: float | None = None,
    ) -> Step:
        self.ended_at = _utc_now()
        self.status = status
        if output is not None:
            self.output = output
        if error is not None:
            self.error = error
        if cost is not None:
            self.cost = cost
        if duration_ms is not None:
            self.duration_ms = duration_ms
        elif self.ended_at and self.started_at:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(self.ended_at.replace("Z", "+00:00"))
            self.duration_ms = (ended - started).total_seconds() * 1000
        return self

    def add_event(self, type: str, message: str = "", data: Any = None) -> Event:
        if len(self.events) >= _max_step_events():
            raise ValueError(f"Step exceeds maximum event count of {_max_step_events()}")
        evt = Event(timestamp=_utc_now(), type=type, message=message, data=data)
        self.events.append(evt)
        return evt

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at,
        }
        if self.parent_id:
            d["parent_id"] = self.parent_id
        if self.ended_at is not None:
            d["ended_at"] = self.ended_at
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.model:
            d["model"] = self.model
        if self.input is not None:
            d["input"] = self.input
        if self.output is not None:
            d["output"] = self.output
        if self.tool is not None:
            d["tool"] = self.tool.to_dict()
        if self.cost is not None:
            d["cost"] = self.cost.to_dict()
        if self.error is not None:
            d["error"] = self.error.to_dict()
        if self.events:
            d["events"] = [e.to_dict() for e in self.events]
        d["replayable"] = self.replayable
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Step:
        return cls(
            id=data["id"],
            type=data.get("type", "custom"),
            name=data.get("name", ""),
            status=data.get("status", "success"),
            started_at=data.get("started_at", ""),
            ended_at=data.get("ended_at"),
            duration_ms=data.get("duration_ms"),
            parent_id=data.get("parent_id"),
            model=data.get("model", ""),
            input=data.get("input"),
            output=data.get("output"),
            tool=ToolCall.from_dict(data.get("tool")),
            cost=Cost.from_dict(data.get("cost")),
            error=Error.from_dict(data.get("error")),
            events=[Event.from_dict(e) for e in data.get("events", [])],
            replayable=data.get("replayable", False),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass
class Run:
    id: str = field(default_factory=_new_id)
    task: str = ""
    status: Status = "success"
    started_at: str = field(default_factory=_utc_now)
    ended_at: str | None = None
    duration_ms: float | None = None
    labels: dict[str, str] = field(default_factory=dict)
    final_output: Any = None
    cost: Cost | None = None

    def complete(
        self,
        status: Status = "success",
        final_output: Any = None,
        cost: Cost | None = None,
        duration_ms: float | None = None,
    ) -> Run:
        self.ended_at = _utc_now()
        self.status = status
        if final_output is not None:
            self.final_output = final_output
        if cost is not None:
            self.cost = cost
        if duration_ms is not None:
            self.duration_ms = duration_ms
        elif self.ended_at and self.started_at:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(self.ended_at.replace("Z", "+00:00"))
            self.duration_ms = (ended - started).total_seconds() * 1000
        return self

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "started_at": self.started_at,
        }
        if self.ended_at is not None:
            d["ended_at"] = self.ended_at
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.labels:
            d["labels"] = self.labels
        if self.final_output is not None:
            d["final_output"] = self.final_output
        if self.cost is not None:
            d["cost"] = self.cost.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Run:
        return cls(
            id=data["id"],
            task=data.get("task", ""),
            status=data.get("status", "success"),
            started_at=data.get("started_at", ""),
            ended_at=data.get("ended_at"),
            duration_ms=data.get("duration_ms"),
            labels=data.get("labels", {}),
            final_output=data.get("final_output"),
            cost=Cost.from_dict(data.get("cost")),
        )


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


@dataclass
class Trace:
    schema_version: str = SCHEMA_VERSION
    run: Run = field(default_factory=Run)
    steps: list[Step] = field(default_factory=list)

    def add_step(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def total_cost(self) -> Cost:
        """Aggregate cost from steps. Falls back to run.cost only when steps carry no cost."""
        c = Cost()
        for step in self.steps:
            if step.cost:
                step_cost = step.cost.validate()
                c.input_tokens += step_cost.input_tokens
                c.output_tokens += step_cost.output_tokens
                c.total_tokens += step_cost.total_tokens
                c.amount_usd += step_cost.amount_usd
        if c.total_tokens == 0 and self.run.cost:
            self.run.cost.validate()
            return Cost(
                input_tokens=self.run.cost.input_tokens,
                output_tokens=self.run.cost.output_tokens,
                total_tokens=self.run.cost.total_tokens,
                amount_usd=self.run.cost.amount_usd,
            )
        return c

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run": self.run.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trace:
        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            run=Run.from_dict(data.get("run", {})),
            steps=[Step.from_dict(s) for s in data.get("steps", [])],
        )

    @classmethod
    def from_file(cls, path: str, *, max_bytes: int | None = None) -> Trace:
        import json
        from pathlib import Path

        trace_path = Path(path)
        limit = _max_trace_bytes(max_bytes)
        if limit is not None:
            size = trace_path.stat().st_size
            if size > limit:
                raise ValueError(f"Trace file {trace_path} exceeds maximum size of {limit} bytes")
        raw = trace_path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        _validate_json_depth(data, DEFAULT_MAX_JSON_DEPTH)
        return cls.from_dict(data)


def new_run(task: str, labels: dict[str, str] | None = None) -> Trace:
    """Create a new trace with a fresh run, ready to record steps."""
    run = Run(task=task, labels=labels or {})
    return Trace(run=run)


def _max_trace_bytes(override: int | None) -> int | None:
    if override is not None:
        return override
    raw = os.getenv("AGENT_DEVTOOLS_MAX_TRACE_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_TRACE_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_TRACE_BYTES
    return value if value > 0 else DEFAULT_MAX_TRACE_BYTES


def _max_step_events() -> int:
    raw = os.getenv("AGENT_DEVTOOLS_MAX_STEP_EVENTS", "").strip()
    if not raw:
        return DEFAULT_MAX_STEP_EVENTS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_STEP_EVENTS
    return value if value > 0 else DEFAULT_MAX_STEP_EVENTS


def _validate_non_negative_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _validate_non_negative_float(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite non-negative number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _validate_json_depth(value: Any, max_depth: int, depth: int = 0) -> None:
    if depth > max_depth:
        raise ValueError(f"Trace JSON exceeds maximum JSON depth of {max_depth}")
    if isinstance(value, dict):
        for child in value.values():
            _validate_json_depth(child, max_depth, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_json_depth(child, max_depth, depth + 1)
