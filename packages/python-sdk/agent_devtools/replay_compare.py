"""Compare replay traces against their original source trace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .trace import Cost, Step, Trace

ReplayChangeKind = Literal[
    "shape_changed",
    "status_changed",
    "output_changed",
    "cost_changed",
    "missing_replay_step",
    "extra_replay_step",
]


@dataclass
class ReplayComparisonDelta:
    token_delta: int
    cost_delta_usd: float
    latency_delta_ms: float
    step_count_delta: int


@dataclass
class ReplayStepChange:
    index: int
    kind: ReplayChangeKind
    source_step_id: str | None
    replay_step_id: str | None
    detail: str


@dataclass
class ReplayComparisonReport:
    source_run_id: str
    replay_run_id: str
    replay_source_run_id: str
    source_run_match: bool
    source_start_step_id: str
    replay_mode: str
    source_status: str
    replay_status: str
    source_step_count: int
    replay_step_count: int
    source_duration_ms: float
    replay_duration_ms: float
    source_tokens: int
    replay_tokens: int
    source_cost_usd: float
    replay_cost_usd: float
    status_changed: bool
    output_changed: bool
    delta: ReplayComparisonDelta
    step_changes: list[ReplayStepChange]


def compare_replay(source: Trace, replay: Trace) -> ReplayComparisonReport:
    """Compare a replay trace against the source path it claims to replay."""
    labels = replay.run.labels
    replay_source_run_id = labels.get("source_run_id", "")
    source_start_step_id = labels.get("source_start_step_id", "")
    replay_mode = labels.get("replay_mode") or ("deterministic" if labels.get("replay") == "true" else "unknown")

    source_steps = _source_slice(source, source_start_step_id)
    source_cost = _total_cost(source_steps)
    replay_cost = replay.total_cost()
    source_duration = _steps_duration(source_steps)
    replay_duration = _trace_duration(replay)
    source_status = _path_status(source, source_steps)
    replay_status = replay.run.status
    source_output = _path_output(source, source_steps)
    replay_output = _trace_output(replay)

    return ReplayComparisonReport(
        source_run_id=source.run.id,
        replay_run_id=replay.run.id,
        replay_source_run_id=replay_source_run_id,
        source_run_match=replay_source_run_id == source.run.id,
        source_start_step_id=source_start_step_id,
        replay_mode=replay_mode,
        source_status=source_status,
        replay_status=replay_status,
        source_step_count=len(source_steps),
        replay_step_count=len(replay.steps),
        source_duration_ms=source_duration,
        replay_duration_ms=replay_duration,
        source_tokens=source_cost.total_tokens,
        replay_tokens=replay_cost.total_tokens,
        source_cost_usd=source_cost.amount_usd,
        replay_cost_usd=replay_cost.amount_usd,
        status_changed=source_status != replay_status,
        output_changed=source_output != replay_output,
        delta=ReplayComparisonDelta(
            token_delta=replay_cost.total_tokens - source_cost.total_tokens,
            cost_delta_usd=replay_cost.amount_usd - source_cost.amount_usd,
            latency_delta_ms=replay_duration - source_duration,
            step_count_delta=len(replay.steps) - len(source_steps),
        ),
        step_changes=_step_changes(source_steps, replay.steps),
    )


def _source_slice(source: Trace, start_step_id: str) -> list[Step]:
    if not start_step_id:
        return list(source.steps)
    for index, step in enumerate(source.steps):
        if step.id == start_step_id:
            return source.steps[index:]
    raise ValueError(f"Replay source_start_step_id not found in source trace: {start_step_id}")


def _total_cost(steps: list[Step]) -> Cost:
    total = Cost()
    for step in steps:
        if step.cost is None:
            continue
        total.input_tokens += step.cost.input_tokens
        total.output_tokens += step.cost.output_tokens
        total.total_tokens += step.cost.total_tokens
        total.amount_usd += step.cost.amount_usd
    return total


def _steps_duration(steps: list[Step]) -> float:
    return sum(float(step.duration_ms or 0) for step in steps)


def _trace_duration(trace: Trace) -> float:
    if trace.run.duration_ms is not None:
        return float(trace.run.duration_ms)
    return _steps_duration(trace.steps)


def _path_status(source: Trace, steps: list[Step]) -> str:
    if any(step.status != "success" for step in steps):
        return source.run.status if source.run.status != "success" else "error"
    return "success"


def _path_output(source: Trace, steps: list[Step]) -> Any:
    if not steps:
        return source.run.final_output
    if source.run.final_output is not None and steps[-1].id == source.steps[-1].id:
        return source.run.final_output
    return _step_output(steps[-1])


def _trace_output(trace: Trace) -> Any:
    if trace.run.final_output is not None:
        return trace.run.final_output
    if trace.steps:
        return _step_output(trace.steps[-1])
    return None


def _step_output(step: Step) -> Any:
    if step.type == "tool_call" and step.tool is not None and step.tool.result is not None:
        return step.tool.result
    return step.output


def _step_changes(source_steps: list[Step], replay_steps: list[Step]) -> list[ReplayStepChange]:
    changes: list[ReplayStepChange] = []
    max_len = max(len(source_steps), len(replay_steps))

    for index in range(max_len):
        source = source_steps[index] if index < len(source_steps) else None
        replay = replay_steps[index] if index < len(replay_steps) else None

        if source is not None and replay is not None:
            _append_pair_changes(changes, index, source, replay)
        elif source is not None:
            changes.append(
                ReplayStepChange(
                    index=index,
                    kind="missing_replay_step",
                    source_step_id=source.id,
                    replay_step_id=None,
                    detail=f"missing replay step for {source.type}/{source.name}",
                )
            )
        elif replay is not None:
            changes.append(
                ReplayStepChange(
                    index=index,
                    kind="extra_replay_step",
                    source_step_id=None,
                    replay_step_id=replay.id,
                    detail=f"extra replay step {replay.type}/{replay.name}",
                )
            )

    return changes


def _append_pair_changes(changes: list[ReplayStepChange], index: int, source: Step, replay: Step) -> None:
    if source.type != replay.type or source.name != replay.name:
        changes.append(
            ReplayStepChange(
                index=index,
                kind="shape_changed",
                source_step_id=source.id,
                replay_step_id=replay.id,
                detail=f"{source.type}/{source.name} -> {replay.type}/{replay.name}",
            )
        )
    if source.status != replay.status:
        changes.append(
            ReplayStepChange(
                index=index,
                kind="status_changed",
                source_step_id=source.id,
                replay_step_id=replay.id,
                detail=f"{source.status} -> {replay.status}",
            )
        )
    if _step_output(source) != _step_output(replay):
        changes.append(
            ReplayStepChange(
                index=index,
                kind="output_changed",
                source_step_id=source.id,
                replay_step_id=replay.id,
                detail="step output changed",
            )
        )
    source_tokens = source.cost.total_tokens if source.cost else 0
    replay_tokens = replay.cost.total_tokens if replay.cost else 0
    source_cost = source.cost.amount_usd if source.cost else 0.0
    replay_cost = replay.cost.amount_usd if replay.cost else 0.0
    if source_tokens != replay_tokens or source_cost != replay_cost:
        changes.append(
            ReplayStepChange(
                index=index,
                kind="cost_changed",
                source_step_id=source.id,
                replay_step_id=replay.id,
                detail=f"{source_tokens}t/${source_cost:.6f} -> {replay_tokens}t/${replay_cost:.6f}",
            )
        )
