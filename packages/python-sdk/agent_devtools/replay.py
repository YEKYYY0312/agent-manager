"""Deterministic replay trace generation.

The Phase 9 replay runner does not call external tools or models. It creates a
new trace from recorded steps so the CLI and Web UI can compare a replayed path
against the original run.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .adapters import AdapterRunResult, AgentAdapter
from .trace import Error, Run, Step, ToolCall, Trace

_UNSET = object()


def create_replay_trace(source: Trace, start_step_id: str, tool_mocks: list[dict[str, Any]] | None = None) -> Trace:
    """Create a new replay trace from *source*, starting at *start_step_id*.

    Tool calls reuse recorded tool results. Model calls reuse recorded outputs
    and cost data. Each replayed step receives a fresh id and metadata pointing
    back to the original step.
    """
    start_index = _find_start_index(source, start_step_id)
    source_steps = source.steps[start_index:]
    mock_by_step_id = _tool_mock_index(tool_mocks)
    replay_steps = [_replay_step(step, mock_by_step_id.get(step.id)) for step in source_steps]
    replay_ids_by_source_id = {
        source_step.id: replay_step.id
        for source_step, replay_step in zip(source_steps, replay_steps)
    }
    for source_step, replay_step in zip(source_steps, replay_steps):
        replay_step.parent_id = replay_ids_by_source_id.get(source_step.parent_id)

    labels = dict(source.run.labels)
    labels.update(
        {
            "replay": "true",
            "source_run_id": source.run.id,
            "source_start_step_id": start_step_id,
            "source_run_status": source.run.status,
        }
    )

    run = Run(
        task=f"Replay: {source.run.task}",
        labels=labels,
        final_output=_final_output(source, source_steps),
    )
    run.complete(status=_replay_status(source, replay_steps), final_output=run.final_output, duration_ms=_total_duration(replay_steps))

    return Trace(schema_version=source.schema_version, run=run, steps=replay_steps)


def replay_with_adapter(
    source: Trace,
    start_step_id: str,
    adapter: AgentAdapter,
    input: Any = _UNSET,
    *,
    output_dir: str = "traces",
    labels: dict[str, str] | None = None,
) -> AdapterRunResult:
    """Re-run a trace path by executing a real agent adapter.

    If *input* is omitted, the selected step's recorded input is used. Tool
    steps fall back to ``tool.args`` because older traces often store tool
    inputs there instead of in the generic step input field.
    """
    start_index = _find_start_index(source, start_step_id)
    start_step = source.steps[start_index]
    replay_input = _adapter_input(start_step) if input is _UNSET else deepcopy(input)

    replay_labels = dict(source.run.labels)
    if labels:
        replay_labels.update({str(key): str(value) for key, value in labels.items()})
    replay_labels.update(
        {
            "replay": "true",
            "replay_mode": "adapter_execution",
            "source_run_id": source.run.id,
            "source_start_step_id": start_step_id,
            "source_run_status": source.run.status,
        }
    )

    return adapter.run(
        task=f"Replay: {source.run.task}",
        input=replay_input,
        labels=replay_labels,
        output_dir=output_dir,
    )


def _find_start_index(source: Trace, start_step_id: str) -> int:
    for index, step in enumerate(source.steps):
        if step.id == start_step_id:
            return index
    raise ValueError(f"Start step not found: {start_step_id}")


def _replay_step(source: Step, tool_mock: dict[str, Any] | None = None) -> Step:
    step = Step(
        type=source.type,
        name=source.name,
        status=_mock_status(source, tool_mock),
        parent_id=source.parent_id,
        model=source.model,
        input=deepcopy(source.input),
        output=_recorded_output(source, tool_mock),
        tool=_replay_tool(source.tool, tool_mock),
        cost=deepcopy(source.cost),
        error=_replay_error(source.error, tool_mock),
        replayable=source.replayable,
        metadata={**deepcopy(source.metadata), "source_step_id": source.id, "replay_mode": _replay_mode(source, tool_mock)},
    )
    step.complete(
        status=step.status,
        output=step.output,
        error=step.error,
        cost=step.cost,
        duration_ms=source.duration_ms,
    )
    return step


def _replay_tool(tool: ToolCall | None, tool_mock: dict[str, Any] | None = None) -> ToolCall | None:
    if tool is None:
        return None
    args = tool_mock.get("args") if tool_mock is not None and "args" in tool_mock else tool.args
    result = tool_mock.get("result") if tool_mock is not None and "result" in tool_mock else tool.result
    return ToolCall(name=tool.name, args=deepcopy(args), result=deepcopy(result))


def _replay_error(error: Error | None, tool_mock: dict[str, Any] | None = None) -> Error | None:
    if tool_mock is not None and tool_mock.get("status") == "success":
        return None
    if error is None:
        return None
    return Error(type=error.type, message=error.message, stack=error.stack)


def _recorded_output(step: Step, tool_mock: dict[str, Any] | None = None) -> Any:
    if tool_mock is not None and "result" in tool_mock:
        return deepcopy(tool_mock["result"])
    if step.type == "tool_call" and step.tool is not None and step.tool.result is not None:
        return deepcopy(step.tool.result)
    return deepcopy(step.output)


def _adapter_input(step: Step) -> Any:
    if step.input is not None:
        return deepcopy(step.input)
    if step.tool is not None and step.tool.args is not None:
        return deepcopy(step.tool.args)
    return None


def _replay_mode(step: Step, tool_mock: dict[str, Any] | None = None) -> str:
    if tool_mock is not None:
        return "edited_tool_mock"
    if step.type == "tool_call":
        return "mocked_tool_result"
    if step.type == "model_call":
        return "recorded_model_output"
    return "recorded_step"


def _mock_status(step: Step, tool_mock: dict[str, Any] | None = None) -> str:
    if tool_mock is not None and isinstance(tool_mock.get("status"), str):
        return str(tool_mock["status"])
    return step.status


def _tool_mock_index(tool_mocks: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for mock in tool_mocks or []:
        step_id = mock.get("stepId") or mock.get("step_id")
        if isinstance(step_id, str) and step_id:
            index[step_id] = mock
    return index


def _replay_status(source: Trace, steps: list[Step]) -> str:
    if any(step.status != "success" for step in steps):
        return source.run.status if source.run.status != "success" else "error"
    return "success"


def _total_duration(steps: list[Step]) -> float:
    return sum(float(step.duration_ms or 0) for step in steps)


def _final_output(source: Trace, steps: list[Step]) -> Any:
    if source.run.final_output is not None and steps and steps[-1].id == source.steps[-1].id:
        return deepcopy(source.run.final_output)
    if steps:
        return deepcopy(steps[-1].output)
    return None
