"""OpenTelemetry Protocol JSON export for Agent DevTools traces."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .trace import Cost, Step, Trace

DEFAULT_SERVICE_NAME = "agent-devtools"
DEFAULT_OTLP_HTTP_ENDPOINT = "http://localhost:4318/v1/traces"
SCOPE_NAME = "agent-devtools"
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
DANGEROUS_HTTP_HEADERS = {
    "connection",
    "content-length",
    "content-type",
    "expect",
    "host",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

SPAN_KIND_INTERNAL = 1
SPAN_KIND_CLIENT = 3

STATUS_CODE_OK = 1
STATUS_CODE_ERROR = 2
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class OtlpHttpExportResult:
    """Result returned after pushing a trace to an OTLP HTTP endpoint."""

    endpoint: str
    status_code: int
    response_body: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class OtlpHttpExportError(RuntimeError):
    """Raised when an OTLP HTTP export fails."""

    def __init__(self, endpoint: str, status_code: int | None = None, response_body: str = "", message: str = "") -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        self.response_body = response_body
        detail = message or f"OTLP HTTP export failed for {endpoint}"
        if status_code is not None:
            detail = f"{detail} (status {status_code})"
        if response_body:
            detail = f"{detail}: {response_body}"
        super().__init__(detail)


def trace_to_otlp_json(
    trace: Trace,
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
    include_payloads: bool = False,
) -> dict[str, Any]:
    """Convert an Agent DevTools trace into OTLP JSON-compatible data.

    The output follows the OTLP trace shape of
    resourceSpans -> scopeSpans -> spans and intentionally avoids exporting
    prompt/tool payloads unless include_payloads is enabled.
    """
    trace_id = _stable_hex("trace", trace.run.id, length=32)
    run_span_id = _stable_hex("run", trace.run.id, length=16)
    step_span_ids = {step.id: _stable_hex("step", step.id, length=16) for step in trace.steps}

    spans = [_run_to_span(trace, trace_id, run_span_id)]
    spans.extend(
        _step_to_span(
            step,
            trace_id=trace_id,
            span_id=step_span_ids[step.id],
            run_span_id=run_span_id,
            step_span_ids=step_span_ids,
            include_payloads=include_payloads,
        )
        for step in trace.steps
    )

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _resource_attributes(trace, service_name),
                },
                "scopeSpans": [
                    {
                        "scope": {"name": SCOPE_NAME},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def write_otlp_json(
    trace: Trace,
    path: str | Path,
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
    include_payloads: bool = False,
) -> Path:
    """Write OTLP JSON data to a file and return the resolved Path."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            trace_to_otlp_json(trace, service_name=service_name, include_payloads=include_payloads),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return output_path


def push_trace_to_otlp_http(
    trace: Trace,
    *,
    endpoint: str | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
    include_payloads: bool = False,
    allow_private_endpoint: bool = False,
    allow_insecure_endpoint: bool = False,
) -> OtlpHttpExportResult:
    """Push a trace to an OTLP HTTP JSON endpoint.

    Uses only the Python standard library and defaults to the Collector's
    HTTP traces endpoint when no endpoint is provided.
    """
    target = _resolve_endpoint(endpoint)
    pinned_ips = _validate_otlp_endpoint(
        target,
        allow_private_endpoint=allow_private_endpoint,
        allow_insecure_endpoint=allow_insecure_endpoint,
    )
    payload = json.dumps(
        trace_to_otlp_json(trace, service_name=service_name, include_payloads=include_payloads),
        ensure_ascii=False,
    ).encode("utf-8")
    merged_headers = {
        "Content-Type": "application/json",
        **_safe_http_headers({
            **_env_headers(),
            **(headers or {}),
        }),
    }
    request = Request(target, data=payload, headers=merged_headers, method="POST")
    timeout = timeout_seconds if timeout_seconds is not None else _env_timeout_seconds()
    parsed = urlparse(target)
    open_kwargs: dict[str, Any] = {"timeout": timeout}
    if parsed.scheme == "https":
        open_kwargs["context"] = ssl.create_default_context()

    try:
        with _pinned_dns_resolution(parsed.hostname or "", pinned_ips):
            response = urlopen(request, **open_kwargs)
        with response:
            body = response.read().decode("utf-8", errors="replace")
            status_code = int(response.status)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OtlpHttpExportError(target, status_code=exc.code, response_body=body) from exc
    except URLError as exc:
        raise OtlpHttpExportError(target, message=str(exc.reason)) from exc
    except TimeoutError as exc:
        raise OtlpHttpExportError(target, message="request timed out") from exc

    if not 200 <= status_code < 300:
        raise OtlpHttpExportError(target, status_code=status_code, response_body=body)
    return OtlpHttpExportResult(endpoint=target, status_code=status_code, response_body=body)


