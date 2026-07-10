"""Decorators for tracing agent functions — model calls, tool calls, etc.

Each decorator auto-records a step in the current trace context with timing,
input, output, cost, and errors. Works standalone or alongside TraceContext
context managers.

Usage::

    ctx = TraceContext(task="Demo")

    @traced_model("generate", model="gpt-4.1-mini")
    def generate(prompt: str) -> str: ...

    @traced_tool("web_search")
    def search(query: str) -> dict: ...

    @traced_step("planner", "plan")
    def plan(task: str) -> str: ...
"""

from __future__ import annotations

import functools
import sys
import time
from typing import Any, Callable

from .context import current_trace
from .trace import Cost, Error as TraceError, Step, StepType, ToolCall


def traced_step(
    type: StepType,
    name: str = "",
    *,
    model: str = "",
    replayable: bool = True,
) -> Callable:
    """Record every invocation as a step in the current trace."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            trace = current_trace()
            if trace is None:
                return fn(*args, **kwargs)

            step_name = name or fn.__name__
            step_input = _serialize_input(args, kwargs)
            step = Step(
                type=type,
                name=step_name,
                input=step_input,
                model=model,
                replayable=replayable,
            )
            trace.add_step(step)
            t0 = time.perf_counter()

            try:
                result = fn(*args, **kwargs)
            except Exception:
                elapsed = (time.perf_counter() - t0) * 1000
                step.complete(status="error", error=TraceError.from_exc(sys.exc_info()[1]), duration_ms=elapsed)
                raise

            elapsed = (time.perf_counter() - t0) * 1000
            step.complete(status="success", output=_serialize_output(result), duration_ms=elapsed)
            return result

        return wrapper

    return decorator


def traced_model(
    name: str = "",
    *,
    model: str = "",
    replayable: bool = True,
    track_cost: bool = True,
) -> Callable:
    """Record a model/LLM call, with optional cost from the return value.

    If the wrapped function returns a dict or object with token usage fields
    (``usage``, ``input_tokens``/``output_tokens``, or if it's an OpenAI-style
    response object), cost is extracted automatically.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            trace = current_trace()
            if trace is None:
                return fn(*args, **kwargs)

            step_name = name or fn.__name__
            step_input = _serialize_input(args, kwargs)
            step = Step(
                type="model_call",
                name=step_name,
                input=step_input,
                model=model,
                replayable=replayable,
            )
            trace.add_step(step)
            t0 = time.perf_counter()

            try:
                result = fn(*args, **kwargs)
            except Exception:
                elapsed = (time.perf_counter() - t0) * 1000
                step.complete(status="error", error=TraceError.from_exc(sys.exc_info()[1]), duration_ms=elapsed)
                raise

            elapsed = (time.perf_counter() - t0) * 1000
            output = _serialize_output(result)
            cost = _extract_cost(result, model=model) if track_cost else None
            step.complete(status="success", output=output, duration_ms=elapsed, cost=cost)
            return result

        return wrapper

    return decorator


def traced_tool(
    name: str = "",
    *,
    replayable: bool = True,
) -> Callable:
    """Record a tool call in the trace — like ``traced_step(type="tool_call")``
    but auto-fills the ``tool`` field with name, args, and result."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            trace = current_trace()
            if trace is None:
                return fn(*args, **kwargs)

            tool_name = name or fn.__name__
            tool_args = _serialize_input(args, kwargs)
            step = Step(
                type="tool_call",
                name=tool_name,
                input=tool_args,
                tool=ToolCall(name=tool_name, args=tool_args),
                replayable=replayable,
            )
            trace.add_step(step)
            t0 = time.perf_counter()

            try:
                result = fn(*args, **kwargs)
            except Exception:
                elapsed = (time.perf_counter() - t0) * 1000
                step.tool.result = None
                step.complete(status="error", error=TraceError.from_exc(sys.exc_info()[1]), duration_ms=elapsed)
                raise

            elapsed = (time.perf_counter() - t0) * 1000
            serialized = _serialize_output(result)
            step.tool.result = serialized
            step.complete(status="success", output=serialized, duration_ms=elapsed)
            return result

        return wrapper

    return decorator


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _serialize_input(args: tuple, kwargs: dict) -> Any:
    """Build a display-friendly input from positional and keyword args."""
    if not kwargs and len(args) == 1:
        val = args[0]
        return val if isinstance(val, (str, dict, list, int, float, bool, type(None))) else str(val)
    parts: dict[str, Any] = {}
    if args:
        parts["args"] = list(args)
    if kwargs:
        parts["kwargs"] = {k: _safe(v) for k, v in kwargs.items()}
    return parts if parts else ""


def _serialize_output(result: Any) -> Any:
    """Best-effort output serialization."""
    if isinstance(result, (str, dict, list, int, float, bool, type(None))):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "__dict__"):
        return str(result)
    return str(result)


def _safe(val: Any) -> Any:
    if isinstance(val, (str, int, float, bool, type(None))):
        return val
    return str(val)


# Approximate USD pricing per 1M tokens (input / output). Used as fallback
# when the response doesn't include a dollar amount directly.
_MODEL_RATES: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.04, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-opus-5": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku": (0.25, 1.25),
    "claude-sonnet": (3.00, 15.00),
    "claude-opus": (15.00, 75.00),
}


def _compute_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _MODEL_RATES.get(model, (0.0, 0.0))
    input_rate, output_rate = rates
    if input_rate == 0.0 and output_rate == 0.0:
        return 0.0
    return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate


def _extract_cost(result: Any, model: str = "") -> Cost | None:
    """Try to extract token usage from common LLM response shapes."""
    usage = None
    if isinstance(result, dict):
        usage = result.get("usage")
    elif hasattr(result, "usage") and not callable(getattr(result, "usage", None)):
        usage = result.usage
    elif hasattr(result, "model_dump"):
        usage = result.model_dump().get("usage")

    if hasattr(usage, "prompt_tokens"):
        cost = Cost(
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            total_tokens=getattr(usage, "total_tokens", 0),
        )
        if cost.amount_usd == 0.0:
            cost.amount_usd = _compute_usd(model, cost.input_tokens, cost.output_tokens)
        return cost
    if isinstance(usage, dict):
        cost = Cost(
            input_tokens=usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0),
            output_tokens=usage.get("output_tokens", 0) or usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            amount_usd=usage.get("cost", 0) or usage.get("amount_usd", 0),
        )
        if cost.amount_usd == 0.0:
            cost.amount_usd = _compute_usd(model, cost.input_tokens, cost.output_tokens)
        return cost
    return None
