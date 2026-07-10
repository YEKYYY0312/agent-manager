"""Run context — lifecycle manager for trace recording.

Provides a context manager that opens a run, tracks active steps, and writes
the completed trace on exit. Supports nested step contexts via a stack.

Usage::

    with TraceContext(task="Answer a question") as ctx:
        with ctx.step("planner", "Plan answer", input=question) as plan:
            plan.complete(output="Call tool then summarize")
        with ctx.step("tool_call", "weather.lookup", input={"city": "Shanghai"}) as tool:
            tool.complete(output={"temp": 22})
    # Trace is written to traces/ when the context exits.
"""

from __future__ import annotations

import contextvars
import sys
from contextlib import contextmanager
from typing import Any, Generator

from .trace import Cost, Error, Run, Step, StepType, Status, Trace, _new_id
from .writer import TraceWriter

_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "agent_devtools_current_trace", default=None
)
_current_step: contextvars.ContextVar[Step | None] = contextvars.ContextVar(
    "agent_devtools_current_step", default=None
)


class TraceContext:
    """Open a trace run, record steps, and persist on exit."""

    def __init__(
        self,
        task: str,
        run_id: str | None = None,
        labels: dict[str, str] | None = None,
        output_dir: str = "traces",
    ) -> None:
        self.task = task
        self.labels = labels or {}
        self.output_dir = output_dir
        self.trace = Trace(
            run=Run(
                id=run_id or _new_id(),
                task=task,
                labels=self.labels,
            )
        )
        self.writer = TraceWriter(output_dir)
        self._token: contextvars.Token | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> TraceContext:
        self._token = _current_trace.set(self.trace)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._token is not None:
            _current_trace.reset(self._token)
            self._token = None

        if exc_type is not None:
            self.trace.run.complete(
                status=_exc_to_status(exc_type),
                final_output=str(exc_val) if exc_val else None,
            )
        elif self.trace.run.ended_at is None:
            self.trace.run.complete(status="success")

        self.writer.write(self.trace)
        return False  # don't swallow exceptions

    # ------------------------------------------------------------------
    # Step context
    # ------------------------------------------------------------------

    @contextmanager
    def step(
        self,
        type: StepType,
        name: str,
        input: Any = None,
        model: str = "",
        replayable: bool = True,
        parent_id: str | None = None,
    ) -> Generator[Step, None, None]:
        s = Step(
            type=type,
            name=name,
            input=input,
            model=model,
            replayable=replayable,
            parent_id=parent_id or self._current_step_id(),
        )
        self.trace.add_step(s)

        token = _current_step.set(s)
        try:
            yield s
        except Exception:
            s.complete(status="error", error=Error.from_exc(sys.exc_info()[1]))
            raise
        else:
            if s.ended_at is None:
                s.complete(status="success")
        finally:
            _current_step.reset(token)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    @contextmanager
    def model_call(
        self,
        name: str,
        input: Any = None,
        model: str = "",
        replayable: bool = True,
    ) -> Generator[Step, None, None]:
        with self.step("model_call", name, input=input, model=model, replayable=replayable) as s:
            yield s

    @contextmanager
    def tool_call(
        self,
        name: str,
        args: Any = None,
        replayable: bool = True,
    ) -> Generator[Step, None, None]:
        from .trace import ToolCall

        s = Step(type="tool_call", name=name, input=args, replayable=replayable, tool=ToolCall(name=name, args=args))
        self.trace.add_step(s)
        token = _current_step.set(s)
        try:
            yield s
        except Exception:
            s.tool.result = None
            s.complete(status="error", error=Error.from_exc(sys.exc_info()[1]))
            raise
        else:
            if s.ended_at is None:
                s.complete(status="success")
            # Always sync step.output into tool.result after exit (success path)
            s.tool.result = s.output
        finally:
            _current_step.reset(token)

    def _current_step_id(self) -> str | None:
        s = _current_step.get()
        return s.id if s else None


def _exc_to_status(exc_type: type) -> Status:
    if exc_type is TimeoutError:
        return "timeout"
    if exc_type is KeyboardInterrupt:
        return "cancelled"
    return "error"


# ------------------------------------------------------------------
# Module-level helpers (convenient when inside a TraceContext block)
# ------------------------------------------------------------------


def current_trace() -> Trace | None:
    return _current_trace.get()


def current_step() -> Step | None:
    return _current_step.get()
