"""Loopback HTTP API for project-scoped team Trace storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .team import PostgresTeamRepository, TeamTraceService
from .trace import Trace

_MAX_REQUEST_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class _MemoryBackend:
    service: TeamTraceService

    def accepts_project(self, project_id: str) -> bool:
        return project_id == self.service.project_id

    def write(self, project_id: str, token: str, trace: Trace, retention_days: int) -> str:
        return self.service.upsert(token, trace, retention_days=retention_days)

    def read(self, project_id: str, token: str, run_id: str) -> Trace | None:
        return self.service.get(token, run_id)

    def list(self, project_id: str, token: str, query: str | None, limit: int) -> list[Trace]:
        return self.service.list(token, query=query, limit=limit)

    def purge(self, project_id: str, token: str) -> int:
        return self.service.purge_expired(token)


@dataclass(frozen=True)
class _PostgresBackend:
    repository: PostgresTeamRepository

    def accepts_project(self, project_id: str) -> bool:
        return bool(project_id)

    def write(self, project_id: str, token: str, trace: Trace, retention_days: int) -> str:
        return self.repository.upsert_trace(project_id, token, trace, retention_days=retention_days)

    def read(self, project_id: str, token: str, run_id: str) -> Trace | None:
        return self.repository.get_trace(project_id, token, run_id)

    def list(self, project_id: str, token: str, query: str | None, limit: int) -> list[Trace]:
        return self.repository.list_traces(project_id, token, query=query, limit=limit)

    def purge(self, project_id: str, token: str) -> int:
        return self.repository.purge_expired(project_id, token)


def create_server(service: TeamTraceService, *, port: int = 8767) -> ThreadingHTTPServer:
    """Create a loopback API backed by the in-memory team service."""
    return _create_server(_MemoryBackend(service), port=port)


def create_postgres_server(repository: PostgresTeamRepository, *, port: int = 8767) -> ThreadingHTTPServer:
    """Create a loopback API backed by a PostgreSQL team repository."""
    return _create_server(_PostgresBackend(repository), port=port)


def _create_server(backend: _MemoryBackend | _PostgresBackend, *, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            project_id, resource, run_id = self._route()
            if not self._accepts(project_id, resource):
                return
            token = self._token()
            if token is None:
                return
            try:
                if resource == "traces" and run_id:
                    trace = backend.read(project_id, token, run_id)
                    self._send(200, trace.to_dict()) if trace else self._send(404, {"error": "Trace not found"})
                    return
                if resource == "traces":
                    params = parse_qs(urlsplit(self.path).query)
                    query = params.get("query", [None])[0]
                    limit = _parse_limit(params.get("limit", ["100"])[0])
                    self._send(200, {"traces": [trace.to_dict() for trace in backend.list(project_id, token, query, limit)]})
                    return
                self._send(404, {"error": "Not found"})
            except PermissionError:
                self._send(403, {"error": "Forbidden"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            project_id, resource, run_id = self._route()
            if not self._accepts(project_id, resource) or resource != "traces" or run_id:
                return
            token = self._token()
            if token is None:
                return
            try:
                payload = self._payload()
                trace = Trace.from_dict(payload["trace"])
                retention_days = payload.get("retention_days", 30)
                if not isinstance(retention_days, int) or isinstance(retention_days, bool):
                    raise ValueError("retention_days must be an integer")
                run_id = backend.write(project_id, token, trace, retention_days)
                self._send(201, {"run_id": run_id})
            except PermissionError:
                self._send(403, {"error": "Forbidden"})
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                self._send(400, {"error": "Invalid Trace request"})

        def do_DELETE(self) -> None:  # noqa: N802
            project_id, resource, run_id = self._route()
            if not self._accepts(project_id, resource) or resource != "expired" or run_id:
                return
            token = self._token()
            if token is None:
                return
            try:
                self._send(200, {"deleted": backend.purge(project_id, token)})
            except PermissionError:
                self._send(403, {"error": "Forbidden"})

        def _route(self) -> tuple[str, str, str | None]:
            parts = urlsplit(self.path).path.split("/")
            if len(parts) not in (5, 6) or parts[:3] != ["", "api", "projects"]:
                return "", "", None
            project_id = unquote(parts[3])
            resource = parts[4]
            run_id = unquote(parts[5]) if len(parts) == 6 else None
            if not project_id or "/" in project_id or (run_id is not None and (not run_id or "/" in run_id)):
                return "", "", None
            return project_id, resource, run_id

        def _accepts(self, project_id: str, resource: str) -> bool:
            if not project_id or resource not in {"traces", "expired"} or not backend.accepts_project(project_id):
                self._send(404, {"error": "Not found"})
                return False
            return True

        def _token(self) -> str | None:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer ") or not header[7:]:
                self._send(401, {"error": "Bearer token required"})
                return None
            return header[7:]

        def _payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > _MAX_REQUEST_BYTES:
                raise ValueError("request body size is invalid")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            return payload

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def _parse_limit(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("limit must be an integer between 1 and 100") from exc
