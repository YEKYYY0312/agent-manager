# Agent DevTools CLI

Zero-dependency CLI for reading, inspecting, analyzing, and comparing Agent DevTools trace files.

## Commands

Run from the project root:

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
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\<trace>.trace.json --start-step <step-id> --callable path\to\agent.py:run
py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json
py packages\cli\agent_devtools_cli\main.py privacy-scan traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py redact traces\<trace>.trace.json --output traces\<trace>.safe.trace.json
py packages\cli\agent_devtools_cli\main.py store import traces --redact
py packages\cli\agent_devtools_cli\main.py store list
py packages\cli\agent_devtools_cli\main.py store search "weather"
py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace>.trace.json --redact --output traces\<trace>.otlp.json
py packages\cli\agent_devtools_cli\main.py otel-push traces\<trace>.trace.json --redact --endpoint http://localhost:4318/v1/traces
```

## Generate Demo Traces

```powershell
py examples\simple-agent\demo.py
```

## Tests

```powershell
py -m pytest packages\cli\tests
```