def _resource_attributes(trace: Trace, service_name: str) -> list[dict[str, Any]]:
    attrs = [
        _attr("service.name", service_name),
        _attr("agent.devtools.schema_version", trace.schema_version),
        _attr("agent.devtools.run.id", trace.run.id),
        _attr("agent.devtools.run.task", trace.run.task),
        _attr("agent.devtools.run.status", trace.run.status),
    ]
    for key, value in sorted(trace.run.labels.items()):
        attrs.append(_attr(f"agent.devtools.run.label.{key}", value))
    return attrs


def _run_to_span(trace: Trace, trace_id: str, span_id: str) -> dict[str, Any]:
    run = trace.run
    start_nanos, end_nanos = _time_range(run.started_at, run.ended_at, run.duration_ms)
    attrs = [
        _attr("agent.run.id", run.id),
        _attr("agent.run.task", run.task),
        _attr("agent.run.status", run.status),
        _attr("agent.run.step_count", len(trace.steps)),
    ]
    if run.duration_ms is not None:
        attrs.append(_attr("agent.run.duration_ms", run.duration_ms))
    attrs.extend(_cost_attributes("agent.run", run.cost))

    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "agent.run",
        "kind": SPAN_KIND_INTERNAL,
        "startTimeUnixNano": str(start_nanos),
        "endTimeUnixNano": str(end_nanos),
        "attributes": attrs,
        "status": _status(run.status),
    }
    return span


def _step_to_span(
    step: Step,
    *,
    trace_id: str,
    span_id: str,
    run_span_id: str,
    step_span_ids: dict[str, str],
    include_payloads: bool,
) -> dict[str, Any]:
    start_nanos, end_nanos = _time_range(step.started_at, step.ended_at, step.duration_ms)
    attrs = [
        _attr("agent.step.id", step.id),
        _attr("agent.step.type", step.type),
        _attr("agent.step.status", step.status),
        _attr("agent.step.replayable", step.replayable),
    ]
    if step.duration_ms is not None:
        attrs.append(_attr("agent.step.duration_ms", step.duration_ms))
    if step.model:
        attrs.append(_attr("llm.model", step.model))
    if step.tool:
        attrs.append(_attr("tool.name", step.tool.name))
    if step.error:
        attrs.append(_attr("error.type", step.error.type))
        attrs.append(_attr("error.message", step.error.message))
    attrs.extend(_cost_attributes("llm.usage", step.cost))
    if include_payloads:
        attrs.extend(_payload_attributes(step))

    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": step_span_ids.get(step.parent_id or "", run_span_id),
        "name": step.name or step.type,
        "kind": _span_kind(step),
        "startTimeUnixNano": str(start_nanos),
        "endTimeUnixNano": str(end_nanos),
        "attributes": attrs,
        "status": _status(step.status, step.error.message if step.error else ""),
    }
    events = _events(step, end_nanos)
    if events:
        span["events"] = events
    return span


def _cost_attributes(prefix: str, cost: Cost | None) -> list[dict[str, Any]]:
    if cost is None:
        return []
    return [
        _attr(f"{prefix}.input_tokens", cost.input_tokens),
        _attr(f"{prefix}.output_tokens", cost.output_tokens),
        _attr(f"{prefix}.total_tokens", cost.total_tokens),
        _attr(f"{prefix}.cost_usd", cost.amount_usd),
    ]


def _payload_attributes(step: Step) -> list[dict[str, Any]]:
    attrs: list[dict[str, Any]] = []
    if step.input is not None:
        attrs.append(_attr("agent.step.input_json", _json_string(step.input)))
    if step.output is not None:
        attrs.append(_attr("agent.step.output_json", _json_string(step.output)))
    if step.tool and step.tool.args is not None:
        attrs.append(_attr("tool.args_json", _json_string(step.tool.args)))
    if step.tool and step.tool.result is not None:
        attrs.append(_attr("tool.result_json", _json_string(step.tool.result)))
    return attrs


def _events(step: Step, fallback_nanos: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event in step.events:
        attrs: list[dict[str, Any]] = []
        if event.message:
            attrs.append(_attr("message", event.message))
        if event.data is not None:
            attrs.append(_attr("data_json", _json_string(event.data)))
        events.append(
            {
                "timeUnixNano": str(_timestamp_to_nanos(event.timestamp, fallback=fallback_nanos)),
                "name": event.type or "event",
                "attributes": attrs,
            }
        )
    if step.error:
        attrs = [
            _attr("exception.type", step.error.type),
            _attr("exception.message", step.error.message),
        ]
        if step.error.stack:
            attrs.append(_attr("exception.stacktrace", step.error.stack))
        events.append(
            {
                "timeUnixNano": str(fallback_nanos),
                "name": "exception",
                "attributes": attrs,
            }
        )
    return events


def _span_kind(step: Step) -> int:
    if step.type in {"model_call", "tool_call", "retrieval"}:
        return SPAN_KIND_CLIENT
    return SPAN_KIND_INTERNAL


def _status(status: str, message: str = "") -> dict[str, Any]:
    if status == "success":
        return {"code": STATUS_CODE_OK}
    result: dict[str, Any] = {"code": STATUS_CODE_ERROR}
    if message:
        result["message"] = message
    return result


def _time_range(started_at: str, ended_at: str | None, duration_ms: float | None) -> tuple[int, int]:
    start_nanos = _timestamp_to_nanos(started_at)
    if ended_at:
        return start_nanos, _timestamp_to_nanos(ended_at, fallback=start_nanos)
    if duration_ms is not None:
        return start_nanos, start_nanos + int(float(duration_ms) * 1_000_000)
    return start_nanos, start_nanos


def _timestamp_to_nanos(value: str | None, *, fallback: int = 0) -> int:
    if not value:
        return fallback
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt.astimezone(timezone.utc) - UNIX_EPOCH
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000_000) + (delta.microseconds * 1_000)


