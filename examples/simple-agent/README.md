# Simple Agent Example

A demo agent that generates repeatable traces for Agent DevTools testing.

## Scenarios

| Scenario | File | Status | What happens |
|---|---|---|---|
| Success | `*-success.trace.json` | success | planner → tool → model → answer |
| Failure | `*-failure.trace.json` | error | planner → tool timeout |
| Decorator-only | `*-decorator-only.trace.json` | success | Same pipeline, all decorators |

## Run

```powershell
cd agent管理器
py examples/simple-agent/demo.py
```

## Inspect

```powershell
py -m agent_devtools_cli.main list traces/
py -m agent_devtools_cli.main show traces/simple-agent-success.trace.json
py -m agent_devtools_cli.main cost traces/simple-agent-success.trace.json
py -m agent_devtools_cli.main diff traces/simple-agent-success.trace.json traces/simple-agent-failure.trace.json
```

## What the demo covers

- Context manager API (`TraceContext`)
- Step decorators (`@traced_step`, `@traced_model`, `@traced_tool`)
- Mixed usage (decorators + context managers in one run)
- Success trace with planner, tool, and model steps
- Failure trace with timeout error and partial steps
- Automatic cost extraction from model response dicts
