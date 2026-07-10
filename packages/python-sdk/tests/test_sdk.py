"""Tests for the Agent DevTools Python SDK."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent_devtools import (
    Cost,
    Error,
    Event,
    Run,
    Step,
    ToolCall,
    Trace,
    TraceContext,
    TraceWriter,
    new_run,
    traced_model,
    traced_step,
    traced_tool,
)

# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


class TestCost:
    def test_defaults(self) -> None:
        c = Cost()
        assert c.input_tokens == 0
        assert c.output_tokens == 0
        assert c.total_tokens == 0
        assert c.amount_usd == 0.0

    def test_roundtrip(self) -> None:
        c = Cost(input_tokens=100, output_tokens=200, total_tokens=300, amount_usd=0.05)
        data = c.to_dict()
        c2 = Cost.from_dict(data)
        assert c2.input_tokens == 100
        assert c2.output_tokens == 200
        assert c2.total_tokens == 300
        assert c2.amount_usd == 0.05

    def test_from_none(self) -> None:
        assert Cost.from_dict(None) == Cost()


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class TestError:
    def test_roundtrip(self) -> None:
        e = Error(type="ValueError", message="bad input", stack="line 1\nline 2")
        d = e.to_dict()
        e2 = Error.from_dict(d)
        assert e2.type == "ValueError"
        assert e2.message == "bad input"
        assert "line 2" in e2.stack

    def test_from_none(self) -> None:
        assert Error.from_dict(None) is None

    def test_from_exc(self) -> None:
        try:
            raise ValueError("test")
        except ValueError as exc:
            e = Error.from_exc(exc)
        assert e.type == "ValueError"
        assert e.message == "test"
        assert "ValueError" in e.stack

    def test_minimal(self) -> None:
        e = Error(message="fail")
        d = e.to_dict()
        assert d["message"] == "fail"
        assert "type" not in d
        assert "stack" not in d


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------


class TestToolCall:
    def test_roundtrip(self) -> None:
        tc = ToolCall(name="weather.lookup", args={"city": "Shanghai"}, result={"summary": "warm"})
        d = tc.to_dict()
        tc2 = ToolCall.from_dict(d)
        assert tc2.name == "weather.lookup"
        assert tc2.args == {"city": "Shanghai"}
        assert tc2.result == {"summary": "warm"}

    def test_from_none(self) -> None:
        assert ToolCall.from_dict(None) is None


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class TestStep:
    def test_minimal(self) -> None:
        s = Step(type="planner", name="plan")
        assert s.id
        assert s.started_at
        assert s.status == "success"

    def test_complete_success(self) -> None:
        s = Step(type="tool_call", name="search")
        s.complete(status="success", output="found")
        assert s.ended_at is not None
        assert s.duration_ms is not None
        assert s.output == "found"
        assert s.status == "success"

    def test_complete_error(self) -> None:
        s = Step(type="tool_call", name="search")
        err = Error(type="Timeout", message="timed out")
        s.complete(status="timeout", error=err)
        assert s.status == "timeout"
        assert s.error.type == "Timeout"

    def test_complete_with_cost(self) -> None:
        s = Step(type="model_call", name="llm")
        c = Cost(input_tokens=10, output_tokens=5, total_tokens=15, amount_usd=0.001)
        s.complete(status="success", cost=c)
        assert s.cost.total_tokens == 15

    def test_nested_parent_id(self) -> None:
        parent = Step(type="planner", name="plan")
        child = Step(type="tool_call", name="search", parent_id=parent.id)
        assert child.parent_id == parent.id

    def test_events(self) -> None:
        s = Step(type="model_call", name="llm")
        s.add_event("log", "starting")
        s.add_event("log", "done")
        assert len(s.events) == 2
        assert s.events[0].type == "log"

    def test_replayable_default(self) -> None:
        s = Step(type="model_call", name="llm")
        assert s.replayable is False

    def test_roundtrip(self) -> None:
        s = Step(
            type="tool_call",
            name="weather.lookup",
            status="success",
            tool=ToolCall(name="weather.lookup", args={"city": "Shanghai"}, result={"summary": "warm"}),
            cost=Cost(input_tokens=0, output_tokens=0, total_tokens=0, amount_usd=0),
            replayable=True,
        )
        s.complete(status="success")
        d = s.to_dict()
        s2 = Step.from_dict(d)
        assert s2.type == "tool_call"
        assert s2.tool.name == "weather.lookup"
        assert s2.replayable is True


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class TestRun:
    def test_minimal(self) -> None:
        r = Run(task="test task")
        assert r.id
        assert r.task == "test task"
        assert r.status == "success"

    def test_complete(self) -> None:
        r = Run(task="test")
        r.complete(status="success", final_output="answer")
        assert r.ended_at is not None
        assert r.final_output == "answer"

    def test_roundtrip(self) -> None:
        r = Run(task="test", labels={"env": "test"})
        r.complete(status="success", final_output="done")
        d = r.to_dict()
        r2 = Run.from_dict(d)
        assert r2.task == "test"
        assert r2.labels == {"env": "test"}
        assert r2.final_output == "done"


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class TestTrace:
    def test_new_run(self) -> None:
        t = new_run("Test task", labels={"env": "test"})
        assert t.run.task == "Test task"
        assert t.run.labels == {"env": "test"}
        assert t.steps == []

    def test_add_step(self) -> None:
        t = new_run("task")
        s = t.add_step(Step(type="custom", name="test"))
        assert len(t.steps) == 1
        assert s in t.steps

    def test_total_cost_aggregates_steps(self) -> None:
        t = new_run("task")
        t.add_step(Step(type="model_call", name="a", cost=Cost(input_tokens=10, total_tokens=10, amount_usd=0.01)))
        t.add_step(Step(type="model_call", name="b", cost=Cost(input_tokens=20, total_tokens=20, amount_usd=0.02)))
        tc = t.total_cost()
        assert tc.input_tokens == 30
        assert tc.amount_usd == 0.03

    def test_total_cost_fallback_run_when_steps_empty(self) -> None:
        t = new_run("task")
        t.run.cost = Cost(input_tokens=5, total_tokens=5, amount_usd=0.005)
        tc = t.total_cost()
        assert tc.input_tokens == 5

    def test_total_cost_ignores_run_when_steps_have_cost(self) -> None:
        t = new_run("task")
        t.run.cost = Cost(input_tokens=999, total_tokens=999, amount_usd=9.99)
        t.add_step(Step(type="model_call", name="a", cost=Cost(input_tokens=10, total_tokens=10, amount_usd=0.01)))
        tc = t.total_cost()
        assert tc.input_tokens == 10
        assert tc.amount_usd == 0.01

    def test_to_dict_schema_version(self) -> None:
        t = new_run("task")
        d = t.to_dict()
        assert d["schema_version"] == "0.1.0"
        assert "run" in d
        assert "steps" in d

    def test_roundtrip(self) -> None:
        t = new_run("task", labels={"env": "test"})
        t.add_step(Step(type="planner", name="plan"))
        t.add_step(Step(type="tool_call", name="search"))
        t.run.complete(status="success", final_output="done")
        d = t.to_dict()
        t2 = Trace.from_dict(d)
        assert t2.run.task == "task"
        assert t2.run.labels == {"env": "test"}
        assert len(t2.steps) == 2


# ---------------------------------------------------------------------------
# TraceWriter
# ---------------------------------------------------------------------------


class TestTraceWriter:
    def test_new_ids_use_full_uuid_entropy(self) -> None:
        trace = new_run("test")
        step = Step(type="planner", name="plan")

        assert len(trace.run.id) == 32
        assert len(step.id) == 32

    def test_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            w = TraceWriter(tmp)
            t = new_run("test")
            path = w.write(t)
            assert path.exists()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["schema_version"] == "0.1.0"
            assert data["run"]["task"] == "test"

    def test_write_sanitizes_default_filename_from_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            w = TraceWriter(tmp)
            t = new_run("test")
            t.run.id = "../escape"

            path = w.write(t)

            assert path.parent.resolve() == Path(tmp).resolve()
            assert path.name == "escape.trace.json"
            assert path.exists()

    def test_write_rejects_filename_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            w = TraceWriter(tmp)

            with pytest.raises(ValueError, match="inside output_dir"):
                w.write(new_run("test"), filename="../escape.trace.json")

    def test_write_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            w = TraceWriter(tmp)
            t = new_run("test")
            path = w.write_atomic(t)
            assert path.exists()
            # No .tmp file left behind
            tmps = list(Path(tmp).glob("*.tmp"))
            assert len(tmps) == 0

    def test_trace_from_file_rejects_oversized_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.trace.json"
            path.write_text('{"run":{},"steps":[]}', encoding="utf-8")

            with pytest.raises(ValueError, match="exceeds maximum"):
                Trace.from_file(str(path), max_bytes=5)

    def test_rejects_invalid_run_fields(self) -> None:
        from agent_devtools.writer import _validate_structure

        with pytest.raises(ValueError, match="missing 'run'"):
            _validate_structure({})

    def test_rejects_non_dict(self) -> None:
        from agent_devtools.writer import _validate_structure

        with pytest.raises(ValueError, match="must be a JSON object"):
            _validate_structure([])

    def test_rejects_missing_steps(self) -> None:
        from agent_devtools.writer import _validate_structure

        with pytest.raises(ValueError, match="missing 'steps'"):
            _validate_structure({"run": {"id": "x", "task": "t", "status": "success", "started_at": "now"}})

    def test_rejects_step_missing_id(self) -> None:
        from agent_devtools.writer import _validate_structure

        with pytest.raises(ValueError, match="missing required field"):
            _validate_structure(
                {
                    "run": {"id": "x", "task": "t", "status": "success", "started_at": "now"},
                    "steps": [{"type": "custom"}],
                }
            )


# ---------------------------------------------------------------------------
# TraceContext
# ---------------------------------------------------------------------------


class TestTraceContext:
    def test_success_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp) as ctx:
                with ctx.step("planner", "plan") as s:
                    s.complete(output="do X")
                with ctx.step("tool_call", "search") as s:
                    s.complete(output="found")

            traces = list(Path(tmp).glob("*.trace.json"))
            assert len(traces) == 1
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            assert data["run"]["status"] == "success"
            assert data["run"]["ended_at"] is not None
            assert data["run"]["duration_ms"] is not None
            assert len(data["steps"]) == 2

    def test_error_scenario_preserves_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with TraceContext(task="will fail", output_dir=tmp) as ctx:
                    with ctx.step("planner", "plan") as s:
                        s.complete(output="do X")
                    raise ValueError("something broke")
            except ValueError:
                pass

            traces = list(Path(tmp).glob("*.trace.json"))
            assert len(traces) == 1
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            assert data["run"]["status"] == "error"
            # Partial trace still has the planner step
            assert len(data["steps"]) == 1

    def test_model_call_convenience(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp) as ctx:
                with ctx.model_call("llm", model="gpt-4") as s:
                    s.complete(output="hello", cost=Cost(input_tokens=5, total_tokens=5))
            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            assert data["steps"][0]["type"] == "model_call"

    def test_tool_call_convenience(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp) as ctx:
                with ctx.tool_call("weather.lookup", args={"city": "Shanghai"}) as s:
                    s.complete(output={"summary": "warm"})
            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            step = data["steps"][0]
            assert step["type"] == "tool_call"
            assert step["tool"]["name"] == "weather.lookup"
            assert step["tool"]["args"] == {"city": "Shanghai"}
            assert step["tool"]["result"] == {"summary": "warm"}

    def test_tool_call_failure_preserves_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp) as ctx:
                with ctx.tool_call("weather.lookup", args={"city": "Mars"}) as s:
                    s.complete(status="error", error=Error(type="ToolTimeout", message="timed out"))
            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            step = data["steps"][0]
            assert step["type"] == "tool_call"
            assert step["tool"]["name"] == "weather.lookup"
            assert step["tool"]["args"] == {"city": "Mars"}
            assert step["tool"]["result"] is None
            assert step["error"]["type"] == "ToolTimeout"

    def test_run_auto_completes_on_normal_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="auto-complete test", output_dir=tmp) as ctx:
                with ctx.step("planner", "plan") as s:
                    s.complete(output="done")
            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            assert data["run"]["status"] == "success"
            assert data["run"]["ended_at"] is not None
            assert data["run"]["duration_ms"] is not None
            # auto-complete doesn't set final_output — key is absent


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


class TestDecorators:
    def test_traced_step_outside_context_is_noop(self) -> None:
        @traced_step("planner", "plan")
        def plan(task: str) -> str:
            return f"plan for {task}"

        result = plan("test")
        assert result == "plan for test"

    def test_traced_step_inside_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp):

                @traced_step("planner", "plan")
                def plan(task: str) -> str:
                    return f"plan for {task}"

                plan("test")

            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            assert len(data["steps"]) == 1
            assert data["steps"][0]["type"] == "planner"
            assert data["steps"][0]["name"] == "plan"
            assert data["steps"][0]["status"] == "success"

    def test_traced_model_extracts_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp):

                @traced_model("llm", model="gpt-4-mini")
                def llm(prompt: str) -> dict:
                    return {
                        "content": "answer",
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }

                llm("hello")

            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            step = data["steps"][0]
            assert step["type"] == "model_call"
            assert step["cost"]["input_tokens"] == 10
            assert step["cost"]["output_tokens"] == 5
            assert step["cost"]["total_tokens"] == 15

    def test_traced_tool_records_tool_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp):

                @traced_tool("weather.lookup")
                def weather(city: str) -> dict:
                    return {"summary": "warm"}

                weather("Shanghai")

            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            step = data["steps"][0]
            assert step["type"] == "tool_call"
            assert step["tool"]["name"] == "weather.lookup"
            assert step["tool"]["result"] == {"summary": "warm"}

    def test_traced_step_captures_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp):

                @traced_step("tool_call", "failing")
                def fail() -> None:
                    raise ValueError("boom")

                try:
                    fail()
                except ValueError:
                    pass

            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            step = data["steps"][0]
            assert step["status"] == "error"
            assert step["error"]["type"] == "ValueError"
            assert "boom" in step["error"]["message"]

    def test_traced_model_no_cost_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with TraceContext(task="test", output_dir=tmp):

                @traced_model("llm", model="gpt-4-mini", track_cost=False)
                def llm(prompt: str) -> dict:
                    return {"content": "answer", "usage": {"total_tokens": 100}}

                llm("hello")

            traces = list(Path(tmp).glob("*.trace.json"))
            data = json.loads(traces[0].read_text(encoding="utf-8"))
            step = data["steps"][0]
            assert "cost" not in step or step["cost"] is None
