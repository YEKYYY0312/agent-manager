# Agent DevTools CLI

Zero-dependency CLI for reading, inspecting, analyzing, and comparing Agent DevTools trace files.

## Install From Source

From the repository root:

```powershell
py -m pip install -e .
agent-devtools --help
```

On Windows, if `agent-devtools` is not found, add Python's `Scripts` directory
to `PATH` or run the generated `agent-devtools.exe` by full path.

You can still run the source entry point directly:

```powershell
py packages\cli\agent_devtools_cli\main.py --help
```

## Commands

After editable install:

```powershell
agent-devtools list traces
agent-devtools show traces\<trace>.trace.json --detail
agent-devtools steps traces\<trace>.trace.json
agent-devtools inspect traces\<trace>.trace.json <step-id>
agent-devtools cost traces\<trace>.trace.json
agent-devtools analyze traces\<trace>.trace.json
agent-devtools diff traces\<left>.trace.json traces\<right>.trace.json
agent-devtools experiment traces\<left>.trace.json traces\<right>.trace.json
agent-devtools regression-check traces\<baseline>.trace.json traces\<candidate>.trace.json --max-token-delta 100 --max-latency-delta-ms 500 --json
agent-devtools replay traces\<trace>.trace.json --start-step <step-id>
agent-devtools replay traces\<trace>.trace.json --plan replay-plan.json --output-dir traces
agent-devtools replay-adapter traces\<trace>.trace.json --start-step <step-id> --callable path\to\agent.py:run --allow-unsafe-code
agent-devtools replay-compare traces\<source>.trace.json traces\<replay>.trace.json
agent-devtools privacy-scan traces\<trace>.trace.json
agent-devtools redact traces\<trace>.trace.json --output traces\<trace>.safe.trace.json
agent-devtools store import traces --redact
agent-devtools store list
agent-devtools store search "weather"
agent-devtools otel-export traces\<trace>.trace.json --redact --output traces\<trace>.otlp.json
agent-devtools otel-push traces\<trace>.trace.json --redact --endpoint http://localhost:4318/v1/traces
```

From source without install:

```powershell
py packages\cli\agent_devtools_cli\main.py list traces
py packages\cli\agent_devtools_cli\main.py show traces\<trace>.trace.json --detail
py packages\cli\agent_devtools_cli\main.py steps traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py inspect traces\<trace>.trace.json <step-id>
py packages\cli\agent_devtools_cli\main.py cost traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py analyze traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py diff traces\<left>.trace.json traces\<right>.trace.json
py packages\cli\agent_devtools_cli\main.py experiment traces\<left>.trace.json traces\<right>.trace.json
py packages\cli\agent_devtools_cli\main.py regression-check traces\<baseline>.trace.json traces\<candidate>.trace.json --max-token-delta 100 --max-latency-delta-ms 500 --json
py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --start-step <step-id>
py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --plan replay-plan.json --output-dir traces
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\<trace>.trace.json --start-step <step-id> --callable path\to\agent.py:run --allow-unsafe-code
py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json
py packages\cli\agent_devtools_cli\main.py privacy-scan traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py redact traces\<trace>.trace.json --output traces\<trace>.safe.trace.json
py packages\cli\agent_devtools_cli\main.py store import traces --redact
py packages\cli\agent_devtools_cli\main.py store list
py packages\cli\agent_devtools_cli\main.py store search "weather"
py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace>.trace.json --redact --output traces\<trace>.otlp.json
py packages\cli\agent_devtools_cli\main.py otel-push traces\<trace>.trace.json --redact --endpoint http://localhost:4318/v1/traces
```

`store import` scans only the top-level directory by default. Use `--recursive` when you intentionally want nested trace discovery, and `--max-files` to lower the default import cap.

`replay-adapter` executes local Python code and therefore requires `--allow-unsafe-code`. `otel-push` blocks non-loopback HTTP and private/link-local endpoints by default; use the explicit override flags only for trusted collectors.

## Generate Demo Traces

```powershell
py examples\simple-agent\demo.py
```

## Tests

```powershell
py -m pytest packages\cli\tests
```

## Build Package

From the repository root:

```powershell
py -m pip install build wheel
py -m build
```

See `docs/release.md` for the full release checklist.
