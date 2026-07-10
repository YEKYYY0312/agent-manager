# Agent DevTools Python SDK

The Python SDK records agent runs into schema-compatible `.trace.json` files.

## Install From Source

From the repository root:

```powershell
py -m pip install -e .
```

This installs both the SDK import package and the `agent-devtools` CLI entry point.

## Main APIs

- `TraceContext`
- `traced_step`
- `traced_tool`
- `traced_model`
- `TraceWriter`
- `Trace.from_file`
- `redact_trace`
- `scan_trace_for_secrets`
- `TraceStore`
- `trace_to_otlp_json`
- `push_trace_to_otlp_http`
- `agent_devtools.analysis.analyze`

## Example

```python
from agent_devtools import TraceContext, traced_tool, traced_model

@traced_tool("weather.lookup")
def lookup(city: str) -> dict:
    return {"city": city, "summary": "Warm and humid"}

@traced_model("Generate final answer", model="gpt-4.1-mini")
def answer(weather: dict) -> dict:
    return {
        "content": f"Weather: {weather['summary']}",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }

with TraceContext(task="Answer weather question") as ctx:
    result = lookup("Shanghai")
    final = answer(result)
    ctx.trace.run.complete(status="success", final_output=final["content"])
```

The trace is written to `traces/` by default when the context exits.

Enable write-time redaction when traces may contain secrets:

```powershell
$env:AGENT_DEVTOOLS_REDACT_ON_WRITE = "true"
```

## Tests

From the project root:

```powershell
py -m pytest packages\python-sdk\tests
```
