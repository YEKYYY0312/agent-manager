"""Forward one Cursor stdio hook payload to the local Agent DevTools API."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_URL = "http://127.0.0.1:8791/api/hooks/cursor"
DEFAULT_LOG = Path(__file__).resolve().parents[1] / ".agent-devtools" / "cursor-hook-forward.log"


def _allow_response(event_name: str) -> dict[str, Any]:
    if event_name == "beforeSubmitPrompt":
        return {"continue": True}
    return {}


def _write_diagnostic(log_path: str, event_name: str, url: str, error: Exception) -> None:
    if not log_path:
        return
    try:
        path = Path(log_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        status = getattr(error, "code", None)
        status_text = f" status={status}" if status is not None else ""
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"{timestamp} event={event_name or 'unknown'} url={url} "
                f"error={type(error).__name__}{status_text}\n"
            )
    except OSError:
        pass


def _repair_windows_utf8(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return value.encode("mbcs").decode("utf-8")
        except (LookupError, UnicodeEncodeError, UnicodeDecodeError):
            return value
    if isinstance(value, list):
        return [_repair_windows_utf8(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_windows_utf8(item) for key, item in value.items()}
    return value


def main() -> int:
    input_error: Exception | None = None
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        payload = {}
        input_error = exc
    payload = _repair_windows_utf8(payload)
    event_name = str(payload.get("hook_event_name", "")) if isinstance(payload, dict) else ""
    url = os.getenv("AGENT_DEVTOOLS_CURSOR_HOOK_URL", DEFAULT_URL).strip() or DEFAULT_URL
    log_path = os.getenv("AGENT_DEVTOOLS_CURSOR_HOOK_LOG", str(DEFAULT_LOG)).strip()

    if input_error is not None:
        _write_diagnostic(log_path, event_name, url, input_error)

    if isinstance(payload, dict) and payload:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3.0) as response:
                response.read()
        except OSError as exc:
            _write_diagnostic(log_path, event_name, url, exc)

    sys.stdout.write(json.dumps(_allow_response(event_name), separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
