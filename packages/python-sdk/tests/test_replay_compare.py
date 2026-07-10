"""Tests for replay-to-source comparison reports."""

from __future__ import annotations

from agent_devtools import Cost, Step, Trace, create_replay_trace, new_run
from agent_devtools.replay_compare import compare_replay


def _source_trace() -> Trace:
    trace = new_run("Replay compare task")

    plan = Step(type="planner", name="plan")
    plan.complete(status="success", output="use lookup", duration_ms=10)

    tool = Step(type="tool_call", name="weather.lookup")
    tool.complete(status="success", output={"temp_c": 21}, duration_ms=20)

    model = Step(
        type="model_call",
        name="answer",
        model="gpt-4.1-mini",
        cost=Cost(input_tokens=10, output_tokens=5, total_tokens=15, amount_usd=0.00002),
    )
    model.complete(status="success", output="21C", duration_ms=30)

    trace.add_step(plan)
    trace.add_step(tool)
    trace.add_step(model)
    trace.run.complete(status="success", final_output="21C", duration_ms=60)
    return trace


def test_compare_replay_uses_source_start_step_slice() -> None:
    source = _source_trace()
    replay = create_replay_trace(source, start_step_id=source.steps[1].id)

    report = compare_replay(source, replay)

    assert report.source_run_match is True
    assert report.source_start_step_id == source.steps[1].id
    assert report.replay_mode == "deterministic"
    assert report.source_step_count == 2
    assert report.replay_step_count == 2
    assert report.delta.step_count_delta == 0
    assert report.delta.token_delta == 0
    assert report.status_changed is False
    assert report.output_changed is False
    assert report.step_changes == []


def test_compare_replay_reports_status_and_output_drift() -> None:
    source = _source_trace()
    replay = create_replay_trace(source, start_step_id=source.steps[1].id)
    replay.run.complete(status="error", final_output="different answer", duration_ms=90)
    replay.steps[-1].complete(status="error", output="different answer", duration_ms=60)

    report = compare_replay(source, replay)

    assert report.status_changed is True
    assert report.output_changed is True
    assert report.delta.latency_delta_ms == 40
    assert any(change.kind == "status_changed" for change in report.step_changes)
    assert any(change.kind == "output_changed" for change in report.step_changes)


def test_compare_replay_marks_source_run_mismatch() -> None:
    source = _source_trace()
    replay = create_replay_trace(source, start_step_id=source.steps[1].id)
    replay.run.labels["source_run_id"] = "other-run"

    report = compare_replay(source, replay)

    assert report.source_run_match is False
