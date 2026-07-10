"""Tests for exporting Agent DevTools traces as OTLP JSON."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from agent_devtools import (
    Cost,
    Error,
    OtlpHttpExportError,
    Run,
    Step,
    ToolCall,
    Trace,
    push_trace_to_otlp_http,
    trace_to_otlp_json,
)


def _attrs(items: list[dict]) -> dict[str, dict]:
    return {item["key"]: item["value"] for item in items}


def _string(attrs: dict[str, dict], key: str) -> str:
    return attrs[key]["stringValue"]


def _int(attrs: dict[str, dict], key: str) -> str:
    return attrs[key]["intValue"]


def _double(attrs: dict[str, dict], key: str) -> float:
    return attrs[key]["doubleValue"]


def _make_trace() -> Trace:
    run = Run(
        id="run-otel-1",
        task="Explain weather",
        status="success",
        started_at="2026-01-02T03:04:05Z",
        ended_at="2026-01-02T03:04:06Z",
        duration_ms=1000,
        labels={"env": "test"},
        cost=Cost(input_tokens=12, output_tokens=8, total_tokens=20, amount_usd=0.0003),
    )
    root = Step(
        id="step-plan",
        type="planner",
        name="Plan answer",
        status="success",
        started_at="2026-01-02T03:04:05.100Z",
        ended_at="2026-01-02T03:04:05.200Z",
        duration_ms=100,
    )
    model = Step(
        id="step-model",
        type="model_call",
        name="Call model",
        status="success",
        started_at="2026-01-02T03:04:05.250Z",
        ended_at="2026-01-02T03:04:05.900Z",
        duration_ms=650,
        parent_id="step-plan",
        model="gpt-4.1-mini",
        cost=Cost(input_tokens=10, output_tokens=5, total_tokens=15, amount_usd=0.0002),
    )
    tool = Step(
        id="step-tool",
        type="tool_call",
        name="Fetch weather",
        status="timeout",
        started_at="2026-01-02T03:04:05.300Z",
        ended_at="2026-01-02T03:04:05.500Z",
        duration_ms=200,
        parent_id="step-plan",
        tool=ToolCall(name="weather.lookup", args={"city": "Beijing"}, result=None),
        error=Error(type="TimeoutError", message="tool timed out"),
    )
    return Trace(run=run, steps=[root, model, tool])


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


def test_trace_to_otlp_json_exports_run_and_step_spans() -> None:
    data = trace_to_otlp_json(_make_trace())

    assert list(data.keys()) == ["resourceSpans"]
    resource_span = data["resourceSpans"][0]
    resource_attrs = _attrs(resource_span["resource"]["attributes"])
    assert _string(resource_attrs, "service.name") == "agent-devtools"
    assert _string(resource_attrs, "agent.devtools.run.id") == "run-otel-1"
    assert _string(resource_attrs, "agent.devtools.run.task") == "Explain weather"
    assert _string(resource_attrs, "agent.devtools.run.status") == "success"
    assert _string(resource_attrs, "agent.devtools.run.label.env") == "test"

    scope_span = resource_span["scopeSpans"][0]
    assert scope_span["scope"]["name"] == "agent-devtools"
    spans = scope_span["spans"]
    assert [span["name"] for span in spans] == ["agent.run", "Plan answer", "Call model", "Fetch weather"]

    trace_ids = {span["traceId"] for span in spans}
    assert len(trace_ids) == 1
    assert len(next(iter(trace_ids))) == 32
    assert all(len(span["spanId"]) == 16 for span in spans)

    run_span, plan_span, model_span, tool_span = spans
    assert "parentSpanId" not in run_span
    assert plan_span["parentSpanId"] == run_span["spanId"]
    assert model_span["parentSpanId"] == plan_span["spanId"]
    assert tool_span["parentSpanId"] == plan_span["spanId"]

    assert run_span["startTimeUnixNano"] == "1767323045000000000"
    assert run_span["endTimeUnixNano"] == "1767323046000000000"
    assert model_span["kind"] == 3
    assert tool_span["kind"] == 3
    assert plan_span["kind"] == 1
    assert model_span["status"]["code"] == 1
    assert tool_span["status"]["code"] == 2

    model_attrs = _attrs(model_span["attributes"])
    assert _string(model_attrs, "agent.step.id") == "step-model"
    assert _string(model_attrs, "agent.step.type") == "model_call"
    assert _string(model_attrs, "llm.model") == "gpt-4.1-mini"
    assert _int(model_attrs, "llm.usage.total_tokens") == "15"
    assert _double(model_attrs, "llm.usage.cost_usd") == 0.0002

    tool_attrs = _attrs(tool_span["attributes"])
    assert _string(tool_attrs, "tool.name") == "weather.lookup"
    assert _string(tool_attrs, "error.type") == "TimeoutError"
    assert _string(tool_attrs, "error.message") == "tool timed out"
    assert "tool.args_json" not in tool_attrs


def test_trace_to_otlp_json_can_include_payload_attributes() -> None:
    trace = _make_trace()
    trace.steps[1].input = {"question": "weather"}
    trace.steps[1].output = {"summary": "cold"}

    data = trace_to_otlp_json(trace, include_payloads=True)
    model_span = data["resourceSpans"][0]["scopeSpans"][0]["spans"][2]
    attrs = _attrs(model_span["attributes"])

    assert _string(attrs, "agent.step.input_json") == '{"question":"weather"}'
    assert _string(attrs, "agent.step.output_json") == '{"summary":"cold"}'


def test_push_trace_to_otlp_http_posts_json_payload() -> None:
    with _CaptureServer() as server:
        result = push_trace_to_otlp_http(
            _make_trace(),
            endpoint=server.url,
            headers={"x-test-token": "local"},
            timeout_seconds=2,
            service_name="agent-devtools-test",
        )

    assert result.ok is True
    assert result.status_code == 200
    assert result.endpoint == server.url
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == "/v1/traces"
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["headers"]["X-Test-Token"] == "local"
    body = json.loads(request["body"].decode("utf-8"))
    resource_attrs = _attrs(body["resourceSpans"][0]["resource"]["attributes"])
    assert _string(resource_attrs, "service.name") == "agent-devtools-test"


def test_push_trace_to_otlp_http_uses_env_endpoint_and_headers(monkeypatch) -> None:
    with _CaptureServer() as server:
        base_endpoint = server.url.removesuffix("/v1/traces")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", base_endpoint)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-shared=one")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "x-trace=two")

        result = push_trace_to_otlp_http(_make_trace(), timeout_seconds=2)

    assert result.ok is True
    assert result.endpoint == server.url
    request_headers = server.requests[0]["headers"]
    assert request_headers["X-Shared"] == "one"
    assert request_headers["X-Trace"] == "two"


def test_push_trace_to_otlp_http_raises_for_non_2xx() -> None:
    with _CaptureServer(status_code=503, response_body=b"collector down") as server:
        with pytest.raises(OtlpHttpExportError) as exc_info:
            push_trace_to_otlp_http(_make_trace(), endpoint=server.url, timeout_seconds=2)

    assert exc_info.value.status_code == 503
    assert "collector down" in exc_info.value.response_body
