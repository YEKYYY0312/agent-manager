"""CI regression checks for Agent DevTools traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .experiment import ExperimentReport, compare_experiment
from .trace import Trace


@dataclass(frozen=True)
class RegressionThresholds:
    max_token_delta: int | None = None
    max_cost_delta_usd: float | None = None
    max_latency_delta_ms: float | None = None
    max_step_count_delta: int | None = None
    allow_output_change: bool = True


@dataclass(frozen=True)
class RegressionCheck:
    name: str
    passed: bool
    detail: str
    actual: Any = None
    threshold: Any = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }
        if self.actual is not None:
            data["actual"] = self.actual
        if self.threshold is not None:
            data["threshold"] = self.threshold
        return data


@dataclass(frozen=True)
class RegressionReport:
    baseline_run_id: str
    candidate_run_id: str
    passed: bool
    checks: list[RegressionCheck]
    experiment: ExperimentReport

    def to_dict(self) -> dict[str, Any]:
        delta = self.experiment.delta
        return {
            "baseline_run_id": self.baseline_run_id,
            "candidate_run_id": self.candidate_run_id,
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "delta": {
                "token_delta": delta.token_delta,
                "cost_delta_usd": delta.cost_delta_usd,
                "latency_delta_ms": delta.latency_delta_ms,
                "step_count_delta": delta.step_count_delta,
                "output_changed": delta.output_changed,
            },
        }


def check_regression(
    baseline: Trace,
    candidate: Trace,
    thresholds: RegressionThresholds | None = None,
) -> RegressionReport:
    """Compare a baseline trace against a candidate trace for CI gates."""
    limits = thresholds or RegressionThresholds()
    experiment = compare_experiment(baseline, candidate)
    checks = [
        _status_check(experiment),
        _failed_steps_check(experiment),
        _max_delta_check(
            "token_delta",
            experiment.delta.token_delta,
            limits.max_token_delta,
            "Token delta",
        ),
        _max_delta_check(
            "cost_delta_usd",
            experiment.delta.cost_delta_usd,
            limits.max_cost_delta_usd,
            "Cost delta",
        ),
        _max_delta_check(
            "latency_delta_ms",
            experiment.delta.latency_delta_ms,
            limits.max_latency_delta_ms,
            "Latency delta",
        ),
        _max_delta_check(
            "step_count_delta",
            experiment.delta.step_count_delta,
            limits.max_step_count_delta,
            "Step count delta",
        ),
        _output_check(experiment.delta.output_changed, limits.allow_output_change),
    ]
    passed = all(check.passed for check in checks)
    return RegressionReport(
        baseline_run_id=experiment.left.trace_id,
        candidate_run_id=experiment.right.trace_id,
        passed=passed,
        checks=checks,
        experiment=experiment,
    )


def _status_check(report: ExperimentReport) -> RegressionCheck:
    baseline_rank = _status_rank(report.left.status)
    candidate_rank = _status_rank(report.right.status)
    passed = candidate_rank <= baseline_rank
    if passed:
        detail = f"Candidate status {report.right.status} is not worse than baseline {report.left.status}."
    else:
        detail = f"Candidate status regressed from {report.left.status} to {report.right.status}."
    return RegressionCheck(
        name="success_status",
        passed=passed,
        detail=detail,
        actual=report.right.status,
        threshold=report.left.status,
    )


def _failed_steps_check(report: ExperimentReport) -> RegressionCheck:
    delta = report.right.failed_steps - report.left.failed_steps
    passed = delta <= 0
    if passed:
        detail = f"Failed steps did not increase ({report.left.failed_steps} -> {report.right.failed_steps})."
    else:
        detail = f"Failed steps increased by {delta} ({report.left.failed_steps} -> {report.right.failed_steps})."
    return RegressionCheck(
        name="failed_steps",
        passed=passed,
        detail=detail,
        actual=report.right.failed_steps,
        threshold=report.left.failed_steps,
    )


def _max_delta_check(name: str, delta: int | float, threshold: int | float | None, label: str) -> RegressionCheck:
    if threshold is None:
        return RegressionCheck(
            name=name,
            passed=True,
            detail=f"{label} is {delta:+}; no threshold configured.",
            actual=delta,
        )

    passed = delta <= threshold
    if passed:
        detail = f"{label} {delta:+} is within threshold {threshold}."
    else:
        detail = f"{label} {delta:+} exceeds threshold {threshold}."
    return RegressionCheck(
        name=name,
        passed=passed,
        detail=detail,
        actual=delta,
        threshold=threshold,
    )


def _output_check(output_changed: bool, allow_output_change: bool) -> RegressionCheck:
    passed = allow_output_change or not output_changed
    if passed:
        detail = "Output change is allowed." if output_changed else "Output did not change."
    else:
        detail = "Output changed and allow_output_change is false."
    return RegressionCheck(
        name="output_changed",
        passed=passed,
        detail=detail,
        actual=output_changed,
        threshold=allow_output_change,
    )


def _status_rank(status: str) -> int:
    ranks = {
        "success": 0,
        "cancelled": 1,
        "timeout": 2,
        "error": 3,
    }
    return ranks.get(status, 3)
