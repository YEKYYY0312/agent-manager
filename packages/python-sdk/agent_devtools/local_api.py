"""Loopback HTTP API for the local Agent DevTools workspace."""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

from .local import LocalWorkspace, import_new_traces
from .store import TraceStore


def create_server(config: LocalWorkspace, *, port: int = 8765) -> ThreadingHTTPServer:
    """Create an API server bound only to the IPv4 loopback interface."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(config))
    server.daemon_threads = True
    return server


def serve(config: LocalWorkspace, *, port: int = 8765) -> int:
    """Serve local trace data until interrupted."""
    server = create_server(config, port=port)
    print(f"Local Trace API listening at http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _handler(config: LocalWorkspace):
    class LocalApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - HTTP handler interface
            path = urlsplit(self.path).path
            try:
                store = TraceStore(config.db_path, redaction=True)
                import_new_traces(config, store)
                if path == "/api/health":
                    self._json(200, {"ok": True, "trace_count": len(store.list_traces(limit=100))})
                    return
                if path == "/api/traces":
                    self._json(200, {"traces": [asdict(row) for row in store.list_traces(limit=100)]})
                    return
                if path.startswith("/api/traces/"):
                    run_id = unquote(path.removeprefix("/api/traces/"))
                    trace = store.get_trace(run_id) if run_id and "/" not in run_id else None
                    if trace is None:
                        self._json(404, {"error": "Trace not found"})
                    else:
                        self._json(200, trace.to_dict())
                    return
            except (OSError, ValueError, json.JSONDecodeError):
                self._json(500, {"error": "Unable to read the local Trace store"})
                return
            self._json(404, {"error": "Not found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return LocalApiHandler
