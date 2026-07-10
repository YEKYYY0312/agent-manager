"""A/B experiment comparison for Agent DevTools traces."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from .trace import Trace

Winner = Literal["A", "B", "tie"]


@dataclass
class ExperimentArm:
    label: str
    trace_id: str
    status: str
    step_count: int
    failed_steps: int
    duration_ms: float
    total_tokens: int
    cost_usd: float
    final_output: object


@dataclass
class ExperimentDelta:
    token_delta: int
    cost_delta_usd: float
    latency_delta_ms: float
    step_count_delta: int
    output_changed: bool


@dataclass
class ExperimentReport:
    left: ExperimentArm
    right: ExperimentArm
    delta: ExperimentDelta
    winner_by_success: Winner
    winner_by_cost: Winner
    winner_by_latency: Winner
    recommendation: Winner
    recommendation_reason: str


def compare_experiment(left: Trace, right: Trace) -> ExperimentReport:
    """Compare two traces as an A/B experiment."""
    left_arm = _arm("A", left)
    right_arm = _arm("B", right)
    delta = ExperimentDelta(
        token_delta=right_arm.total_tokens - left_arm.total_tokens,
        cost_delta_usd=right_arm.cost_usd - left_arm.cost_usd,
        latency_delta_ms=right_arm.duration_ms - left_arm.duration_ms,
        step_count_delta=right_arm.step_count - left_arm.step_count,
        output_changed=left_arm.final_output != right_arm.final_output,
    )

    winner_by_success = _success_winner(left_arm, right_arm)
    winner_by_cost = _lower_winner(left_arm.cost_usd, right_arm.cost_usd)
    winner_by_latency = _lower_winner(left_arm.duration_ms, right_arm.duration_ms)
    recommendation, reason = _recommend(winner_by_success, winner_by_cost, winner_by_latency)

    return ExperimentReport(
        left=left_arm,
        right=right_arm,
        delta=delta,
        winner_by_success=winner_by_success,
        winner_by_cost=winner_by_cost,
        winner_by_latency=winner_by_latency,
        recommendation=recommendation,
        recommendation_reason=reason,
    )


def _arm(label: str, trace: Trace) -> ExperimentArm:
    total = trace.total_cost()
    failed_steps = sum(1 for step in trace.steps if step.status != "success")
    return ExperimentArm(
        label=label,
        trace_id=trace.run.id,
        status=trace.run.status,
        step_count=len(trace.steps),
        failed_steps=failed_steps,
        duration_ms=_duration(trace),
        total_tokens=total.total_tokens,
        cost_usd=total.amount_usd,
        final_output=trace.run.final_output,
    )


def _duration(trace: Trace) -> float:
    if trace.run.duration_ms is not None:
        return float(trace.run.duration_ms)
    return sum(float(step.duration_ms or 0) for step in trace.steps)


def _success_winner(left: ExperimentArm, right: ExperimentArm) -> Winner:
    left_ok = left.status == "success" and left.failed_steps == 0
    right_ok = right.status == "success" and right.failed_steps == 0
    if left_ok == right_ok:
        return "tie"
    return "A" if left_ok else "B"


def _lower_winner(left_value: float, right_value: float) -> Winner:
    if math.isclose(left_value, right_value, rel_tol=1e-9, abs_tol=1e-12):
        return "tie"
    return "A" if left_value < right_value else "B"


def _recommend(success: Winner, cost: Winner, latency: Winner) -> tuple[Winner, str]:
    if success != "tie":
        return success, f"{success} has better success status."

    votes = {"A": 0, "B": 0}
    for winner in (cost, latency):
        if winner in votes:
            votes[winner] += 1

    if votes["A"] > votes["B"]:
        return "A", "A is cheaper and/or faster."
    if votes["B"] > votes["A"]:
        return "B", "B is cheaper and/or faster."
    return "tie", "No clear winner; review output quality manually."
