"""Loopback HTTP API for the local Agent DevTools workspace."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .claude_hooks import ClaudeHookIngestor
from .codex_sessions import CodexSessionWatcher, discover_codex_sessions_dir
from .cursor_hooks import CursorHookIngestor
from .local import LocalWorkspace, import_new_traces
from .store import TraceStore

MAX_HOOK_BODY_BYTES = 2 * 1024 * 1024


class LiveTraceHub:
    """Small in-memory event buffer used by loopback SSE clients."""

    def __init__(self, max_events: int = 200) -> None:
        self._condition = threading.Condition()
        self._events: deque[tuple[int, dict[str, Any]]] = deque(maxlen=max_events)
        self._next_id = 1

    def publish(self, payload: dict[str, Any]) -> int:
        with self._condition:
            event_id = self._next_id
            self._next_id += 1
            run_id = payload.get("run_id")
            if isinstance(run_id, str) and run_id:
                retained = [event for event in self._events if event[1].get("run_id") != run_id]
                self._events.clear()
                self._events.extend(retained)
            self._events.append((event_id, payload))
            self._condition.notify_all()
            return event_id

    def wait_after(self, event_id: int, timeout: float = 15.0) -> list[tuple[int, dict[str, Any]]]:
        with self._condition:
            events = [(current_id, payload) for current_id, payload in self._events if current_id > event_id]
            if events:
                return events
            self._condition.wait(timeout)
            return [(current_id, payload) for current_id, payload in self._events if current_id > event_id]


class LocalTraceServer(ThreadingHTTPServer):
    """Loopback server that also polls append-only local agent event files."""

    def __init__(self, *args: Any, codex_watcher: CodexSessionWatcher | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.codex_watcher = codex_watcher
        self.codex_watcher_error: str | None = None
        self._watcher_lock = threading.Lock()
        self._watcher_running = False
        self._watcher_retry_at = 0.0

    def service_actions(self) -> None:
        if self.codex_watcher is None or time.monotonic() < self._watcher_retry_at:
            return
        with self._watcher_lock:
            if self._watcher_running:
                return
            self._watcher_running = True
        threading.Thread(target=self._poll_codex_watcher, daemon=True).start()

    def _poll_codex_watcher(self) -> None:
        try:
            if self.codex_watcher is not None:
                self.codex_watcher.poll_once()
            self.codex_watcher_error = None
        except Exception as exc:
            self.codex_watcher_error = type(exc).__name__
        finally:
            with self._watcher_lock:
                self._watcher_running = False
                self._watcher_retry_at = time.monotonic() + 0.5


def create_server(
    config: LocalWorkspace,
    *,
    port: int = 8791,
    codex_sessions_dir: str | Path | None = None,
) -> ThreadingHTTPServer:
    """Create an API server bound only to the IPv4 loopback interface."""
    live_hub = LiveTraceHub()
    codex_watcher = (
        CodexSessionWatcher(config, codex_sessions_dir, live_hub.publish)
        if codex_sessions_dir is not None
        else None
    )
    server = LocalTraceServer(
        ("127.0.0.1", port),
        _handler(config, ClaudeHookIngestor(config), CursorHookIngestor(config), live_hub),
        codex_watcher=codex_watcher,
    )
    server.daemon_threads = True
    return server


def serve(config: LocalWorkspace, *, port: int = 8791) -> int:
    """Serve local trace data until interrupted."""
    codex_sessions_dir = discover_codex_sessions_dir()
    server = create_server(config, port=port, codex_sessions_dir=codex_sessions_dir)
    print(f"Local Trace API listening at http://127.0.0.1:{server.server_port}")
    if codex_sessions_dir is not None:
        print(f"Codex session watcher: {codex_sessions_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _handler(
    config: LocalWorkspace,
    claude_hooks: ClaudeHookIngestor,
    cursor_hooks: CursorHookIngestor,
    live_hub: LiveTraceHub,
):
    class LocalApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - HTTP handler interface
            path = urlsplit(self.path).path
            if path == "/api/live/traces":
                self._live_traces()
                return
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

        def do_POST(self) -> None:  # noqa: N802 - HTTP handler interface
            path = urlsplit(self.path).path
            if path not in {
                "/api/hooks/claude-code",
                "/api/hooks/cursor",
                "/api/replay/claude-code/register",
                "/api/replay/claude-code/finalize",
            }:
                self._json(404, {"error": "Not found"})
                return
            try:
                payload = self._json_body()
                if path == "/api/replay/claude-code/register":
                    claude_hooks.register_replay(payload)
                    self._json(202, {"ok": True})
                    return
                if path == "/api/replay/claude-code/finalize":
                    result = claude_hooks.finalize_replay(payload)
                elif path == "/api/hooks/cursor":
                    result = cursor_hooks.ingest(payload)
                else:
                    result = claude_hooks.ingest(payload)
                live_hub.publish(
                    {
                        "run_id": result.run_id,
                        "source_event": "cursor-http-hooks" if path == "/api/hooks/cursor" else "claude-code-http-hooks",
                        "hook_event_name": result.hook_event_name,
                        "trace": result.trace.to_dict(),
                    }
                )
            except (json.JSONDecodeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            except OSError:
                self._json(500, {"error": "Unable to store the local hook event"})
                return
            self._json(202, {"ok": True, "run_id": result.run_id})

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

        def _json_body(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length", "")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("Content-Length is required") from exc
            if length <= 0 or length > MAX_HOOK_BODY_BYTES:
                raise ValueError("Claude Code hook payload size is invalid")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Claude Code hook payload must be a JSON object")
            return payload

        def _live_traces(self) -> None:
            try:
                last_event_id = int(self.headers.get("Last-Event-ID", "0"))
            except ValueError:
                last_event_id = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    events = live_hub.wait_after(last_event_id)
                    if not events:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    for event_id, payload in events:
                        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                        self.wfile.write(f"id: {event_id}\n".encode("ascii"))
                        self.wfile.write(b"data: " + body + b"\n\n")
                        self.wfile.flush()
                        last_event_id = event_id
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return LocalApiHandler
