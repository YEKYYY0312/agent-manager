"""Subprocess entry point for executing replay-adapter callables."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

_sdk = Path(__file__).resolve().parents[2] / "python-sdk"
if str(_sdk) not in sys.path:
    sys.path.append(str(_sdk))

from agent_devtools import CallableAgentAdapter
from agent_devtools_cli.main import _load_callable


def main() -> int:
    try:
        request = json.loads(_decode(sys.stdin.read().strip()))
        fn = _load_callable(request["callable"], request.get("pythonpath"))
        adapter = CallableAgentAdapter(fn, name=request.get("name"))
        result = adapter.run(
            task=request["task"],
            input=request.get("input"),
            labels=request.get("labels") or {},
            output_dir=request["output_dir"],
        )
        trace_path = Path(request["output_dir"]) / f"{result.trace.run.id}.trace.json"
        response = {
            "output": result.output,
            "trace": result.trace.to_dict(),
            "trace_path": str(trace_path),
            "error": result.error.to_dict() if result.error else None,
        }
        encoded = _encode(response)
        response_path = request.get("response_path")
        if response_path:
            Path(response_path).write_text(encoded, encoding="ascii")
        else:
            print(encoded, end="")
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _encode(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def _decode(value: str) -> str:
    return base64.b64decode(value.encode("ascii")).decode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
