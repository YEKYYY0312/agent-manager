"""Agent DevTools CLI - inspect, compare, and analyze agent trace files.

Usage::

    py packages/cli/agent_devtools_cli/main.py list traces/
    py packages/cli/agent_devtools_cli/main.py show traces/run.trace.json
    py packages/cli/agent_devtools_cli/main.py steps traces/run.trace.json
    py packages/cli/agent_devtools_cli/main.py inspect traces/run.trace.json <step-id>
    py packages/cli/agent_devtools_cli/main.py cost traces/run.trace.json
    py packages/cli/agent_devtools_cli/main.py diff traces/a.trace.json traces/b.trace.json
    py packages/cli/agent_devtools_cli/main.py experiment traces/a.trace.json traces/b.trace.json
    py packages/cli/agent_devtools_cli/main.py replay traces/run.trace.json --start-step <step-id>
    py packages/cli/agent_devtools_cli/main.py replay-adapter traces/run.trace.json --start-step <step-id> --callable path/to/agent.py:run --allow-unsafe-code
    py packages/cli/agent_devtools_cli/main.py replay-compare traces/source.trace.json traces/replay.trace.json
    py packages/cli/agent_devtools_cli/main.py regression-check traces/baseline.trace.json traces/candidate.trace.json --max-token-delta 100
    py packages/cli/agent_devtools_cli/main.py redact traces/run.trace.json --output traces/run.safe.trace.json
    py packages/cli/agent_devtools_cli/main.py privacy-scan traces/run.trace.json
    py packages/cli/agent_devtools_cli/main.py otel-export traces/run.trace.json --redact --output traces/run.otlp.json
    py packages/cli/agent_devtools_cli/main.py otel-push traces/run.trace.json --redact --endpoint http://localhost:4318/v1/traces
    py packages/cli/agent_devtools_cli/main.py store import traces --db .agent-devtools/traces.db
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

# Ensure sibling python-sdk is importable
_sdk = Path(__file__).resolve().parents[2] / "python-sdk"
if importlib.util.find_spec("agent_devtools") is None and str(_sdk) not in sys.path:
    sys.path.append(str(_sdk))

from agent_devtools import (
    CallableAgentAdapter,
    Cost,
    Trace,
    TraceStore,
    TraceWriter,
    RegressionThresholds,
    check_regression,
    create_replay_trace,
    push_trace_to_otlp_http,
    redact_trace,
    replay_with_adapter,
    scan_trace_for_secrets,
    trace_to_otlp_json,
    write_otlp_json,
)
from agent_devtools.analysis import analyze
from agent_devtools.experiment import compare_experiment
from agent_devtools.replay_compare import compare_replay
from agent_devtools.replay import _adapter_input, _find_start_index

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load(path_str: str) -> Trace:
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"Trace file not found: {path}")
    try:
        return Trace.from_file(str(path))
    except Exception as exc:
        raise SystemExit(f"Failed to load trace file {path}: {exc}")


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}ms"


def _fmt_usd(value: float | None) -> str:
    if value is None or value == 0.0:
        return "$0.000000"
    return f"${float(value):.6f}"


def _truncate(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _status_marker(status: str) -> str:
    return "!" if status != "success" else "-"


def _total_step_tokens(steps: list) -> int:
    return sum((s.cost.total_tokens if s.cost else 0) for s in steps)


def _total_step_cost(steps: list) -> float:
    return sum((s.cost.amount_usd if s.cost else 0.0) for s in steps)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def command_list(args: argparse.Namespace) -> int:
    directory = Path(args.directory)
    traces = sorted(directory.glob("*.trace.json"))
    if not traces:
        print(f"No trace files found in {directory}")
        return 0

    rows: list[tuple[str, str, str, str, int, float, str]] = []
    for path in traces:
        trace = _load(str(path))
        tokens = _total_step_tokens(trace.steps)
        cost_usd = _total_step_cost(trace.steps)
        rows.append((
            path.name,
            trace.run.id,
            trace.run.status,
            _fmt_ms(trace.run.duration_ms),
            tokens,
            cost_usd,
            trace.run.task,
        ))

    # Table header
    header = f"{'FILE':<32} {'ID':<14} {'STATUS':<10} {'DURATION':<10} {'TOKENS':<8} {'COST':<12} TASK"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r[0]:<32} {r[1]:<14} {r[2]:<10} {r[3]:<10} {r[4]:<8} {_fmt_usd(r[5]):<12} {_truncate(r[6], 60)}")
    return 0


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def command_show(args: argparse.Namespace) -> int:
    trace = _load(args.trace)
    run = trace.run
    total_cost = trace.total_cost()

    print(f"Run:        {run.id}")
    print(f"Task:       {run.task}")
    print(f"Status:     {run.status}")
    print(f"Started:    {run.started_at}")
    if run.ended_at:
        print(f"Ended:      {run.ended_at}")
    print(f"Duration:   {_fmt_ms(run.duration_ms)}")
    print(f"Steps:      {len(trace.steps)}")
    print(f"Tokens:     {total_cost.total_tokens:.0f}")
    print(f"Cost:       {_fmt_usd(total_cost.amount_usd)}")
    if run.final_output:
        print(f"Output:     {_truncate(str(run.final_output), 100)}")

    print(f"\n{'#' if args.show_detail else '-'} Timeline:")
    for step in trace.steps:
        marker = _status_marker(step.status)
        cost_str = f"  {step.cost.total_tokens}t {_fmt_usd(step.cost.amount_usd if step.cost else None)}" if step.cost and step.cost.total_tokens > 0 else ""
        print(f"  {marker} {step.id} [{step.type}] {step.name}  {step.status}  {_fmt_ms(step.duration_ms)}{cost_str}")
        if args.show_detail:
            if step.input is not None:
                inp = _truncate(json.dumps(step.input, ensure_ascii=False, default=str), 100)
                print(f"       in:  {inp}")
            if step.output is not None:
                out = _truncate(json.dumps(step.output, ensure_ascii=False, default=str), 100)
                print(f"       out: {out}")
            if step.error:
                print(f"       err: {step.error.type}: {step.error.message}")
    return 0


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


def command_steps(args: argparse.Namespace) -> int:
    trace = _load(args.trace)

    header = f"{'ID':<14} {'TYPE':<12} {'NAME':<24} {'STATUS':<10} {'DURATION':<10} {'TOKENS':<8} COST"
    print(header)
    print("-" * len(header))
    for step in trace.steps:
        tokens = step.cost.total_tokens if step.cost else 0
        cost_usd = step.cost.amount_usd if step.cost else 0.0
        print(
            f"{step.id:<14} {step.type:<12} {step.name:<24} {step.status:<10} "
            f"{_fmt_ms(step.duration_ms):<10} {tokens:<8} {_fmt_usd(cost_usd)}"
        )
    print(f"\n{len(trace.steps)} steps")
    return 0


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def command_inspect(args: argparse.Namespace) -> int:
    trace = _load(args.trace)
    for step in trace.steps:
        if step.id == args.step_id:
            print(json.dumps(step.to_dict(), indent=2, ensure_ascii=False, default=str))
            return 0
    raise SystemExit(f"Step not found: {args.step_id}")


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


def command_cost(args: argparse.Namespace) -> int:
    trace = _load(args.trace)
    steps_with_cost = [s for s in trace.steps if s.cost and s.cost.total_tokens > 0]

    if not steps_with_cost:
        print("No cost data in this trace.")
        return 0

    totals = trace.total_cost()

    print(f"Input tokens:  {totals.input_tokens:.0f}")
    print(f"Output tokens: {totals.output_tokens:.0f}")
    print(f"Total tokens:  {totals.total_tokens:.0f}")
    print(f"Total cost:    {_fmt_usd(totals.amount_usd)}")

    # By model
    model_stats: dict[str, Cost] = {}
    for step in steps_with_cost:
        m = step.model or "(unknown)"
        if m not in model_stats:
            model_stats[m] = Cost()
        c = step.cost
        model_stats[m].input_tokens += c.input_tokens
        model_stats[m].output_tokens += c.output_tokens
        model_stats[m].total_tokens += c.total_tokens
        model_stats[m].amount_usd += c.amount_usd

    if len(model_stats) > 1:
        print("\nBy model:")
        for model, cost in sorted(model_stats.items()):
            print(f"  {model:<20} {cost.total_tokens:>8}t  {_fmt_usd(cost.amount_usd)}")

    print("\nMost expensive steps:")
    ranked = sorted(steps_with_cost, key=lambda s: s.cost.amount_usd if s.cost else 0.0, reverse=True)
    for step in ranked[:5]:
        print(f"  {step.id}  {_fmt_usd(step.cost.amount_usd if step.cost else None)}  {step.name}")
    return 0


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def command_diff(args: argparse.Namespace) -> int:
    left = _load(args.left_trace)
    right = _load(args.right_trace)

    left_cost = left.total_cost()
    right_cost = right.total_cost()

    print(f"Left:   {left.run.id} ({left.run.status}) - {len(left.steps)} steps, {_fmt_ms(left.run.duration_ms)}, {left_cost.total_tokens:.0f}t, {_fmt_usd(left_cost.amount_usd)}")
    print(f"Right:  {right.run.id} ({right.run.status}) - {len(right.steps)} steps, {_fmt_ms(right.run.duration_ms)}, {right_cost.total_tokens:.0f}t, {_fmt_usd(right_cost.amount_usd)}")

    # Cost delta
    token_delta = right_cost.total_tokens - left_cost.total_tokens
    cost_delta = right_cost.amount_usd - left_cost.amount_usd
    if token_delta != 0 or cost_delta != 0:
        sign = "+" if token_delta >= 0 else ""
        print(f"\n  Tokens: {sign}{token_delta:.0f}   Cost: {cost_delta:+.6f}")

    # Step count delta
    if len(left.steps) != len(right.steps):
        delta = len(right.steps) - len(left.steps)
        sign = "+" if delta >= 0 else ""
        print(f"  Steps:  {sign}{delta}")

    diffs: list[str] = []
    max_len = max(len(left.steps), len(right.steps))
    for index in range(max_len):
        ls = left.steps[index] if index < len(left.steps) else None
        rs = right.steps[index] if index < len(right.steps) else None
        if ls and rs:
            if ls.type != rs.type or ls.name != rs.name:
                diffs.append(f"  ~ [{index}] {ls.type}/{ls.name} -> {rs.type}/{rs.name}")
            if ls.status != rs.status:
                diffs.append(f"  ~ [{index}] status {ls.status} -> {rs.status}")
            left_dur = ls.duration_ms or 0
            right_dur = rs.duration_ms or 0
            if abs(right_dur - left_dur) > 1:
                if left_dur > 0:
                    pct = (right_dur - left_dur) / left_dur * 100
                    sign = "+" if pct >= 0 else ""
                    diffs.append(f"  ~ [{index}] duration {_fmt_ms(left_dur)} -> {_fmt_ms(right_dur)} ({sign}{pct:.0f}%)")
                else:
                    diffs.append(f"  ~ [{index}] duration {_fmt_ms(left_dur)} -> {_fmt_ms(right_dur)}")
            left_cost_t = ls.cost.total_tokens if ls.cost else 0
            right_cost_t = rs.cost.total_tokens if rs.cost else 0
            if left_cost_t != right_cost_t:
                diffs.append(f"  ~ [{index}] cost {left_cost_t}t -> {right_cost_t}t")
        elif ls and not rs:
            diffs.append(f"  - {ls.id} [{ls.type}] {ls.name}  (removed)")
        elif rs and not ls:
            diffs.append(f"  + {rs.id} [{rs.type}] {rs.name}  (added, {rs.status})")

    if diffs:
        print("\nStructural diff:")
        for d in diffs:
            print(d)
    else:
        print("\n  (no structural differences)")

    return 0


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def command_analyze(args: argparse.Namespace) -> int:
    trace = _load(args.trace)
    report = analyze(trace)

    print(f"Trace:  {report.trace_id}")
    print(f"Summary: {report.summary}")
    print()

    # Cost
    ca = report.cost
    print("-- Cost --")
    print(f"  Total:         {ca.total.total_tokens}t  {_fmt_usd(ca.total.amount_usd)}")
    if ca.by_model:
        print("  By model:")
        for model, c in sorted(ca.by_model.items()):
            print(f"    {model:<20} {c.total_tokens:>6}t  {_fmt_usd(c.amount_usd)}")
    if ca.by_step_type:
        print("  By step type:")
        for t, c in sorted(ca.by_step_type.items()):
            print(f"    {t:<20} {c.total_tokens:>6}t  {_fmt_usd(c.amount_usd)}")

    # Latency
    la = report.latency
    print(f"\n-- Latency --")
    print(f"  Total:   {_fmt_ms(la.total_ms)}")
    print(f"  Average: {_fmt_ms(la.avg_ms)}")
    if la.slowest:
        print("  Slowest steps:")
        for sid, dur, name in la.slowest:
            print(f"    {sid}  {_fmt_ms(dur)}  {name}")
    if la.by_step_type:
        print("  By step type:")
        for t, dur in sorted(la.by_step_type.items()):
            pct = (dur / la.total_ms * 100) if la.total_ms > 0 else 0
            print(f"    {t:<20} {_fmt_ms(dur)}  ({pct:.0f}%)")

    # Failures
    fa = report.failure
    if fa:
        print(f"\n-- Failures --")
        print(f"  Run status:   {fa.run_status}")
        print(f"  Failed steps: {len(fa.failed_steps)}/{fa.total_steps} ({fa.failure_rate:.0%})")
        for fp in fa.failed_steps:
            print(f"    [{fp.position}] {fp.step_id} {fp.step_name}: {fp.status} - {fp.error_type}: {fp.error_message}")
    else:
        print(f"\n-- Failures --")
        print(f"  All {report.cost.step_count} steps passed.")

    # Loops
    lp = report.loops
    print(f"\n-- Loops --")
    if lp.has_suspicious_loops:
        for lc in lp.loops:
            print(f"  {lc.step_name} [{lc.step_type}] x{lc.count} (steps {lc.first_index}-{lc.last_index})")
    else:
        print("  No suspicious loops detected.")

    # Retries
    ra = report.retries
    print(f"\n-- Retries --")
    if ra.total_retry_chains:
        for rc in ra.retries:
            outcome = "succeeded" if rc.succeeded else "failed"
            print(f"  {rc.step_name} [{rc.step_type}] x{rc.attempts} attempts {outcome}")
    else:
        print("  No retry chains detected.")

    return 0


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def command_replay(args: argparse.Namespace) -> int:
    source = _load(args.trace)
    plan = _load_replay_plan(getattr(args, "plan", None), source)
    start_step_id = args.start_step or plan.get("start_step_id")
    if not start_step_id:
        raise SystemExit("Replay requires --start-step or a replay plan with start_step_id")
    tool_mocks = plan.get("mocked_tools")
    try:
        replay = create_replay_trace(source, start_step_id, tool_mocks=tool_mocks)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if plan:
        replay.run.labels["replay_plan"] = "true"

    writer = TraceWriter(args.output_dir)
    path = writer.write(replay)
    print(f"Replay trace written: {path}")
    print(f"Source run: {source.run.id}")
    print(f"Replay run: {replay.run.id}")
    print(f"Steps:      {len(replay.steps)}")
    if plan:
        print(f"Mocks:      {len(tool_mocks or [])}")
    return 0


# ---------------------------------------------------------------------------
# replay-adapter
# ---------------------------------------------------------------------------


def command_replay_adapter(args: argparse.Namespace) -> int:
    if not _unsafe_code_allowed(args):
        raise SystemExit("replay-adapter executes unsafe code from --callable. Re-run with --allow-unsafe-code only for code you trust.")

    source = _load(args.trace)
    start_index = _find_start_index(source, args.start_step)
    start_step = source.steps[start_index]
    replay_input = _parse_json_arg(args.input_json, "--input-json") if args.input_json is not None else _adapter_input(start_step)
    adapter_name = args.name or _callable_label(args.callable)

    labels = dict(source.run.labels)
    labels.update(
        {
            "replay": "true",
            "replay_mode": "adapter_execution",
            "source_run_id": source.run.id,
            "source_start_step_id": args.start_step,
            "source_run_status": source.run.status,
        }
    )

    result = _run_callable_replay_subprocess(
        callable_spec=args.callable,
        input_value=replay_input,
        task=f"Replay: {source.run.task}",
        labels=labels,
        output_dir=args.output_dir,
        name=args.name,
        pythonpath=args.pythonpath,
    )
    trace = Trace.from_dict(result["trace"])
    output = result.get("output")
    error = result.get("error")
    path = Path(result["trace_path"])

    print(f"Adapter replay trace written: {path}")
    print(f"Source run: {source.run.id}")
    print(f"Replay run: {trace.run.id}")
    print(f"Status:     {trace.run.status}")
    print(f"Adapter:    {adapter_name}")
    if output is not None:
        print(f"Output:     {_truncate(str(output), 100)}")
    if error is not None:
        print(f"Error:      {error.get('type', '')}: {error.get('message', '')}")
        return 1
    return 0


def _callable_label(spec: str) -> str:
    target, _, attr_path = spec.rpartition(":")
    return attr_path or target or spec


def _run_callable_replay_subprocess(
    *,
    callable_spec: str,
    input_value: Any,
    task: str,
    labels: dict[str, str],
    output_dir: str,
    name: str | None,
    pythonpath: list[str] | None,
) -> dict[str, Any]:
    request = {
        "callable": callable_spec,
        "input": input_value,
        "task": task,
        "labels": labels,
        "output_dir": output_dir,
        "name": name,
        "pythonpath": pythonpath or [],
    }
    env = os.environ.copy()
    sdk_path = str(_sdk)
    cli_path = str(Path(__file__).resolve().parents[1])
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in [sdk_path, cli_path, existing_pythonpath] if part)
    proc = subprocess.run(
        [sys.executable, "-m", "agent_devtools_cli.callable_runner"],
        input=_encode_runner_payload(request),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise SystemExit(f"Callable replay subprocess failed: {detail}")
    try:
        return json.loads(_decode_runner_payload(proc.stdout.strip()))
    except Exception as exc:
        raise SystemExit(f"Callable replay subprocess returned invalid JSON: {exc}") from exc


def _encode_runner_payload(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def _decode_runner_payload(value: str) -> str:
    return base64.b64decode(value.encode("ascii")).decode("utf-8")


def _load_callable(spec: str, pythonpath: list[str] | None = None) -> Callable[[Any], Any]:
    if ":" not in spec:
        raise SystemExit("Callable must be in the form module:function or path/to/file.py:function")

    target, attr_path = spec.rsplit(":", 1)
    if not target or not attr_path:
        raise SystemExit("Callable must be in the form module:function or path/to/file.py:function")

    with _temporary_pythonpath(pythonpath):
        target_path = Path(target)
        if target_path.suffix == ".py" or target_path.exists():
            obj = _load_callable_from_file(target_path, attr_path)
        else:
            obj = _load_callable_from_module(target, attr_path)

    if not callable(obj):
        raise SystemExit(f"Imported object is not callable: {spec}")
    return obj


def _unsafe_code_allowed(args: argparse.Namespace) -> bool:
    if getattr(args, "allow_unsafe_code", False):
        return True
    return os.getenv("AGENT_DEVTOOLS_ALLOW_UNSAFE_CODE", "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def _temporary_pythonpath(paths: list[str] | None):
    original = list(sys.path)
    try:
        _extend_pythonpath(paths)
        yield
    finally:
        sys.path[:] = original


def _extend_pythonpath(paths: list[str] | None) -> None:
    for raw in paths or []:
        for part in raw.split(os.pathsep):
            if part and part not in sys.path:
                sys.path.insert(0, part)


def _load_callable_from_file(path: Path, attr_path: str) -> Callable[[Any], Any]:
    if not path.exists():
        raise SystemExit(f"Callable file not found: {path}")
    resolved = path.resolve()

    module_name = f"agent_devtools_runtime_{resolved.stem}_{abs(hash(str(resolved)))}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Failed to import callable file: {path}")
    module = importlib.util.module_from_spec(spec)
    with _temporary_pythonpath([str(resolved.parent)]):
        spec.loader.exec_module(module)
    return _resolve_attr(module, attr_path)


def _load_callable_from_module(module_name: str, attr_path: str) -> Callable[[Any], Any]:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise SystemExit(f"Failed to import module {module_name}: {exc}")
    return _resolve_attr(module, attr_path)


def _resolve_attr(root: Any, attr_path: str) -> Any:
    current = root
    for part in attr_path.split("."):
        if not part:
            raise SystemExit(f"Invalid callable attribute path: {attr_path}")
        current = getattr(current, part, None)
        if current is None:
            raise SystemExit(f"Callable attribute not found: {attr_path}")
    return current


def _parse_json_arg(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for {label}: {exc}")


def _load_replay_plan(plan_path: str | None, source: Trace) -> dict[str, Any]:
    if not plan_path:
        return {}
    path = Path(plan_path)
    if not path.exists():
        raise SystemExit(f"Replay plan not found: {path}")
    try:
        plan = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid replay plan JSON: {exc}")
    if not isinstance(plan, dict):
        raise SystemExit("Replay plan must be a JSON object")

    plan_run_id = plan.get("run_id")
    if isinstance(plan_run_id, str) and plan_run_id and plan_run_id != source.run.id:
        raise SystemExit(f"Replay plan run_id does not match source trace: {plan_run_id} != {source.run.id}")

    mocks = plan.get("mocked_tools", [])
    if mocks is None:
        plan["mocked_tools"] = []
    elif not isinstance(mocks, list):
        raise SystemExit("Replay plan mocked_tools must be a list")
    return plan


# ---------------------------------------------------------------------------
# replay-compare
# ---------------------------------------------------------------------------


def command_replay_compare(args: argparse.Namespace) -> int:
    source = _load(args.source_trace)
    replay = _load(args.replay_trace)
    try:
        report = compare_replay(source, replay)
    except ValueError as exc:
        raise SystemExit(str(exc))

    print("Replay comparison: original vs replay")
    print(f"Source: {report.source_run_id} ({report.source_status}) - {report.source_step_count} replayed steps, {_fmt_ms(report.source_duration_ms)}, {report.source_tokens}t, {_fmt_usd(report.source_cost_usd)}")
    print(f"Replay: {report.replay_run_id} ({report.replay_status}) - {report.replay_step_count} steps, {_fmt_ms(report.replay_duration_ms)}, {report.replay_tokens}t, {_fmt_usd(report.replay_cost_usd)}")
    print(f"Source match: {'yes' if report.source_run_match else 'no'}")
    print(f"Start step: {report.source_start_step_id or 'n/a'}")
    print(f"Replay mode: {report.replay_mode}")
    print()
    print(f"Status changed: {'yes' if report.status_changed else 'no'}")
    print(f"Output changed: {'yes' if report.output_changed else 'no'}")
    print(f"Token delta: {report.delta.token_delta:+d}")
    print(f"Cost delta: {report.delta.cost_delta_usd:+.6f}")
    print(f"Latency delta: {report.delta.latency_delta_ms:+.0f}ms")
    print(f"Step delta: {report.delta.step_count_delta:+d}")

    if report.step_changes:
        print("\nReplay differences:")
        for change in report.step_changes[:20]:
            print(f"  ~ [{change.index}] {change.kind}: {change.detail}")
        if len(report.step_changes) > 20:
            print(f"  ... {len(report.step_changes) - 20} more")
    else:
        print("\n  (no replay differences)")

    return 0


# ---------------------------------------------------------------------------
# experiment
# ---------------------------------------------------------------------------


def command_experiment(args: argparse.Namespace) -> int:
    left = _load(args.left_trace)
    right = _load(args.right_trace)
    report = compare_experiment(left, right)

    print("Experiment: A vs B")
    print(f"A: {report.left.trace_id} ({report.left.status}) - {report.left.step_count} steps, {_fmt_ms(report.left.duration_ms)}, {report.left.total_tokens}t, {_fmt_usd(report.left.cost_usd)}")
    print(f"B: {report.right.trace_id} ({report.right.status}) - {report.right.step_count} steps, {_fmt_ms(report.right.duration_ms)}, {report.right.total_tokens}t, {_fmt_usd(report.right.cost_usd)}")
    print()
    print(f"Winner by success: {report.winner_by_success}")
    print(f"Winner by cost: {report.winner_by_cost}")
    print(f"Winner by latency: {report.winner_by_latency}")
    print(f"Token delta: {report.delta.token_delta:+d}")
    print(f"Cost delta: {report.delta.cost_delta_usd:+.6f}")
    print(f"Latency delta: {report.delta.latency_delta_ms:+.0f}ms")
    print(f"Step delta: {report.delta.step_count_delta:+d}")
    print(f"Output changed: {'yes' if report.delta.output_changed else 'no'}")
    print(f"Recommendation: {report.recommendation}")
    print(f"Reason: {report.recommendation_reason}")
    return 0


# ---------------------------------------------------------------------------
# regression-check
# ---------------------------------------------------------------------------


def command_regression_check(args: argparse.Namespace) -> int:
    baseline = _load(args.baseline_trace)
    candidate = _load(args.candidate_trace)
    report = check_regression(
        baseline,
        candidate,
        RegressionThresholds(
            max_token_delta=args.max_token_delta,
            max_cost_delta_usd=args.max_cost_delta_usd,
            max_latency_delta_ms=args.max_latency_delta_ms,
            max_step_count_delta=args.max_step_count_delta,
            allow_output_change=not args.fail_on_output_change,
        ),
    )

    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0 if report.passed else 1

    status = "PASS" if report.passed else "FAIL"
    delta = report.experiment.delta
    print(f"Regression check: {status}")
    print(f"Baseline:  {report.experiment.left.trace_id} ({report.experiment.left.status}) - {report.experiment.left.step_count} steps, {_fmt_ms(report.experiment.left.duration_ms)}, {report.experiment.left.total_tokens}t, {_fmt_usd(report.experiment.left.cost_usd)}")
    print(f"Candidate: {report.experiment.right.trace_id} ({report.experiment.right.status}) - {report.experiment.right.step_count} steps, {_fmt_ms(report.experiment.right.duration_ms)}, {report.experiment.right.total_tokens}t, {_fmt_usd(report.experiment.right.cost_usd)}")
    print()
    print(f"Token delta: {delta.token_delta:+d}")
    print(f"Cost delta: {delta.cost_delta_usd:+.6f}")
    print(f"Latency delta: {delta.latency_delta_ms:+.0f}ms")
    print(f"Step delta: {delta.step_count_delta:+d}")
    print(f"Output changed: {'yes' if delta.output_changed else 'no'}")

    failed_checks = [check for check in report.checks if not check.passed]
    if failed_checks:
        print("\nFailed checks:")
        for check in failed_checks:
            print(f"  - {check.name}: {check.detail}")
    else:
        print("\nAll regression checks passed.")
    return 0 if report.passed else 1


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


def command_redact(args: argparse.Namespace) -> int:
    source_path = Path(args.trace)
    trace = _load(args.trace)
    redacted = redact_trace(trace)
    output_path = Path(args.output) if args.output else source_path.with_name(f"{source_path.stem}.redacted.trace.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(redacted.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"Redacted trace written: {output_path}")
    return 0


# ---------------------------------------------------------------------------
# privacy-scan
# ---------------------------------------------------------------------------


def command_privacy_scan(args: argparse.Namespace) -> int:
    trace = _load(args.trace)
    findings = scan_trace_for_secrets(trace)
    if args.json_output:
        print(json.dumps({
            "trace": args.trace,
            "finding_count": len(findings),
            "findings": [finding.to_dict() for finding in findings],
        }, indent=2, ensure_ascii=False))
    elif findings:
        print(f"Sensitive trace findings: {len(findings)}")
        for finding in findings[:20]:
            print(f"  - {finding.path} ({finding.kind})")
        if len(findings) > 20:
            print(f"  ... {len(findings) - 20} more")
        print("Use redact, --redact, or --allow-sensitive before exporting/storing this trace.")
    else:
        print("No sensitive trace findings detected.")
    return 1 if findings else 0


# ---------------------------------------------------------------------------
# otel-export
# ---------------------------------------------------------------------------


def command_otel_export(args: argparse.Namespace) -> int:
    trace = _load(args.trace)
    trace = _prepare_sensitive_trace(trace, args, "export")
    if args.output:
        output_path = write_otlp_json(
            trace,
            args.output,
            service_name=args.service_name,
            include_payloads=args.include_payloads,
        )
        print(f"OpenTelemetry JSON written: {output_path}")
        return 0

    data = trace_to_otlp_json(
        trace,
        service_name=args.service_name,
        include_payloads=args.include_payloads,
    )
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return 0


# ---------------------------------------------------------------------------
# otel-push
# ---------------------------------------------------------------------------


def command_otel_push(args: argparse.Namespace) -> int:
    trace = _load(args.trace)
    trace = _prepare_sensitive_trace(trace, args, "push")
    result = push_trace_to_otlp_http(
        trace,
        endpoint=args.endpoint,
        headers=_parse_header_args(args.header),
        timeout_seconds=args.timeout,
        service_name=args.service_name,
        include_payloads=args.include_payloads,
        allow_private_endpoint=args.allow_private_endpoint,
        allow_insecure_endpoint=args.allow_insecure_endpoint,
    )
    print(f"OpenTelemetry trace pushed: {result.endpoint} ({result.status_code})")
    return 0


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def command_store_import(args: argparse.Namespace) -> int:
    paths = _collect_trace_files(args.path)
    _preflight_sensitive_files(paths, args, "store")
    store = TraceStore(args.db, redaction=args.redact)
    run_ids = store.import_files(paths)
    print(f"Imported {len(run_ids)} trace(s) into {store.db_path}")
    return 0


def command_store_list(args: argparse.Namespace) -> int:
    store = TraceStore(args.db)
    rows = store.list_traces(query=args.query)
    _print_store_rows(rows)
    return 0


def command_store_search(args: argparse.Namespace) -> int:
    store = TraceStore(args.db)
    rows = store.search(args.query)
    _print_store_rows(rows)
    return 0


def command_store_show(args: argparse.Namespace) -> int:
    store = TraceStore(args.db)
    trace = store.get_trace(args.run_id)
    if trace is None:
        raise SystemExit(f"Trace not found in store: {args.run_id}")
    print(json.dumps(trace.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 0


def _collect_trace_files(path_str: str) -> list[Path]:
    path = Path(path_str)
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.rglob("*.trace.json"))
    raise SystemExit(f"Trace path not found: {path}")


def _print_store_rows(rows) -> None:
    if not rows:
        print("No traces found in store.")
        return
    header = f"{'ID':<14} {'STATUS':<10} {'DURATION':<10} {'STEPS':<6} {'TOKENS':<8} {'COST':<12} TASK"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row.run_id:<14} {row.status:<10} {_fmt_ms(row.duration_ms):<10} "
            f"{row.step_count:<6} {row.total_tokens:<8} {_fmt_usd(row.cost_usd):<12} "
            f"{_truncate(row.task, 60)}"
        )


def _prepare_sensitive_trace(trace: Trace, args: argparse.Namespace, action: str) -> Trace:
    if getattr(args, "redact", False):
        return redact_trace(trace)
    findings = scan_trace_for_secrets(trace)
    if findings and not getattr(args, "allow_sensitive", False):
        raise SystemExit(_sensitive_block_message(len(findings), action))
    return trace


def _preflight_sensitive_files(paths: list[Path], args: argparse.Namespace, action: str) -> None:
    if getattr(args, "redact", False):
        return
    if getattr(args, "allow_sensitive", False):
        return

    total_findings = 0
    first_path: Path | None = None
    for path in paths:
        trace = _load(str(path))
        findings = scan_trace_for_secrets(trace)
        if findings:
            total_findings += len(findings)
            first_path = first_path or path
    if total_findings:
        detail = f" First sensitive file: {first_path}." if first_path else ""
        raise SystemExit(_sensitive_block_message(total_findings, action) + detail)


def _sensitive_block_message(finding_count: int, action: str) -> str:
    return (
        f"Sensitive trace findings detected ({finding_count}); refusing to {action}. "
        "Run privacy-scan for locations, use --redact to sanitize, or --allow-sensitive to continue."
    )


def _parse_header_args(values: list[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise SystemExit(f"Invalid header, expected name=value: {value}")
        name, header_value = value.split("=", 1)
        name = name.strip()
        if not name:
            raise SystemExit(f"Invalid header, expected name=value: {value}")
        headers[name] = header_value.strip()
    return headers


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-devtools", description="Inspect and compare Agent DevTools trace files")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List trace files in a directory")
    p_list.add_argument("directory", nargs="?", default="traces")
    p_list.set_defaults(func=command_list)

    # show
    p_show = subparsers.add_parser("show", help="Show a run summary and timeline")
    p_show.add_argument("trace")
    p_show.add_argument("--detail", action="store_true", dest="show_detail", help="Show step input/output")
    p_show.set_defaults(func=command_show)

    # steps
    p_steps = subparsers.add_parser("steps", help="List all steps in a trace")
    p_steps.add_argument("trace")
    p_steps.set_defaults(func=command_steps)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect a single step as JSON")
    p_inspect.add_argument("trace")
    p_inspect.add_argument("step_id")
    p_inspect.set_defaults(func=command_inspect)

    # cost
    p_cost = subparsers.add_parser("cost", help="Summarize trace cost")
    p_cost.add_argument("trace")
    p_cost.set_defaults(func=command_cost)

    # diff
    p_diff = subparsers.add_parser("diff", help="Compare two trace files")
    p_diff.add_argument("left_trace")
    p_diff.add_argument("right_trace")
    p_diff.set_defaults(func=command_diff)

    # experiment
    p_experiment = subparsers.add_parser("experiment", help="Compare two traces as an A/B experiment")
    p_experiment.add_argument("left_trace")
    p_experiment.add_argument("right_trace")
    p_experiment.set_defaults(func=command_experiment)

    # regression-check
    p_regression = subparsers.add_parser("regression-check", help="Fail CI when a candidate trace regresses against a baseline")
    p_regression.add_argument("baseline_trace")
    p_regression.add_argument("candidate_trace")
    p_regression.add_argument("--max-token-delta", type=int, help="Maximum allowed candidate token increase")
    p_regression.add_argument("--max-cost-delta-usd", type=float, help="Maximum allowed candidate cost increase in USD")
    p_regression.add_argument("--max-latency-delta-ms", type=float, help="Maximum allowed candidate latency increase in milliseconds")
    p_regression.add_argument("--max-step-count-delta", type=int, help="Maximum allowed candidate step count increase")
    p_regression.add_argument("--fail-on-output-change", action="store_true", help="Fail when run.final_output changes")
    p_regression.add_argument("--json", action="store_true", dest="json_output", help="Print a machine-readable JSON report")
    p_regression.set_defaults(func=command_regression_check)

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Run full analysis on a trace")
    p_analyze.add_argument("trace")
    p_analyze.set_defaults(func=command_analyze)

    # replay
    p_replay = subparsers.add_parser("replay", help="Create a deterministic replay trace from a recorded run")
    p_replay.add_argument("trace")
    p_replay.add_argument("--start-step", help="Step id to start replay from")
    p_replay.add_argument("--plan", help="Replay Plan JSON with start_step_id and mocked_tools")
    p_replay.add_argument("--output-dir", default="traces", help="Directory for the replay trace")
    p_replay.set_defaults(func=command_replay)

    # replay-adapter
    p_replay_adapter = subparsers.add_parser("replay-adapter", help="Execute a local callable adapter from a recorded step")
    p_replay_adapter.add_argument("trace")
    p_replay_adapter.add_argument("--start-step", required=True, help="Step id to start replay from")
    p_replay_adapter.add_argument("--callable", required=True, help="Callable import path: module:function or path/to/file.py:function")
    p_replay_adapter.add_argument("--name", default=None, help="Adapter name shown in the replay trace")
    p_replay_adapter.add_argument("--input-json", help="Override replay input with JSON instead of the selected step input")
    p_replay_adapter.add_argument("--pythonpath", action="append", help="Extra import path for module:function callables")
    p_replay_adapter.add_argument("--allow-unsafe-code", action="store_true", help="Allow execution of local Python code from --callable")
    p_replay_adapter.add_argument("--output-dir", default="traces", help="Directory for the adapter replay trace")
    p_replay_adapter.set_defaults(func=command_replay_adapter)

    # replay-compare
    p_replay_compare = subparsers.add_parser("replay-compare", help="Compare an original trace path against a replay trace")
    p_replay_compare.add_argument("source_trace")
    p_replay_compare.add_argument("replay_trace")
    p_replay_compare.set_defaults(func=command_replay_compare)

    # redact
    p_redact = subparsers.add_parser("redact", help="Write a privacy-redacted copy of a trace")
    p_redact.add_argument("trace")
    p_redact.add_argument("--output", help="Output .trace.json path")
    p_redact.set_defaults(func=command_redact)

    # privacy-scan
    p_privacy_scan = subparsers.add_parser("privacy-scan", help="Scan a trace for sensitive values without printing them")
    p_privacy_scan.add_argument("trace")
    p_privacy_scan.add_argument("--json", action="store_true", dest="json_output", help="Print finding locations as JSON")
    p_privacy_scan.set_defaults(func=command_privacy_scan)

    # otel-export
    p_otel_export = subparsers.add_parser("otel-export", help="Export a trace as OpenTelemetry OTLP JSON")
    p_otel_export.add_argument("trace")
    p_otel_export.add_argument("--output", help="Output .otlp.json path. Prints JSON to stdout when omitted")
    p_otel_export.add_argument("--service-name", default="agent-devtools", help="OpenTelemetry service.name resource attribute")
    p_otel_export.add_argument("--include-payloads", action="store_true", help="Include step input/output and tool args/result JSON attributes")
    p_otel_export.add_argument("--redact", action="store_true", help="Redact sensitive values before exporting")
    p_otel_export.add_argument("--allow-sensitive", action="store_true", help="Export even when privacy-scan finds sensitive values")
    p_otel_export.set_defaults(func=command_otel_export)

    # otel-push
    p_otel_push = subparsers.add_parser("otel-push", help="Push a trace to an OpenTelemetry Collector OTLP HTTP endpoint")
    p_otel_push.add_argument("trace")
    p_otel_push.add_argument("--endpoint", help="OTLP HTTP traces endpoint. Defaults to OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or http://localhost:4318/v1/traces")
    p_otel_push.add_argument("--header", action="append", help="Additional HTTP header in name=value form")
    p_otel_push.add_argument("--timeout", type=float, help="HTTP timeout in seconds")
    p_otel_push.add_argument("--service-name", default="agent-devtools", help="OpenTelemetry service.name resource attribute")
    p_otel_push.add_argument("--include-payloads", action="store_true", help="Include step input/output and tool args/result JSON attributes")
    p_otel_push.add_argument("--redact", action="store_true", help="Redact sensitive values before pushing")
    p_otel_push.add_argument("--allow-sensitive", action="store_true", help="Push even when privacy-scan finds sensitive values")
    p_otel_push.add_argument("--allow-private-endpoint", action="store_true", help="Allow pushing to non-loopback private/link-local endpoints")
    p_otel_push.add_argument("--allow-insecure-endpoint", action="store_true", help="Allow non-loopback http endpoints")
    p_otel_push.set_defaults(func=command_otel_push)

    # store
    p_store = subparsers.add_parser("store", help="Import and search traces in a local SQLite store")
    store_subparsers = p_store.add_subparsers(dest="store_command", required=True)

    p_store_import = store_subparsers.add_parser("import", help="Import a trace file or directory into SQLite")
    p_store_import.add_argument("path", help="Trace file or directory containing .trace.json files")
    p_store_import.add_argument("--db", default=".agent-devtools/traces.db", help="SQLite database path")
    p_store_import.add_argument("--redact", action="store_true", help="Redact sensitive values before storing")
    p_store_import.add_argument("--allow-sensitive", action="store_true", help="Store even when privacy-scan finds sensitive values")
    p_store_import.set_defaults(func=command_store_import)

    p_store_list = store_subparsers.add_parser("list", help="List traces in SQLite")
    p_store_list.add_argument("--db", default=".agent-devtools/traces.db", help="SQLite database path")
    p_store_list.add_argument("--query", help="Optional search query")
    p_store_list.set_defaults(func=command_store_list)

    p_store_search = store_subparsers.add_parser("search", help="Search traces in SQLite")
    p_store_search.add_argument("query")
    p_store_search.add_argument("--db", default=".agent-devtools/traces.db", help="SQLite database path")
    p_store_search.set_defaults(func=command_store_search)

    p_store_show = store_subparsers.add_parser("show", help="Show a stored trace as JSON")
    p_store_show.add_argument("run_id")
    p_store_show.add_argument("--db", default=".agent-devtools/traces.db", help="SQLite database path")
    p_store_show.set_defaults(func=command_store_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
