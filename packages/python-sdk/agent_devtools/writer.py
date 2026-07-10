"""Trace file writer — persists Trace objects to JSON files.

Treats schemas/trace.schema.json as the contract. Validates basic structure
before writing so broken traces are caught early.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .trace import Trace

from .redaction import RedactionConfig, normalize_redaction_config, redact_value
from .trace import _max_step_events


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
        self.redaction = normalize_redaction_config(redaction)

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
            _to_strict_json(data),
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
            _to_strict_json(data),
            encoding="utf-8",
        )
        tmp_path.replace(final_path)
        return final_path

    def _to_serializable(self, trace: Trace) -> dict:
        data = trace.to_dict()
        if self.redaction is None:
            return data
        return redact_value(data, self.redaction)


def _safe_trace_filename(run_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id))
    safe_id = re.sub(r"\.{2,}", "_", safe_id).strip("._-")
    digest = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:12]
    return f"{safe_id or 'trace'}-{digest}.trace.json"


def _resolve_output_file(output_dir: Path, filename: str) -> Path:
    output_root = output_dir.resolve()
    candidate = Path(filename)
    if candidate.is_absolute() or len(candidate.parts) != 1:
        raise ValueError("Trace filename must resolve inside output_dir")
    _validate_trace_filename(candidate.name)
    final_path = (output_dir / candidate.name).resolve()
    if final_path.parent != output_root:
        raise ValueError("Trace filename must resolve inside output_dir")
    return final_path


def _validate_trace_filename(filename: str) -> None:
    stem = Path(filename).stem
    device_name = stem.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if device_name in reserved:
        raise ValueError("Trace filename uses a reserved device name")


def _to_strict_json(data: dict) -> str:
    _validate_json_numbers(data)
    return json.dumps(data, indent=2, ensure_ascii=False, default=str, allow_nan=False)


def _validate_json_numbers(value: Any, path: str = "$") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Trace JSON contains non-finite number at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_numbers(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_json_numbers(item, f"{path}.{key}")
        return


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
    _validate_cost_values(run.get("cost"), "run.cost")

    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Trace is missing 'steps' array")

    step_ids: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{i}] must be an object")
        for key in ("id", "type", "name", "status", "started_at"):
            if key not in step:
                raise ValueError(f"steps[{i}] is missing required field: {key}")
        events = step.get("events", [])
        if events is not None and not isinstance(events, list):
            raise ValueError(f"steps[{i}].events must be an array")
        if isinstance(events, list) and len(events) > _max_step_events():
            raise ValueError(f"steps[{i}] exceeds maximum event count of {_max_step_events()}")
        _validate_cost_values(step.get("cost"), f"steps[{i}].cost")
        step_ids.add(str(step["id"]))

    for i, step in enumerate(steps):
        parent_id = step.get("parent_id")
        if parent_id and str(parent_id) not in step_ids:
            raise ValueError(f"steps[{i}] references unknown parent_id: {parent_id}")


def _validate_cost_values(cost: Any, path: str) -> None:
    if cost is None:
        return
    if not isinstance(cost, dict):
        raise ValueError(f"{path} must be an object")
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = cost.get(key, 0)
        if isinstance(value, bool):
            raise ValueError(f"{path}.{key} must be a non-negative integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}.{key} must be a non-negative integer") from exc
        if parsed < 0:
            raise ValueError(f"{path}.{key} must be non-negative")
    amount = cost.get("amount_usd", 0.0)
    if isinstance(amount, bool):
        raise ValueError(f"{path}.amount_usd must be a finite non-negative number")
    try:
        parsed_amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}.amount_usd must be a finite non-negative number") from exc
    if not math.isfinite(parsed_amount):
        raise ValueError(f"{path}.amount_usd must be finite")
    if parsed_amount < 0:
        raise ValueError(f"{path}.amount_usd must be non-negative")
