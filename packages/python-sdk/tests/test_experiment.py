"""Tests for A/B experiment comparison."""

from __future__ import annotations

from agent_devtools import Cost, Error, Step, Trace, new_run
from agent_devtools.experiment import compare_experiment


def _trace(
    *,
    status: str = "success",
    tokens: int = 100,
    cost: float = 0.001,
    duration_ms: float = 100,
    output: str = "answer",
) -> Trace:
    trace = new_run("Experiment task")
    step = Step(
        type="model_call",
        name="Generate answer",
        model="gpt-4.1-mini",
        cost=Cost(input_tokens=tokens, output_tokens=0, total_tokens=tokens, amount_usd=cost),
    )
    if status == "success":
        step.complete(status="success", output=output, duration_ms=duration_ms)
        trace.run.complete(status="success", final_output=output, duration_ms=duration_ms)
    else:
        step.complete(status="error", error=Error(type="ModelError", message="failed"), duration_ms=duration_ms)
        trace.run.complete(status="error", duration_ms=duration_ms)
    trace.add_step(step)
    return trace


def test_compare_experiment_prefers_success_over_cost() -> None:
    left = _trace(status="error", tokens=10, cost=0.0001, duration_ms=10)
    right = _trace(status="success", tokens=100, cost=0.001, duration_ms=100)

    report = compare_experiment(left, right)

    assert report.winner_by_success == "B"
    assert report.winner_by_cost == "A"
    assert report.recommendation == "B"
    assert "success status" in report.recommendation_reason


def test_compare_experiment_reports_cost_latency_and_output_delta() -> None:
    left = _trace(tokens=50, cost=0.0005, duration_ms=80, output="short answer")
    right = _trace(tokens=100, cost=0.0010, duration_ms=120, output="long answer")

    report = compare_experiment(left, right)

    assert report.winner_by_success == "tie"
    assert report.winner_by_cost == "A"
    assert report.winner_by_latency == "A"
    assert report.delta.token_delta == 50
    assert report.delta.cost_delta_usd == 0.0005
    assert report.delta.latency_delta_ms == 40
    assert report.delta.output_changed is True
    assert report.recommendation == "A"
