"""Analysis module for Agent DevTools — cost, latency, failure, loop, and retry analysis.

All functions are pure: they take a Trace and return a dataclass report.
No side effects, no new dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .trace import Cost, Step, StepType, Trace

# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


@dataclass
class CostAnalysis:
    total: Cost
    by_model: dict[str, Cost] = field(default_factory=dict)
    by_step_type: dict[str, Cost] = field(default_factory=dict)
    most_expensive: list[tuple[str, Cost]] = field(default_factory=list)
    step_count: int = 0
    steps_with_cost: int = 0


def analyze_cost(trace: Trace) -> CostAnalysis:
    """Aggregate cost across all steps, grouped by model and step type."""
    by_model: dict[str, Cost] = {}
    by_step_type: dict[str, Cost] = {}
    all_with_cost: list[tuple[str, Cost]] = []

    for step in trace.steps:
        c = step.cost
        if not c or c.total_tokens == 0:
            continue
        c.validate()

        m = step.model or "(unknown)"
        if m not in by_model:
            by_model[m] = Cost()
        acc = by_model[m]
        acc.input_tokens += c.input_tokens
        acc.output_tokens += c.output_tokens
        acc.total_tokens += c.total_tokens
        acc.amount_usd += c.amount_usd

        t = step.type
        if t not in by_step_type:
            by_step_type[t] = Cost()
        acc_t = by_step_type[t]
        acc_t.input_tokens += c.input_tokens
        acc_t.output_tokens += c.output_tokens
        acc_t.total_tokens += c.total_tokens
        acc_t.amount_usd += c.amount_usd

        all_with_cost.append((step.id, c))

    most_expensive = sorted(all_with_cost, key=lambda x: x[1].amount_usd, reverse=True)[:5]

    return CostAnalysis(
        total=trace.total_cost(),
        by_model=by_model,
        by_step_type=by_step_type,
        most_expensive=most_expensive,
        step_count=len(trace.steps),
        steps_with_cost=len(all_with_cost),
    )


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


@dataclass
class LatencyAnalysis:
    total_ms: float = 0.0
    by_step_type: dict[str, float] = field(default_factory=dict)
    slowest: list[tuple[str, float, str]] = field(default_factory=list)
    step_count: int = 0
    avg_ms: float = 0.0


def analyze_latency(trace: Trace) -> LatencyAnalysis:
    """Aggregate duration per step type and identify slowest steps."""
    by_step_type: dict[str, float] = {}
    all_durations: list[tuple[str, float, str]] = []
    total = 0.0

    for step in trace.steps:
        dur = step.duration_ms or 0.0
        total += dur

        t = step.type
        by_step_type[t] = by_step_type.get(t, 0.0) + dur

        all_durations.append((step.id, dur, step.name))

    slowest = sorted(all_durations, key=lambda x: x[1], reverse=True)[:5]

    n = len(trace.steps)
    return LatencyAnalysis(
        total_ms=total,
        by_step_type=by_step_type,
        slowest=slowest,
        step_count=n,
        avg_ms=total / n if n > 0 else 0.0,
    )


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


@dataclass
class FailurePoint:
    step_id: str
    step_name: str
    step_type: str
    status: str
    error_type: str = ""
    error_message: str = ""
    position: int = 0


@dataclass
class FailureAnalysis:
    run_status: str = "success"
    total_steps: int = 0
    failed_steps: list[FailurePoint] = field(default_factory=list)
    failure_rate: float = 0.0


def analyze_failures(trace: Trace) -> FailureAnalysis | None:
    """Find all steps with non-success status. Returns None if everything passed."""
    total = len(trace.steps)
    if total == 0:
        return FailureAnalysis(run_status=trace.run.status)

    failed: list[FailurePoint] = []
    for i, step in enumerate(trace.steps):
        if step.status != "success":
            et = ""
            em = ""
            if step.error:
                et = step.error.type
                em = step.error.message
            failed.append(FailurePoint(
                step_id=step.id,
                step_name=step.name,
                step_type=step.type,
                status=step.status,
                error_type=et,
                error_message=em,
                position=i,
            ))

    if not failed and trace.run.status == "success":
        return None  # all good

    return FailureAnalysis(
        run_status=trace.run.status,
        total_steps=total,
        failed_steps=failed,
        failure_rate=len(failed) / total if total > 0 else 0.0,
    )


# ---------------------------------------------------------------------------
# Loop detection
# ---------------------------------------------------------------------------


@dataclass
class LoopCandidate:
    step_name: str
    step_type: str
    count: int
    first_index: int
    last_index: int


@dataclass
class LoopAnalysis:
    loops: list[LoopCandidate] = field(default_factory=list)
    has_suspicious_loops: bool = False


def detect_loops(trace: Trace, threshold: int = 3) -> LoopAnalysis:
    """Detect consecutive steps with the same name and type (3+ repetitions)."""
    if len(trace.steps) < threshold:
        return LoopAnalysis()

    loops: list[LoopCandidate] = []
    i = 0
    while i < len(trace.steps):
        s = trace.steps[i]
        j = i + 1
        while j < len(trace.steps) and trace.steps[j].name == s.name and trace.steps[j].type == s.type:
            j += 1
        run_len = j - i
        if run_len >= threshold:
            loops.append(LoopCandidate(
                step_name=s.name,
                step_type=s.type,
                count=run_len,
                first_index=i,
                last_index=j - 1,
            ))
        i = j

    return LoopAnalysis(loops=loops, has_suspicious_loops=len(loops) > 0)


# ---------------------------------------------------------------------------
# Retry detection
# ---------------------------------------------------------------------------


@dataclass
class RetryCandidate:
    step_name: str
    step_type: str
    attempts: int
    first_index: int
    succeeded: bool


@dataclass
class RetryAnalysis:
    retries: list[RetryCandidate] = field(default_factory=list)
    total_retry_chains: int = 0


def detect_retries(trace: Trace) -> RetryAnalysis:
    """Detect retry patterns: same-name steps where earlier attempts failed."""
    if len(trace.steps) < 2:
        return RetryAnalysis()

    retries: list[RetryCandidate] = []
    visited: set[int] = set()

    for i, step in enumerate(trace.steps):
        if i in visited:
            continue
        if step.status != "error":
            continue

        # Look ahead for same-name retries
        chain = [step]
        for j in range(i + 1, len(trace.steps)):
            nxt = trace.steps[j]
            if nxt.name == step.name and nxt.type == step.type:
                chain.append(nxt)
                visited.add(j)
            else:
                break

        if len(chain) >= 2:
            last = chain[-1]
            retries.append(RetryCandidate(
                step_name=step.name,
                step_type=step.type,
                attempts=len(chain),
                first_index=i,
                succeeded=last.status == "success",
            ))
        visited.add(i)

    return RetryAnalysis(retries=retries, total_retry_chains=len(retries))


# ---------------------------------------------------------------------------
# Unified report
# ---------------------------------------------------------------------------


@dataclass
class TraceReport:
    trace_id: str
    cost: CostAnalysis
    latency: LatencyAnalysis
    failure: FailureAnalysis | None
    loops: LoopAnalysis
    retries: RetryAnalysis
    summary: str = ""


def analyze(trace: Trace) -> TraceReport:
    """Run all analyzers and return a unified report."""
    cost = analyze_cost(trace)
    latency = analyze_latency(trace)
    failure = analyze_failures(trace)
    loops = detect_loops(trace)
    retries = detect_retries(trace)

    parts: list[str] = []
    parts.append(f"{len(trace.steps)} steps")
    parts.append(f"{cost.total.total_tokens}t/${cost.total.amount_usd:.4f}")
    parts.append(f"{latency.total_ms:.0f}ms")
    if failure:
        parts.append(f"{len(failure.failed_steps)} failed")
    else:
        parts.append("all passed")
    if loops.has_suspicious_loops:
        parts.append(f"{len(loops.loops)} loops")
    if retries.total_retry_chains:
        parts.append(f"{retries.total_retry_chains} retries")

    return TraceReport(
        trace_id=trace.run.id,
        cost=cost,
        latency=latency,
        failure=failure,
        loops=loops,
        retries=retries,
        summary=", ".join(parts),
    )
