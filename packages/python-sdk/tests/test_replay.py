"""Tests for deterministic replay trace generation."""

from __future__ import annotations

from agent_devtools import CallableAgentAdapter, Cost, Step, ToolCall, Trace, new_run
from agent_devtools.replay import create_replay_trace, replay_with_adapter
from agent_devtools.writer import TraceWriter


def _make_trace() -> Trace:
    trace = new_run("Replay weather task", labels={"scenario": "success"})
    trace.run.complete(status="success", final_output="The weather is warm.")

    plan = Step(type="planner", name="Create answer plan", replayable=True)
    plan.complete(status="success", output="Call weather tool, then summarize.")

    tool = Step(
        type="tool_call",
        name="weather.lookup",
        tool=ToolCall(name="weather.lookup", args={"city": "Shanghai"}, result={"summary": "warm"}),
        replayable=True,
    )
    tool.complete(status="success", output={"summary": "warm"})

    model = Step(
        type="model_call",
        name="Generate final answer",
        model="gpt-4.1-mini",
        cost=Cost(input_tokens=10, output_tokens=5, total_tokens=15, amount_usd=0.00002),
        replayable=True,
    )
    model.complete(status="success", output="The weather is warm.")

    trace.add_step(plan)
    trace.add_step(tool)
    trace.add_step(model)
    return trace


def test_create_replay_trace_starts_from_selected_step() -> None:
    source = _make_trace()
    replay = create_replay_trace(source, start_step_id=source.steps[1].id)

    assert replay.run.id != source.run.id
    assert replay.run.status == "success"
    assert replay.run.labels["replay"] == "true"
    assert replay.run.labels["source_run_id"] == source.run.id
    assert replay.run.labels["source_start_step_id"] == source.steps[1].id
    assert len(replay.steps) == 2
    assert replay.steps[0].name == "weather.lookup"
    assert replay.steps[1].name == "Generate final answer"


def test_create_replay_trace_uses_recorded_tool_and_model_outputs() -> None:
    source = _make_trace()
    replay = create_replay_trace(source, start_step_id=source.steps[0].id)

    tool_step = replay.steps[1]
    model_step = replay.steps[2]

    assert tool_step.metadata["replay_mode"] == "mocked_tool_result"
    assert tool_step.metadata["source_step_id"] == source.steps[1].id
    assert tool_step.tool.result == {"summary": "warm"}
    assert tool_step.output == {"summary": "warm"}

    assert model_step.metadata["replay_mode"] == "recorded_model_output"
    assert model_step.output == "The weather is warm."
    assert model_step.cost.total_tokens == 15


def test_create_replay_trace_applies_edited_tool_mocks() -> None:
    source = _make_trace()

    replay = create_replay_trace(
        source,
        start_step_id=source.steps[0].id,
        tool_mocks=[
            {
                "stepId": source.steps[1].id,
                "name": "weather.lookup",
                "args": {"city": "Shanghai"},
                "result": {"summary": "cold"},
            }
        ],
    )

    tool_step = replay.steps[1]

    assert tool_step.metadata["replay_mode"] == "edited_tool_mock"
    assert tool_step.tool.result == {"summary": "cold"}
    assert tool_step.output == {"summary": "cold"}


def test_create_replay_trace_rejects_unknown_start_step() -> None:
    source = _make_trace()

    try:
      create_replay_trace(source, start_step_id="missing-step")
    except ValueError as exc:
      assert "Start step not found" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_create_replay_trace_remaps_nested_parent_ids(tmp_path) -> None:
    source = new_run("Nested replay")
    parent = Step(type="planner", name="parent", replayable=True)
    child = Step(type="tool_call", name="child", parent_id=parent.id, replayable=True)
    source.add_step(parent)
    source.add_step(child)

    replay = create_replay_trace(source, start_step_id=parent.id)

    assert replay.steps[1].parent_id == replay.steps[0].id
    TraceWriter(tmp_path).write(replay)


def test_replay_with_adapter_executes_agent_from_selected_step(tmp_path) -> None:
    source = _make_trace()

    adapter = CallableAgentAdapter(
        lambda payload: {"replayed_city": payload["city"]},
        name="runtime-agent",
    )

    result = replay_with_adapter(
        source,
        start_step_id=source.steps[1].id,
        adapter=adapter,
        output_dir=str(tmp_path),
    )

    assert result.error is None
    assert result.output == {"replayed_city": "Shanghai"}
    assert result.trace.run.task == "Replay: Replay weather task"
    assert result.trace.run.status == "success"
    assert result.trace.run.labels["replay"] == "true"
    assert result.trace.run.labels["replay_mode"] == "adapter_execution"
    assert result.trace.run.labels["source_run_id"] == source.run.id
    assert result.trace.run.labels["source_start_step_id"] == source.steps[1].id
    assert result.trace.run.labels["adapter"] == "runtime-agent"
    assert result.trace.steps[0].input == {"city": "Shanghai"}