def _stable_hex(namespace: str, value: str, *, length: int) -> str:
    digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).hexdigest()
    return digest[:length]


def _attr(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": _any_value(value)}


def _any_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    return {"stringValue": _json_string(value)}


def _json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _resolve_endpoint(endpoint: str | None) -> str:
    if endpoint:
        return endpoint
    traces_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    if traces_endpoint:
        return traces_endpoint
    base_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if base_endpoint:
        return _append_traces_path(base_endpoint)
    return DEFAULT_OTLP_HTTP_ENDPOINT


def _append_traces_path(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.path.endswith("/v1/traces"):
        return endpoint
    return endpoint.rstrip("/") + "/v1/traces"


def _validate_otlp_endpoint(
    endpoint: str,
    *,
    allow_private_endpoint: bool,
    allow_insecure_endpoint: bool,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        raise OtlpHttpExportError(endpoint, message="invalid OTLP endpoint scheme; expected http or https")
    if parsed.username or parsed.password:
        raise OtlpHttpExportError(endpoint, message="invalid OTLP endpoint; userinfo is not allowed")
    host = parsed.hostname
    if not host:
        raise OtlpHttpExportError(endpoint, message="invalid OTLP endpoint; missing host")

    literal_ip = _parse_ip(host)
    if literal_ip is not None and _is_blocked_ip(literal_ip) and not allow_private_endpoint:
        raise OtlpHttpExportError(endpoint, message="private OTLP endpoint blocked; pass allow_private_endpoint to override")

    if parsed.scheme == "http" and not allow_insecure_endpoint and not _is_loopback_host(host):
        raise OtlpHttpExportError(endpoint, message="insecure OTLP endpoint blocked; use https or loopback http")

    if allow_private_endpoint or _is_loopback_host(host) or literal_ip is not None:
        return []

    resolved_ips = _resolve_host_ips(host, endpoint)
    for resolved_ip in resolved_ips:
        if _is_blocked_ip(resolved_ip):
            raise OtlpHttpExportError(endpoint, message="private OTLP endpoint blocked; pass allow_private_endpoint to override")
    return resolved_ips


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    ip = _parse_ip(host)
    return bool(ip and ip.is_loopback)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback:
        return False
    return ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def _resolve_host_ips(host: str, endpoint: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OtlpHttpExportError(endpoint, message=f"could not resolve OTLP endpoint host: {host}") from exc

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        address = info[4][0]
        parsed = _parse_ip(address)
        if parsed is not None:
            addresses.append(parsed)
    return addresses


@contextmanager
def _pinned_dns_resolution(host: str, addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address]):
    if not host or not addresses:
        yield
        return

    original_getaddrinfo = socket.getaddrinfo
    pinned_host = host.rstrip(".").lower()

    def pinned_getaddrinfo(query_host, port, family=0, type=0, proto=0, flags=0):
        query_name = str(query_host).rstrip(".").lower()
        if query_name != pinned_host:
            return original_getaddrinfo(query_host, port, family, type, proto, flags)

        results = []
        socktype = type or socket.SOCK_STREAM
        for address in addresses:
            address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            if family not in (0, address_family):
                continue
            sockaddr = (str(address), port, 0, 0) if address.version == 6 else (str(address), port)
            results.append((address_family, socktype, proto, "", sockaddr))
        return results or original_getaddrinfo(query_host, port, family, type, proto, flags)

    socket.getaddrinfo = pinned_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def _env_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    headers.update(_parse_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")))
    headers.update(_parse_headers(os.getenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "")))
    return headers


def _parse_headers(value: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for part in value.split(","):
        if not part.strip() or "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        key = key.strip()
        if _is_safe_http_header_name(key):
            headers[key] = raw_value.strip()
    return headers


def _safe_http_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if _is_safe_http_header_name(key)}


def _is_safe_http_header_name(name: str) -> bool:
    normalized = name.strip().lower()
    if not normalized or normalized in DANGEROUS_HTTP_HEADERS:
        return False
    return bool(HEADER_NAME_RE.fullmatch(name.strip()))


def _env_timeout_seconds() -> float:
    raw = os.getenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT") or os.getenv("OTEL_EXPORTER_OTLP_TIMEOUT")
    if not raw:
        return 10.0
    try:
        value = float(raw)
    except ValueError:
        return 10.0
    if value <= 0:
        return 10.0
    # OTEL environment timeout values are commonly expressed in milliseconds.
    return value / 1000 if value > 100 else value
