"""Trace file writer — persists Trace objects to JSON files.

Treats schemas/trace.schema.json as the contract. Validates basic structure
before writing so broken traces are caught early.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .trace import Trace

from .redaction import RedactionConfig, redact_value


class TraceWriter:
    """Writes a Trace to a JSON file.

    Usage::

        writer = TraceWriter("traces/")
        writer.write(trace)
    """

    def __init__(self, output_dir: str = "traces", redaction: bool | RedactionConfig | None = None) -> None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        self.output_dir = output_path.resolve()
        self.redaction = _normalize_redaction(redaction)

    def write(self, trace: Trace, filename: str | None = None) -> Path:
        """Persist *trace* to a ``.trace.json`` file.

        If *filename* is omitted it is derived from the run id.
        """
        if filename is None:
            filename = _safe_trace_filename(trace.run.id)
        path = _resolve_output_file(self.output_dir, filename)

        data = self._to_serializable(trace)
        _validate_structure(data)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def write_atomic(self, trace: Trace, filename: str | None = None) -> Path:
        """Same as :meth:`write` but writes to a temp file first, then renames."""
        if filename is None:
            filename = _safe_trace_filename(trace.run.id)
        final_path = _resolve_output_file(self.output_dir, filename)
        tmp_path = final_path.with_name(f"{final_path.name}.tmp")

        data = self._to_serializable(trace)
        _validate_structure(data)
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(final_path)
        return final_path

    def _to_serializable(self, trace: Trace) -> dict:
        data = trace.to_dict()
        if self.redaction is None:
            return data
        return redact_value(data, self.redaction)


def _normalize_redaction(redaction: bool | RedactionConfig | None) -> RedactionConfig | None:
    if redaction is True:
        return RedactionConfig()
    if isinstance(redaction, RedactionConfig):
        return redaction
    if redaction is None and _env_redaction_enabled():
        return RedactionConfig()
    return None


def _env_redaction_enabled() -> bool:
    return os.getenv("AGENT_DEVTOOLS_REDACT_ON_WRITE", "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_trace_filename(run_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id))
    safe_id = re.sub(r"\.{2,}", "_", safe_id).strip("._-")
    return f"{safe_id or 'trace'}.trace.json"


def _resolve_output_file(output_dir: Path, filename: str) -> Path:
    candidate = Path(filename)
    if candidate.is_absolute() or len(candidate.parts) != 1:
        raise ValueError("Trace filename must resolve inside output_dir")
    final_path = (output_dir / candidate.name).resolve()
    if final_path.parent != output_dir:
        raise ValueError("Trace filename must resolve inside output_dir")
    return final_path


def _validate_structure(data: dict) -> None:
    """Lightweight structural validation against the schema contract.

    Raises ValueError if mandatory fields are missing so a broken trace
    never hits disk silently.
    """
    if not isinstance(data, dict):
        raise ValueError("Trace root must be a JSON object")

    run = data.get("run")
    if not isinstance(run, dict):
        raise ValueError("Trace is missing 'run' object")

    required_run = ["id", "task", "status", "started_at"]
    for key in required_run:
        if key not in run or run[key] is None:
            raise ValueError(f"Trace run is missing required field: {key}")

    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Trace is missing 'steps' array")

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{i}] must be an object")
        for key in ("id", "type", "name", "status", "started_at"):
            if key not in step:
                raise ValueError(f"steps[{i}] is missing required field: {key}")
