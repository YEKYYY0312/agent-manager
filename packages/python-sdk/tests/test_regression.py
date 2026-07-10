"""Tests for CI regression checks."""

from __future__ import annotations

from agent_devtools import Cost, Error, Step, Trace, new_run
from agent_devtools.regression import RegressionThresholds, check_regression


def _trace(
    *,
    status: str = "success",
    tokens: int = 100,
    cost: float = 0.001,
    duration_ms: float = 100,
    output: object = "answer",
    extra_step: bool = False,
) -> Trace:
    trace = new_run("Regression task")
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
        trace.run.complete(status=status, duration_ms=duration_ms)
    trace.add_step(step)

    if extra_step:
        extra = Step(type="tool_call", name="Extra lookup")
        extra.complete(status="success", output={"ok": True}, duration_ms=10)
        trace.add_step(extra)

    return trace


def _failed_check_names(report) -> set[str]:
    return {check.name for check in report.checks if not check.passed}


def test_check_regression_passes_when_candidate_is_within_thresholds() -> None:
    baseline = _trace(tokens=100, cost=0.001, duration_ms=100)
    candidate = _trace(tokens=110, cost=0.0015, duration_ms=140)

    report = check_regression(
        baseline,
        candidate,
        RegressionThresholds(
            max_token_delta=20,
            max_cost_delta_usd=0.001,
            max_latency_delta_ms=50,
            max_step_count_delta=0,
        ),
    )

    assert report.passed is True
    assert report.baseline_run_id == baseline.run.id
    assert report.candidate_run_id == candidate.run.id
    assert report.experiment.delta.token_delta == 10
    assert _failed_check_names(report) == set()


def test_check_regression_fails_when_successful_baseline_becomes_error() -> None:
    baseline = _trace(status="success")
    candidate = _trace(status="error")

    report = check_regression(baseline, candidate, RegressionThresholds())

    assert report.passed is False
    assert {"success_status", "failed_steps"} <= _failed_check_names(report)


def test_check_regression_fails_when_deltas_exceed_thresholds() -> None:
    baseline = _trace(tokens=100, cost=0.001, duration_ms=100)
    candidate = _trace(tokens=200, cost=0.004, duration_ms=800, extra_step=True)

    report = check_regression(
        baseline,
        candidate,
        RegressionThresholds(
            max_token_delta=50,
            max_cost_delta_usd=0.001,
            max_latency_delta_ms=100,
            max_step_count_delta=0,
        ),
    )

    assert report.passed is False
    assert {
        "token_delta",
        "cost_delta_usd",
        "latency_delta_ms",
        "step_count_delta",
    } <= _failed_check_names(report)


def test_check_regression_can_fail_on_output_change() -> None:
    baseline = _trace(output={"answer": "A"})
    candidate = _trace(output={"answer": "B"})

    report = check_regression(
        baseline,
        candidate,
        RegressionThresholds(allow_output_change=False),
    )

    assert report.passed is False
    assert "output_changed" in _failed_check_names(report)
