"""Tests for the Agent DevTools CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

import agent_devtools_cli.main as cli_main
from agent_devtools import Cost, Error, Step, ToolCall, Trace, TraceContext, new_run
from agent_devtools_cli.main import (
    build_parser,
    command_cost,
    command_diff,
    command_experiment,
    command_inspect,
    command_list,
    command_otel_export,
    command_otel_push,
    command_privacy_scan,
    command_redact,
    command_regression_check,
    command_replay,
    command_replay_adapter,
    command_replay_compare,
    command_show,
    command_steps,
    _load_callable,
    _unsafe_code_allowed,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_trace(tmpdir: str, trace: Trace, name: str = "test.trace.json") -> Path:
    path = Path(tmpdir) / name
    path.write_text(json.dumps(trace.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _make_success_trace() -> Trace:
    t = new_run("Test task", labels={"env": "test"})
    t.run.complete(status="success", final_output="done")
    s1 = Step(type="planner", name="plan")
    s1.complete(status="success", output="do X")
    s2 = Step(
        type="model_call",
        name="llm",
        model="gpt-4.1-mini",
        cost=Cost(input_tokens=10, output_tokens=5, total_tokens=15, amount_usd=0.00002),
    )
    s2.complete(status="success", output="answer")
    t.add_step(s1)
    t.add_step(s2)
    return t


def _make_error_trace() -> Trace:
    t = new_run("Failing task")
    t.run.complete(status="error")
    s1 = Step(type="planner", name="plan")
    s1.complete(status="success", output="do X")
    s2 = Step(
        type="tool_call",
        name="search",
        tool=ToolCall(name="search", args={"q": "test"}, result=None),
    )
    s2.complete(status="timeout", error=Error(type="ToolTimeout", message="timed out"))
    t.add_step(s1)
    t.add_step(s2)
    return t


def _make_sensitive_trace() -> Trace:
    t = new_run("Email alice@example.com with sk-live-secret123")
    t.run.complete(status="success", final_output={"password": "hunter2"})
    s1 = Step(
        type="tool_call",
        name="secret.lookup",
        input={"api_key": "sk-live-secret123", "query": "alice@example.com"},
        tool=ToolCall(name="secret.lookup", args={"authorization": "Bearer secret-token"}, result={"email": "alice@example.com"}),
    )
    s1.complete(status="success", output={"email": "alice@example.com"})
    t.add_step(s1)
    return t


def _arg(data: dict) -> argparse.Namespace:
    return argparse.Namespace(**data)


class _CaptureHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    status_code = 200
    response_body = b"{}"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append({
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": body,
        })
        self.send_response(type(self).status_code)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _CaptureServer:
    def __init__(self, status_code: int = 200, response_body: bytes = b"{}") -> None:
        class Handler(_CaptureHandler):
            requests: list[dict[str, Any]] = []

        Handler.status_code = status_code
        Handler.response_body = response_body
        self.handler = Handler
        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "_CaptureServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/v1/traces"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.handler.requests


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_parser_has_all_commands(self) -> None:
        parser = build_parser()
        choices = list(parser._subparsers._group_actions[0].choices.keys())
        for cmd in ["list", "show", "steps", "inspect", "cost", "diff", "replay", "replay-adapter", "replay-compare", "experiment", "regression-check", "redact", "privacy-scan", "otel-export", "otel-push", "store", "init", "doctor", "watch", "mcp", "audit", "mcp-config"]:
            assert cmd in choices

    def test_init_and_doctor_create_a_ready_local_workspace(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assert main(["init", "--root", tmp]) == 0
            assert (Path(tmp) / ".agent-devtools" / "config.json").exists()
            assert main(["doctor", "--root", tmp]) == 0
            assert "ready" in capsys.readouterr().out

    def test_watch_once_imports_new_trace_into_local_store(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(["init", "--root", tmp])
            _write_trace(str(root / "traces"), _make_success_trace(), "incoming.trace.json")

            assert main(["watch", "--root", tmp, "--once"]) == 0
            assert "Imported trace" in capsys.readouterr().out
            assert main(["store", "list", "--db", str(root / ".agent-devtools" / "traces.db")]) == 0

    def test_audit_records_explicit_events_and_mcp_config_is_json(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assert main(["audit", "Codex visible work", "--root", tmp, "--event", "run command", "--error-event", "read docs=403"]) == 0
            out = capsys.readouterr().out
            assert "Audit trace written" in out
            assert len(list((Path(tmp) / "traces").glob("*.trace.json"))) == 1

            assert main(["mcp-config", "--root", tmp]) == 0
            config = json.loads(capsys.readouterr().out)
            assert config["name"] == "agent-devtools"
            assert config["args"][-2:] == ["--root", tmp]

    def test_list_default_directory(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.directory == "traces"

    def test_show_with_detail(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["show", "file.json", "--detail"])
        assert args.trace == "file.json"
        assert args.show_detail is True


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_lists_traces(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_trace(tmp, new_run("test a"), "a.trace.json")
            _write_trace(tmp, new_run("test b"), "b.trace.json")
            args = _arg({"directory": tmp, "func": command_list})
            rc = command_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "a.trace.json" in out
            assert "b.trace.json" in out

    def test_empty_directory(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = _arg({"directory": tmp, "func": command_list})
            rc = command_list(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No trace files" in out


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShowCommand:
    def test_show_summary(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            args = _arg({"trace": str(path), "show_detail": False, "func": command_show})
            rc = command_show(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "Test task" in out
            assert "success" in out
            assert "plan" in out
            assert "llm" in out

    def test_show_detail(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            args = _arg({"trace": str(path), "show_detail": True, "func": command_show})
            rc = command_show(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "out:" in out

    def test_show_error_trace(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_error_trace())
            args = _arg({"trace": str(path), "show_detail": False, "func": command_show})
            rc = command_show(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "error" in out

    def test_show_file_not_found(self) -> None:
        args = _arg({"trace": "nonexistent.trace.json", "show_detail": False, "func": command_show})
        with pytest.raises(SystemExit):
            command_show(args)


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


class TestStepsCommand:
    def test_steps_table(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            args = _arg({"trace": str(path), "func": command_steps})
            rc = command_steps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "plan" in out
            assert "model_call" in out
            assert "2 steps" in out

    def test_steps_empty(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = new_run("empty")
            t.run.complete()
            path = _write_trace(tmp, t)
            args = _arg({"trace": str(path), "func": command_steps})
            rc = command_steps(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "0 steps" in out


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


class TestInspectCommand:
    def test_inspect_existing_step(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = _make_success_trace()
            step_id = t.steps[0].id
            path = _write_trace(tmp, t)
            args = _arg({"trace": str(path), "step_id": step_id, "func": command_inspect})
            rc = command_inspect(args)
            assert rc == 0
            out = capsys.readouterr().out
            data = json.loads(out)
            assert data["id"] == step_id
            assert data["type"] == "planner"

    def test_inspect_nonexistent_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            args = _arg({"trace": str(path), "step_id": "nonexistent", "func": command_inspect})
            with pytest.raises(SystemExit):
                command_inspect(args)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


class TestCostCommand:
    def test_cost_with_data(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            args = _arg({"trace": str(path), "func": command_cost})
            rc = command_cost(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "15" in out  # total tokens
            assert "Most expensive steps" in out

    def test_cost_with_multiple_models(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = new_run("multi-model")
            t.run.complete()
            s1 = Step(type="model_call", name="a", model="gpt-4", cost=Cost(input_tokens=5, total_tokens=5, amount_usd=0.01))
            s1.complete()
            s2 = Step(type="model_call", name="b", model="claude-sonnet", cost=Cost(input_tokens=10, total_tokens=10, amount_usd=0.02))
            s2.complete()
            t.add_step(s1)
            t.add_step(s2)
            path = _write_trace(tmp, t)
            args = _arg({"trace": str(path), "func": command_cost})
            rc = command_cost(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "By model" in out

    def test_cost_empty(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = new_run("no cost")
            t.run.complete()
            t.add_step(Step(type="planner", name="plan"))
            path = _write_trace(tmp, t)
            args = _arg({"trace": str(path), "func": command_cost})
            rc = command_cost(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No cost data" in out


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


class TestDiffCommand:
    def test_diff_aligns_same_shape_steps_with_different_ids(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left = _write_trace(tmp, _make_success_trace(), "left.trace.json")
            right = _write_trace(tmp, _make_success_trace(), "right.trace.json")
            args = _arg({"left_trace": str(left), "right_trace": str(right), "func": command_diff})
            rc = command_diff(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "no structural differences" in out
            assert "removed" not in out
            assert "added" not in out

    def test_diff_status_change(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t1 = _make_success_trace()
            t2 = _make_success_trace()
            t2.steps[1].status = "error"
            left = _write_trace(tmp, t1, "left.trace.json")
            right = _write_trace(tmp, t2, "right.trace.json")
            args = _arg({"left_trace": str(left), "right_trace": str(right), "func": command_diff})
            rc = command_diff(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "Structural diff" in out

    def test_diff_step_count(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left = _write_trace(tmp, _make_success_trace(), "left.trace.json")
            t_right = _make_success_trace()
            t_right.add_step(Step(type="custom", name="extra"))
            right = _write_trace(tmp, t_right, "right.trace.json")
            args = _arg({"left_trace": str(left), "right_trace": str(right), "func": command_diff})
            rc = command_diff(args)
            assert rc == 0
            out = capsys.readouterr().out
            assert "+1" in out
            assert "added" in out


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


class TestReplayCommand:
    def test_replay_writes_trace(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.steps[0].replayable = True
            trace.steps[1].replayable = True
            path = _write_trace(tmp, trace)
            out_dir = Path(tmp) / "replays"

            args = _arg({
                "trace": str(path),
                "start_step": trace.steps[0].id,
                "output_dir": str(out_dir),
                "func": command_replay,
            })
            rc = command_replay(args)

            assert rc == 0
            out = capsys.readouterr().out
            assert "Replay trace written" in out
            written = list(out_dir.glob("*.trace.json"))
            assert len(written) == 1
            replay_data = json.loads(written[0].read_text(encoding="utf-8"))
            assert replay_data["run"]["labels"]["replay"] == "true"
            assert replay_data["run"]["labels"]["source_run_id"] == trace.run.id

    def test_main_replay(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            path = _write_trace(tmp, trace)
            out_dir = Path(tmp) / "replays"

            rc = main(["replay", str(path), "--start-step", trace.steps[0].id, "--output-dir", str(out_dir)])

            assert rc == 0
            assert list(out_dir.glob("*.trace.json"))

    def test_replay_uses_plan_tool_mocks(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.steps[1].tool = ToolCall(name="llm.lookup", args={"q": "recorded"}, result={"answer": "recorded"})
            trace.steps[1].output = {"answer": "recorded"}
            path = _write_trace(tmp, trace)
            out_dir = Path(tmp) / "replays"
            plan_path = Path(tmp) / "replay-plan.json"
            plan_path.write_text(
                json.dumps({
                    "run_id": trace.run.id,
                    "start_step_id": trace.steps[0].id,
                    "mocked_tools": [
                        {
                            "stepId": trace.steps[1].id,
                            "name": "llm.lookup",
                            "args": {"q": "recorded"},
                            "result": {"answer": "edited"},
                        }
                    ],
                }),
                encoding="utf-8",
            )

            rc = main(["replay", str(path), "--plan", str(plan_path), "--output-dir", str(out_dir)])

            assert rc == 0
            out = capsys.readouterr().out
            assert "Mocks:      1" in out
            written = list(out_dir.glob("*.trace.json"))
            replay_data = json.loads(written[0].read_text(encoding="utf-8"))
            assert replay_data["run"]["labels"]["replay_plan"] == "true"
            assert replay_data["steps"][1]["metadata"]["replay_mode"] == "edited_tool_mock"
            assert replay_data["steps"][1]["tool"]["result"] == {"answer": "edited"}
            assert replay_data["steps"][1]["output"] == {"answer": "edited"}

    def test_replay_adapter_runs_callable_and_writes_trace(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.steps[0].input = {"question": "weather"}
            path = _write_trace(tmp, trace)
            out_dir = Path(tmp) / "runtime-replays"
            module_path = Path(tmp) / "demo_agent.py"
            module_path.write_text(
                "def run(payload):\n"
                "    return {'answer': payload['question'].upper()}\n",
                encoding="utf-8",
            )

            args = _arg({
                "trace": str(path),
                "start_step": trace.steps[0].id,
                "callable": f"{module_path}:run",
                "name": "demo-agent",
                "input_json": None,
                "pythonpath": None,
                "output_dir": str(out_dir),
                "allow_unsafe_code": True,
                "func": command_replay_adapter,
            })
            rc = command_replay_adapter(args)

            assert rc == 0
            out = capsys.readouterr().out
            assert "Adapter replay trace written" in out
            assert f"Callable file: {module_path.resolve()}" in out
            assert f"Callable sha256: {hashlib.sha256(module_path.read_bytes()).hexdigest()}" in out
            written = list(out_dir.glob("*.trace.json"))
            assert len(written) == 1
            replay_data = json.loads(written[0].read_text(encoding="utf-8"))
            assert replay_data["run"]["labels"]["replay"] == "true"
            assert replay_data["run"]["labels"]["replay_mode"] == "adapter_execution"
            assert replay_data["run"]["labels"]["source_run_id"] == trace.run.id
            assert replay_data["run"]["labels"]["adapter"] == "demo-agent"
            assert replay_data["steps"][0]["input"] == {"question": "weather"}
            assert replay_data["steps"][0]["output"] == {"answer": "WEATHER"}

    def test_replay_adapter_accepts_input_json_override(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.steps[0].input = {"question": "recorded"}
            path = _write_trace(tmp, trace)
            out_dir = Path(tmp) / "runtime-replays"
            module_path = Path(tmp) / "demo_agent.py"
            module_path.write_text(
                "def run(payload):\n"
                "    return {'answer': payload['question']}\n",
                encoding="utf-8",
            )

            rc = main([
                "replay-adapter",
                str(path),
                "--start-step",
                trace.steps[0].id,
                "--callable",
                f"{module_path}:run",
                "--allow-unsafe-code",
                "--input-json",
                '{"question":"override"}',
                "--output-dir",
                str(out_dir),
            ])

            assert rc == 0
            written = list(out_dir.glob("*.trace.json"))
            replay_data = json.loads(written[0].read_text(encoding="utf-8"))
            assert replay_data["steps"][0]["input"] == {"question": "override"}
            assert replay_data["steps"][0]["output"] == {"answer": "override"}

    def test_replay_adapter_ignores_callable_stdout_pollution(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.steps[0].input = {"question": "weather"}
            source = _write_trace(tmp, trace)
            out_dir = Path(tmp) / "runtime-replays"
            module_path = Path(tmp) / "noisy_agent.py"
            module_path.write_text(
                "print('module import noise')\n"
                "def run(payload):\n"
                "    print('runtime noise')\n"
                "    return {'answer': payload['question']}\n",
                encoding="utf-8",
            )

            rc = main([
                "replay-adapter",
                str(source),
                "--start-step",
                trace.steps[0].id,
                "--callable",
                f"{module_path}:run",
                "--allow-unsafe-code",
                "--output-dir",
                str(out_dir),
            ])

            assert rc == 0
            capsys.readouterr()
            written = list(out_dir.glob("*.trace.json"))
            replay_data = json.loads(written[0].read_text(encoding="utf-8"))
            assert replay_data["steps"][0]["output"] == {"answer": "weather"}

    def test_replay_adapter_executes_callable_in_child_process(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.steps[0].input = {"question": "weather"}
            source = _write_trace(tmp, trace)
            out_dir = Path(tmp) / "runtime-replays"
            marker_path = Path(tmp) / "import-pid.txt"
            module_path = Path(tmp) / "demo_agent.py"
            module_path.write_text(
                "import os\n"
                f"open({str(marker_path)!r}, 'w', encoding='utf-8').write(str(os.getpid()))\n"
                "def run(payload):\n"
                "    return {'pid': os.getpid(), 'question': payload['question']}\n",
                encoding="utf-8",
            )

            rc = main([
                "replay-adapter",
                str(source),
                "--start-step",
                trace.steps[0].id,
                "--callable",
                f"{module_path}:run",
                "--allow-unsafe-code",
                "--output-dir",
                str(out_dir),
            ])

            assert rc == 0
            capsys.readouterr()
            import_pid = int(marker_path.read_text(encoding="utf-8"))
            assert import_pid != os.getpid()
            written = list(out_dir.glob("*.trace.json"))
            replay_data = json.loads(written[0].read_text(encoding="utf-8"))
            assert replay_data["steps"][0]["output"]["pid"] == import_pid

    def test_replay_adapter_rejects_missing_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            path = _write_trace(tmp, trace)

            args = _arg({
                "trace": str(path),
                "start_step": trace.steps[0].id,
                "callable": "missing",
                "name": None,
                "input_json": None,
                "pythonpath": None,
                "output_dir": tmp,
                "allow_unsafe_code": True,
                "func": command_replay_adapter,
            })

            with pytest.raises(SystemExit):
                command_replay_adapter(args)

    def test_replay_adapter_requires_explicit_unsafe_code_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            path = _write_trace(tmp, trace)
            module_path = Path(tmp) / "demo_agent.py"
            module_path.write_text("def run(payload):\n    return payload\n", encoding="utf-8")

            args = _arg({
                "trace": str(path),
                "start_step": trace.steps[0].id,
                "callable": f"{module_path}:run",
                "name": None,
                "input_json": None,
                "pythonpath": None,
                "output_dir": tmp,
                "allow_unsafe_code": False,
                "func": command_replay_adapter,
            })

            with pytest.raises(SystemExit) as exc_info:
                command_replay_adapter(args)

            assert "unsafe code" in str(exc_info.value)

    def test_replay_adapter_ignores_unsafe_environment_variable(self, monkeypatch) -> None:
        monkeypatch.setenv("AGENT_DEVTOOLS_ALLOW_UNSAFE_CODE", "true")

        assert _unsafe_code_allowed(_arg({"allow_unsafe_code": False})) is False

    def test_load_callable_restores_temporary_pythonpath(self) -> None:
        original_path = list(sys.path)
        with tempfile.TemporaryDirectory() as tmp:
            module_path = Path(tmp) / "demo_agent.py"
            module_path.write_text("def run(payload):\n    return payload\n", encoding="utf-8")

            fn = _load_callable("demo_agent:run", [tmp])

            assert fn({"ok": True}) == {"ok": True}
            assert sys.path == original_path

    def test_load_callable_appends_pythonpath_after_standard_paths(self) -> None:
        original_path = list(sys.path)
        with tempfile.TemporaryDirectory() as tmp:
            module_path = Path(tmp) / "shadow_check_agent.py"
            module_path.write_text(
                "import json\n"
                "def run(payload):\n"
                "    return {'json_module': json.__name__}\n",
                encoding="utf-8",
            )
            (Path(tmp) / "json.py").write_text("raise RuntimeError('shadowed json imported')\n", encoding="utf-8")

            fn = _load_callable("shadow_check_agent:run", [tmp])

            assert fn({}) == {"json_module": "json"}
            assert sys.path == original_path

    def test_replay_compare_reports_original_vs_replay(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_trace = _make_success_trace()
            source_trace.steps[0].duration_ms = 10
            source_trace.steps[1].duration_ms = 20
            source = _write_trace(tmp, source_trace, "source.trace.json")

            replay_dir = Path(tmp) / "replays"
            replay_rc = command_replay(_arg({
                "trace": str(source),
                "start_step": source_trace.steps[0].id,
                "output_dir": str(replay_dir),
                "func": command_replay,
            }))
            assert replay_rc == 0
            replay = next(replay_dir.glob("*.trace.json"))
            capsys.readouterr()

            args = _arg({"source_trace": str(source), "replay_trace": str(replay), "func": command_replay_compare})
            rc = command_replay_compare(args)

            assert rc == 0
            out = capsys.readouterr().out
            assert "Replay comparison: original vs replay" in out
            assert "Source match: yes" in out
            assert "Output changed: no" in out
            assert "Step delta: +0" in out

    def test_main_replay_compare(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_trace = _make_success_trace()
            source = _write_trace(tmp, source_trace, "source.trace.json")

            replay_dir = Path(tmp) / "replays"
            command_replay(_arg({
                "trace": str(source),
                "start_step": source_trace.steps[0].id,
                "output_dir": str(replay_dir),
                "func": command_replay,
            }))
            replay = next(replay_dir.glob("*.trace.json"))
            capsys.readouterr()

            rc = main(["replay-compare", str(source), str(replay)])

            assert rc == 0
            assert "Replay comparison" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# experiment
# ---------------------------------------------------------------------------


class TestExperimentCommand:
    def test_experiment_compares_two_traces(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left_trace = _make_success_trace()
            right_trace = _make_success_trace()
            right_trace.steps[1].cost = Cost(input_tokens=20, output_tokens=5, total_tokens=25, amount_usd=0.00004)
            right_trace.run.duration_ms = 200
            left = _write_trace(tmp, left_trace, "left.trace.json")
            right = _write_trace(tmp, right_trace, "right.trace.json")

            args = _arg({"left_trace": str(left), "right_trace": str(right), "func": command_experiment})
            rc = command_experiment(args)

            assert rc == 0
            out = capsys.readouterr().out
            assert "Experiment: A vs B" in out
            assert "Winner by cost: A" in out
            assert "Winner by latency: A" in out
            assert "Recommendation: A" in out

    def test_main_experiment(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left = _write_trace(tmp, _make_success_trace(), "left.trace.json")
            right = _write_trace(tmp, _make_error_trace(), "right.trace.json")

            rc = main(["experiment", str(left), str(right)])

            assert rc == 0
            out = capsys.readouterr().out
            assert "Winner by success: A" in out


# ---------------------------------------------------------------------------
# regression-check
# ---------------------------------------------------------------------------


class TestRegressionCheckCommand:
    def test_regression_check_passes_when_candidate_is_within_thresholds(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline_trace = _make_success_trace()
            baseline_trace.run.duration_ms = 100
            candidate_trace = _make_success_trace()
            candidate_trace.run.duration_ms = 120
            candidate_trace.steps[1].cost = Cost(input_tokens=20, output_tokens=5, total_tokens=25, amount_usd=0.00003)
            baseline = _write_trace(tmp, baseline_trace, "baseline.trace.json")
            candidate = _write_trace(tmp, candidate_trace, "candidate.trace.json")

            args = _arg({
                "baseline_trace": str(baseline),
                "candidate_trace": str(candidate),
                "max_token_delta": 20,
                "max_cost_delta_usd": 0.00002,
                "max_latency_delta_ms": 50,
                "max_step_count_delta": 0,
                "fail_on_output_change": False,
                "json_output": False,
                "func": command_regression_check,
            })
            rc = command_regression_check(args)

            assert rc == 0
            out = capsys.readouterr().out
            assert "Regression check: PASS" in out
            assert "baseline.trace.json" not in out

    def test_regression_check_returns_1_when_threshold_is_exceeded(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline_trace = _make_success_trace()
            candidate_trace = _make_success_trace()
            candidate_trace.steps[1].cost = Cost(input_tokens=120, output_tokens=5, total_tokens=125, amount_usd=0.00020)
            baseline = _write_trace(tmp, baseline_trace, "baseline.trace.json")
            candidate = _write_trace(tmp, candidate_trace, "candidate.trace.json")

            rc = main([
                "regression-check",
                str(baseline),
                str(candidate),
                "--max-token-delta",
                "10",
            ])

            assert rc == 1
            out = capsys.readouterr().out
            assert "Regression check: FAIL" in out
            assert "token_delta" in out

    def test_regression_check_json_output(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = _write_trace(tmp, _make_success_trace(), "baseline.trace.json")
            candidate = _write_trace(tmp, _make_error_trace(), "candidate.trace.json")

            rc = main(["regression-check", str(baseline), str(candidate), "--json"])

            assert rc == 1
            data = json.loads(capsys.readouterr().out)
            assert data["passed"] is False
            assert data["baseline_run_id"]
            assert data["candidate_run_id"]
            assert any(check["name"] == "success_status" for check in data["checks"])


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


class TestRedactCommand:
    def test_redact_writes_sanitized_copy(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.run.final_output = {"email": "alice@example.com"}
            trace.steps[0].input = {"api_key": "sk-live-secret123", "query": "alice@example.com"}
            source = _write_trace(tmp, trace, "source.trace.json")
            output = Path(tmp) / "safe.trace.json"

            args = _arg({"trace": str(source), "output": str(output), "func": command_redact})
            rc = command_redact(args)

            assert rc == 0
            out = capsys.readouterr().out
            assert "Redacted trace written" in out
            data = json.loads(output.read_text(encoding="utf-8"))
            assert data["run"]["final_output"]["email"] == "[REDACTED]"
            assert data["steps"][0]["input"]["api_key"] == "[REDACTED]"
            assert data["steps"][0]["input"]["query"] == "[REDACTED]"

    def test_main_redact(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            trace.steps[0].input = {"password": "secret"}
            source = _write_trace(tmp, trace, "source.trace.json")
            output = Path(tmp) / "safe.trace.json"

            rc = main(["redact", str(source), "--output", str(output)])

            assert rc == 0
            assert output.exists()


# ---------------------------------------------------------------------------
# privacy-scan
# ---------------------------------------------------------------------------


class TestPrivacyScanCommand:
    def test_privacy_scan_reports_findings_without_values(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_trace(tmp, _make_sensitive_trace(), "source.trace.json")

            args = _arg({"trace": str(source), "json_output": False, "func": command_privacy_scan})
            rc = command_privacy_scan(args)

            assert rc == 1
            out = capsys.readouterr().out
            assert "Sensitive trace findings" in out
            assert "steps[0].input.api_key" in out
            assert "sk-live-secret123" not in out
            assert "alice@example.com" not in out

    def test_main_privacy_scan_json(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_trace(tmp, _make_sensitive_trace(), "source.trace.json")

            rc = main(["privacy-scan", str(source), "--json"])

            assert rc == 1
            data = json.loads(capsys.readouterr().out)
            assert data["finding_count"] > 0
            assert "value" not in data["findings"][0]


# ---------------------------------------------------------------------------
# otel-export
# ---------------------------------------------------------------------------


class TestOtelExportCommand:
    def test_otel_export_writes_otlp_json(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = _make_success_trace()
            source = _write_trace(tmp, trace, "source.trace.json")
            output = Path(tmp) / "source.otlp.json"

            args = _arg({"trace": str(source), "output": str(output), "include_payloads": False, "service_name": "agent-devtools", "func": command_otel_export})
            rc = command_otel_export(args)

            assert rc == 0
            out = capsys.readouterr().out
            assert "OpenTelemetry JSON written" in out
            data = json.loads(output.read_text(encoding="utf-8"))
            spans = data["resourceSpans"][0]["scopeSpans"][0]["spans"]
            assert [span["name"] for span in spans] == ["agent.run", "plan", "llm"]

    def test_otel_export_blocks_sensitive_trace_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_trace(tmp, _make_sensitive_trace(), "source.trace.json")
            output = Path(tmp) / "source.otlp.json"

            with pytest.raises(SystemExit):
                main(["otel-export", str(source), "--output", str(output)])

            assert not output.exists()

    def test_otel_export_can_redact_sensitive_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_trace(tmp, _make_sensitive_trace(), "source.trace.json")
            output = Path(tmp) / "source.otlp.json"

            rc = main(["otel-export", str(source), "--redact", "--include-payloads", "--output", str(output)])

            assert rc == 0
            text = output.read_text(encoding="utf-8")
            assert "[REDACTED]" in text
            assert "sk-live-secret123" not in text
            assert "alice@example.com" not in text

    def test_main_otel_export_prints_json(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_trace(tmp, _make_success_trace(), "source.trace.json")

            rc = main(["otel-export", str(source)])

            assert rc == 0
            data = json.loads(capsys.readouterr().out)
            assert "resourceSpans" in data
            resource_attrs = data["resourceSpans"][0]["resource"]["attributes"]
            assert any(attr["key"] == "agent.devtools.run.task" for attr in resource_attrs)


# ---------------------------------------------------------------------------
# otel-push
# ---------------------------------------------------------------------------


class TestOtelPushCommand:
    def test_otel_push_posts_to_collector(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp, _CaptureServer() as server:
            source = _write_trace(tmp, _make_success_trace(), "source.trace.json")

            rc = main([
                "otel-push",
                str(source),
                "--endpoint",
                server.url,
                "--header",
                "x-test-token=local",
            ])

            assert rc == 0
            out = capsys.readouterr().out
            assert "OpenTelemetry trace pushed" in out
            assert len(server.requests) == 1
            request = server.requests[0]
            assert request["path"] == "/v1/traces"
            assert request["headers"]["Content-Type"] == "application/json"
            assert request["headers"]["X-Test-Token"] == "local"
            body = json.loads(request["body"].decode("utf-8"))
            assert "resourceSpans" in body

    def test_otel_push_blocks_sensitive_trace_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _CaptureServer() as server:
            source = _write_trace(tmp, _make_sensitive_trace(), "sensitive.trace.json")

            with pytest.raises(SystemExit):
                main(["otel-push", str(source), "--endpoint", server.url])

            assert server.requests == []

    def test_otel_push_can_redact_sensitive_trace(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp, _CaptureServer() as server:
            source = _write_trace(tmp, _make_sensitive_trace(), "sensitive.trace.json")

            rc = main([
                "otel-push",
                str(source),
                "--endpoint",
                server.url,
                "--redact",
                "--include-payloads",
            ])

            assert rc == 0
            capsys.readouterr()
            body_text = server.requests[0]["body"].decode("utf-8")
            assert "[REDACTED]" in body_text
            assert "sk-live-secret123" not in body_text
            assert "alice@example.com" not in body_text


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class TestStoreCommand:
    def test_store_import_accepts_database_url_without_printing_password(self, monkeypatch, capsys) -> None:
        captured: dict[str, Any] = {}

        class FakeStore:
            location = "postgresql://agent:***@db.example/prod"

            def import_files(self, paths):
                captured["paths"] = paths
                return ["run_1"]

        def fake_create_trace_store(*, db_path, database_url, redaction=False):
            captured["db_path"] = db_path
            captured["database_url"] = database_url
            captured["redaction"] = redaction
            return FakeStore()

        monkeypatch.setattr(cli_main, "create_trace_store", fake_create_trace_store)
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_trace(tmp, _make_success_trace(), "success.trace.json")

            rc = main([
                "store",
                "import",
                str(source),
                "--database-url",
                "postgresql://agent:secret@db.example/prod",
                "--redact",
            ])

        assert rc == 0
        assert captured["database_url"] == "postgresql://agent:secret@db.example/prod"
        assert captured["redaction"] is True
        assert len(captured["paths"]) == 1
        out = capsys.readouterr().out
        assert "postgresql://agent:***@db.example/prod" in out
        assert "secret" not in out

    def test_store_import_list_search_and_show(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.db"
            source = Path(tmp) / "traces"
            source.mkdir()
            trace = _make_success_trace()
            _write_trace(str(source), trace, "success.trace.json")

            rc = main(["store", "import", str(source), "--db", str(db)])
            assert rc == 0
            out = capsys.readouterr().out
            assert "Imported 1 trace" in out

            rc = main(["store", "list", "--db", str(db)])
            assert rc == 0
            out = capsys.readouterr().out
            assert trace.run.id in out
            assert "Test task" in out

            rc = main(["store", "search", "Test", "--db", str(db)])
            assert rc == 0
            out = capsys.readouterr().out
            assert trace.run.id in out

            rc = main(["store", "show", trace.run.id, "--db", str(db)])
            assert rc == 0
            out = capsys.readouterr().out
            assert '"run"' in out
            assert trace.run.id in out

    def test_store_import_blocks_sensitive_trace_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.db"
            source = _write_trace(tmp, _make_sensitive_trace(), "sensitive.trace.json")

            with pytest.raises(SystemExit):
                main(["store", "import", str(source), "--db", str(db)])

    def test_store_import_can_redact_sensitive_trace(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.db"
            trace = _make_sensitive_trace()
            source = _write_trace(tmp, trace, "sensitive.trace.json")

            rc = main(["store", "import", str(source), "--db", str(db), "--redact"])

            assert rc == 0
            capsys.readouterr()
            rc = main(["store", "show", trace.run.id, "--db", str(db)])
            assert rc == 0
            data = json.loads(capsys.readouterr().out)
            assert data["run"]["task"] == "Email [REDACTED] with [REDACTED]"
            assert data["steps"][0]["input"]["api_key"] == "[REDACTED]"

    def test_store_import_directory_is_not_recursive_by_default(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.db"
            source = Path(tmp) / "traces"
            nested = source / "nested"
            nested.mkdir(parents=True)
            top_trace = _make_success_trace()
            nested_trace = new_run("Nested task")
            _write_trace(str(source), top_trace, "top.trace.json")
            _write_trace(str(nested), nested_trace, "nested.trace.json")

            rc = main(["store", "import", str(source), "--db", str(db)])

            assert rc == 0
            capsys.readouterr()
            rc = main(["store", "list", "--db", str(db)])
            assert rc == 0
            out = capsys.readouterr().out
            assert top_trace.run.id in out
            assert nested_trace.run.id not in out

    def test_store_import_directory_can_be_recursive(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.db"
            source = Path(tmp) / "traces"
            nested = source / "nested"
            nested.mkdir(parents=True)
            top_trace = _make_success_trace()
            nested_trace = new_run("Nested task")
            _write_trace(str(source), top_trace, "top.trace.json")
            _write_trace(str(nested), nested_trace, "nested.trace.json")

            rc = main(["store", "import", str(source), "--db", str(db), "--recursive"])

            assert rc == 0
            capsys.readouterr()
            rc = main(["store", "list", "--db", str(db)])
            assert rc == 0
            out = capsys.readouterr().out
            assert top_trace.run.id in out
            assert nested_trace.run.id in out

    def test_store_import_rejects_too_many_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.db"
            source = Path(tmp) / "traces"
            source.mkdir()
            _write_trace(str(source), _make_success_trace(), "a.trace.json")
            _write_trace(str(source), _make_success_trace(), "b.trace.json")

            with pytest.raises(SystemExit, match="too many trace files"):
                main(["store", "import", str(source), "--db", str(db), "--max-files", "1"])


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_list(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_trace(tmp, new_run("test"), "a.trace.json")
            rc = main(["list", tmp])
            assert rc == 0

    def test_main_show(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            rc = main(["show", str(path)])
            assert rc == 0
            out = capsys.readouterr().out
            assert "Test task" in out

    def test_main_steps(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            rc = main(["steps", str(path)])
            assert rc == 0

    def test_main_cost(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_trace(tmp, _make_success_trace())
            rc = main(["cost", str(path)])
            assert rc == 0

    def test_main_diff(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left = _write_trace(tmp, _make_success_trace(), "left.trace.json")
            right = _write_trace(tmp, _make_success_trace(), "right.trace.json")
            rc = main(["diff", str(left), str(right)])
            assert rc == 0

    def test_main_experiment(self, capsys) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            left = _write_trace(tmp, _make_success_trace(), "left.trace.json")
            right = _write_trace(tmp, _make_error_trace(), "right.trace.json")
            rc = main(["experiment", str(left), str(right)])
            assert rc == 0

    def test_main_missing_command(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_main_handles_keyboard_interrupt(self, monkeypatch, capsys) -> None:
        def interrupt(args):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_main, "command_list", interrupt)

        rc = cli_main.main(["list"])

        assert rc == 130
        assert "Interrupted." in capsys.readouterr().err
