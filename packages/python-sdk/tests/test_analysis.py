"""Tests for the Agent DevTools analysis module."""

from __future__ import annotations

from time import perf_counter

from agent_devtools import Cost, Error, Step, Trace, new_run
from agent_devtools.analysis import (
    CostAnalysis,
    FailureAnalysis,
    LatencyAnalysis,
    LoopAnalysis,
    RetryAnalysis,
    TraceReport,
    analyze,
    analyze_cost,
    analyze_failures,
    analyze_latency,
    detect_loops,
    detect_retries,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trace(steps: list[Step], *, run_status: str = "success") -> Trace:
    t = new_run("test")
    t.run.complete(status=run_status)
    for s in steps:
        t.add_step(s)
    return t


# ---------------------------------------------------------------------------
# analyze_cost
# ---------------------------------------------------------------------------


class TestCostAnalysis:
    def test_empty(self) -> None:
        ca = analyze_cost(_make_trace([]))
        assert ca.total.total_tokens == 0
        assert ca.step_count == 0
        assert ca.steps_with_cost == 0

    def test_single_model(self) -> None:
        s = Step(type="model_call", name="llm", model="gpt-4", cost=Cost(input_tokens=10, total_tokens=10, amount_usd=0.05))
        s.complete()
        ca = analyze_cost(_make_trace([s]))
        assert ca.total.total_tokens == 10
        assert ca.total.amount_usd == 0.05
        assert ca.by_model["gpt-4"].total_tokens == 10
        assert len(ca.most_expensive) == 1

    def test_multi_model(self) -> None:
        s1 = Step(type="model_call", name="a", model="gpt-4", cost=Cost(input_tokens=5, total_tokens=5, amount_usd=0.01))
        s1.complete()
        s2 = Step(type="model_call", name="b", model="claude-sonnet", cost=Cost(input_tokens=10, total_tokens=10, amount_usd=0.02))
        s2.complete()
        ca = analyze_cost(_make_trace([s1, s2]))
        assert len(ca.by_model) == 2
        assert ca.by_model["gpt-4"].total_tokens == 5
        assert ca.by_model["claude-sonnet"].total_tokens == 10
        assert ca.most_expensive[0][0] == s2.id

    def test_by_step_type(self) -> None:
        s1 = Step(type="model_call", name="llm", cost=Cost(input_tokens=5, total_tokens=5, amount_usd=0.01))
        s1.complete()
        s2 = Step(type="tool_call", name="search", cost=Cost(input_tokens=0, total_tokens=0, amount_usd=0.0))
        s2.complete()
        ca = analyze_cost(_make_trace([s1, s2]))
        assert ca.steps_with_cost == 1  # only the model_call has tokens

    def test_steps_without_cost_ignored(self) -> None:
        s = Step(type="planner", name="plan")
        s.complete()
        ca = analyze_cost(_make_trace([s]))
        assert ca.steps_with_cost == 0
        assert ca.total.total_tokens == 0


# ---------------------------------------------------------------------------
# analyze_latency
# ---------------------------------------------------------------------------


class TestLatencyAnalysis:
    def test_empty(self) -> None:
        la = analyze_latency(_make_trace([]))
        assert la.step_count == 0
        assert la.total_ms == 0.0
        assert la.avg_ms == 0.0

    def test_single_step(self) -> None:
        s = Step(type="tool_call", name="search")
        s.complete(duration_ms=50.0)
        la = analyze_latency(_make_trace([s]))
        assert la.total_ms == 50.0
        assert la.avg_ms == 50.0
        assert la.by_step_type["tool_call"] == 50.0

    def test_slowest_ordering(self) -> None:
        s1 = Step(type="tool_call", name="fast")
        s1.complete(duration_ms=10.0)
        s2 = Step(type="model_call", name="slow")
        s2.complete(duration_ms=500.0)
        la = analyze_latency(_make_trace([s1, s2]))
        assert la.slowest[0][0] == s2.id
        assert la.slowest[0][1] == 500.0

    def test_null_duration_treated_as_zero(self) -> None:
        s = Step(type="planner", name="plan")
        la = analyze_latency(_make_trace([s]))
        assert la.total_ms == 0.0


# ---------------------------------------------------------------------------
# analyze_failures
# ---------------------------------------------------------------------------


class TestFailureAnalysis:
    def test_all_success_returns_none(self) -> None:
        s = Step(type="planner", name="plan")
        s.complete()
        assert analyze_failures(_make_trace([s])) is None

    def test_error_step(self) -> None:
        s = Step(type="tool_call", name="search")
        s.complete(status="error", error=Error(type="ValueError", message="boom"))
        fa = analyze_failures(_make_trace([s]))
        assert fa is not None
        assert fa.run_status == "success"  # run itself wasn't marked error
        assert len(fa.failed_steps) == 1
        assert fa.failed_steps[0].error_type == "ValueError"
        assert fa.failure_rate == 1.0

    def test_error_run_triggers_analysis(self) -> None:
        s = Step(type="planner", name="plan")
        s.complete()  # step itself is success
        fa = analyze_failures(_make_trace([s], run_status="error"))
        assert fa is not None
        assert fa.run_status == "error"
        assert len(fa.failed_steps) == 0  # no step errors

    def test_position_tracking(self) -> None:
        s1 = Step(type="planner", name="a")
        s1.complete()
        s2 = Step(type="tool_call", name="b")
        s2.complete(status="timeout", error=Error(message="timed out"))
        s3 = Step(type="tool_call", name="c")
        s3.complete(status="error", error=Error(message="fail"))
        fa = analyze_failures(_make_trace([s1, s2, s3]))
        assert fa is not None
        assert fa.failed_steps[0].position == 1
        assert fa.failed_steps[1].position == 2


# ---------------------------------------------------------------------------
# detect_loops
# ---------------------------------------------------------------------------


class TestLoopDetection:
    def test_no_loop_short_trace(self) -> None:
        la = detect_loops(_make_trace([]))
        assert not la.has_suspicious_loops

    def test_no_loop_diverse_steps(self) -> None:
        steps = []
        for name in ["plan", "search", "llm"]:
            s = Step(type="tool_call", name=name)
            s.complete()
            steps.append(s)
        la = detect_loops(_make_trace(steps))
        assert not la.has_suspicious_loops

    def test_detects_loop(self) -> None:
        steps = []
        for _ in range(4):
            s = Step(type="model_call", name="retry-llm")
            s.complete()
            steps.append(s)
        la = detect_loops(_make_trace(steps))
        assert la.has_suspicious_loops
        assert la.loops[0].count == 4
        assert la.loops[0].step_name == "retry-llm"

    def test_threshold_2_is_not_loop(self) -> None:
        steps = []
        for _ in range(2):
            s = Step(type="tool_call", name="fetch")
            s.complete()
            steps.append(s)
        la = detect_loops(_make_trace(steps))
        assert not la.has_suspicious_loops

    def test_two_separate_loops(self) -> None:
        steps = []
        for _ in range(3):
            s = Step(type="model_call", name="llm")
            s.complete()
            steps.append(s)
        for _ in range(3):
            s = Step(type="tool_call", name="search")
            s.complete()
            steps.append(s)
        la = detect_loops(_make_trace(steps))
        assert len(la.loops) == 2


# ---------------------------------------------------------------------------
# detect_retries
# ---------------------------------------------------------------------------


class TestRetryDetection:
    def test_no_retries(self) -> None:
        s = Step(type="tool_call", name="search")
        s.complete()
        assert detect_retries(_make_trace([s])).total_retry_chains == 0

    def test_detects_retry_chain(self) -> None:
        s1 = Step(type="tool_call", name="search")
        s1.complete(status="error", error=Error(message="fail"))
        s2 = Step(type="tool_call", name="search")
        s2.complete(status="success", output="found")
        ra = detect_retries(_make_trace([s1, s2]))
        assert ra.total_retry_chains == 1
        assert ra.retries[0].attempts == 2
        assert ra.retries[0].succeeded is True

    def test_failed_retry_chain(self) -> None:
        s1 = Step(type="tool_call", name="search")
        s1.complete(status="error", error=Error(message="fail1"))
        s2 = Step(type="tool_call", name="search")
        s2.complete(status="error", error=Error(message="fail2"))
        ra = detect_retries(_make_trace([s1, s2]))
        assert ra.total_retry_chains == 1
        assert ra.retries[0].succeeded is False

    def test_no_retry_when_first_succeeds(self) -> None:
        s1 = Step(type="tool_call", name="search")
        s1.complete(status="success")
        s2 = Step(type="tool_call", name="search")
        s2.complete(status="success")
        ra = detect_retries(_make_trace([s1, s2]))
        assert ra.total_retry_chains == 0


# ---------------------------------------------------------------------------
# analyze() unified report
# ---------------------------------------------------------------------------


class TestAnalyze:
    def test_success_trace(self) -> None:
        s = Step(type="model_call", name="llm", model="gpt-4-mini", cost=Cost(input_tokens=10, total_tokens=10, amount_usd=0.01))
        s.complete(duration_ms=100)
        t = _make_trace([s])
        report = analyze(t)
        assert isinstance(report, TraceReport)
        assert report.trace_id == t.run.id
        assert "1 steps" in report.summary
        assert "all passed" in report.summary

    def test_error_trace(self) -> None:
        s = Step(type="tool_call", name="search")
        s.complete(status="error", error=Error(message="fail"))
        t = _make_trace([s])
        report = analyze(t)
        assert "1 failed" in report.summary

    def test_summary_includes_loops(self) -> None:
        steps = []
        for _ in range(3):
            s = Step(type="model_call", name="loop")
            s.complete()
            steps.append(s)
        report = analyze(_make_trace(steps))
        assert "1 loops" in report.summary

    def test_large_trace_analysis_stays_linear(self) -> None:
        steps = []
        for i in range(1000):
            step = Step(
                type="model_call" if i % 2 == 0 else "tool_call",
                name=f"step-{i}",
                model="gpt-4.1-mini" if i % 2 == 0 else "",
                cost=Cost(input_tokens=1, output_tokens=1, total_tokens=2, amount_usd=0.000001),
            )
            step.complete(duration_ms=2)
            steps.append(step)

        started = perf_counter()
        report = analyze(_make_trace(steps))
        elapsed = perf_counter() - started

        assert report.cost.step_count == 1000
        assert report.cost.total.total_tokens == 2000
        assert report.latency.total_ms == 2000
        assert elapsed < 1.0
