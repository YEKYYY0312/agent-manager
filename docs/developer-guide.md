# Developer Guide

This guide explains the project structure, component boundaries, and data contracts.

## Architecture

```text
Agent code
  -> Python SDK
  -> trace JSON
  -> CLI / analysis module / Web UI
```

The trace file is the boundary between every package. If a component needs new trace fields, update these files first:

- `schemas/trace.schema.json`
- `docs/trace-schema.md`
- tests that read or write trace files

## Packages

### Python SDK

Path:

```text
packages/python-sdk/agent_devtools/
```

Responsibilities:

- create runs
- record steps
- capture model calls, tool calls, planner steps, and custom steps
- preserve partial traces when errors happen
- write schema-compatible `.trace.json` files

Important modules:

- `trace.py`: dataclass models and serialization
- `context.py`: `TraceContext` lifecycle and step context managers
- `decorators.py`: `traced_step`, `traced_tool`, `traced_model`
- `writer.py`: trace persistence
- `redaction.py`: privacy scanning and redaction for sensitive trace values
- `store.py`: local SQLite trace index
- `analysis.py`: pure analysis functions
- `replay.py`: deterministic replay trace generation from recorded steps
- `adapters.py`: framework adapter contract, callable runtime adapter, LangGraph adapter, OpenAI adapter, and Anthropic adapter
- `experiment.py`: A/B trace experiment comparison

Important rules:

- `TraceContext` should auto-complete normal runs when the user did not manually call `run.complete()`.
- Tool steps must preserve `tool.args` and `tool.result`.
- Step cost is the primary source of cost detail.
- `run.cost` is a fallback or cached aggregate, not an additional amount to double-count.
- Framework-specific integrations should implement the adapter contract without adding hard dependencies to the core SDK.

### CLI

Path:

```text
packages/cli/agent_devtools_cli/
```

Responsibilities:

- load trace files
- list available runs
- show run summaries and timelines
- inspect individual steps
- summarize cost
- run full analysis
- compare two runs
- create deterministic replay traces
- execute explicit local callable adapter replay traces
- compare original source paths against replay traces
- compare two traces as an experiment
- scan/redact traces before sharing, storing, or exporting
- import/search traces in local SQLite with privacy preflight

Commands:

```powershell
py packages\cli\agent_devtools_cli\main.py list traces
py packages\cli\agent_devtools_cli\main.py show traces\<trace>.trace.json --detail
py packages\cli\agent_devtools_cli\main.py steps traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py inspect traces\<trace>.trace.json <step-id>
py packages\cli\agent_devtools_cli\main.py cost traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py analyze traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py diff traces\<left>.trace.json traces\<right>.trace.json
py packages\cli\agent_devtools_cli\main.py experiment traces\<left>.trace.json traces\<right>.trace.json
py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --start-step <step-id>
py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --plan replay-plan.json --output-dir traces
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\<trace>.trace.json --start-step <step-id> --callable path\to\agent.py:run
py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json
py packages\cli\agent_devtools_cli\main.py privacy-scan traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py redact traces\<trace>.trace.json --output traces\<trace>.safe.trace.json
py packages\cli\agent_devtools_cli\main.py store import traces --redact
py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace>.trace.json --redact --output traces\<trace>.otlp.json
```

### Runtime Adapter API

The SDK exposes a runtime adapter contract for real agent execution:

```python
from agent_devtools import AnthropicAdapter, CallableAgentAdapter, LangGraphAdapter, OpenAIAdapter, replay_with_adapter

adapter = CallableAgentAdapter(lambda payload: {"answer": payload}, name="demo-agent")
result = replay_with_adapter(source_trace, start_step_id="<step-id>", adapter=adapter)

langgraph_adapter = LangGraphAdapter(compiled_graph, name="qa-graph")
result = langgraph_adapter.run(task="Run graph", input={"messages": messages})

streaming_adapter = LangGraphAdapter(compiled_graph, name="qa-graph", trace_stream=True)
result = streaming_adapter.run(task="Run graph", input={"messages": messages})

openai_adapter = OpenAIAdapter(openai_client, model="gpt-4.1-mini")
result = openai_adapter.run(task="Ask model", input="weather in Shanghai")

anthropic_adapter = AnthropicAdapter(anthropic_client, model="claude-opus-4-8")
result = anthropic_adapter.run(task="Ask Claude", input="weather in Shanghai")
```

The CLI `replay` command is still deterministic. For real local Python execution, `replay-adapter` runs only when the caller explicitly passes `--callable`:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\<trace>.trace.json --start-step <step-id> --callable path\to\agent.py:run --output-dir traces
```

The callable import path accepts `module:function` or `path\to\file.py:function`. Use `--input-json` to override the selected step input and `--pythonpath` for extra module import paths.

The deterministic `replay` command can also read a Replay Plan JSON:

```powershell
py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --plan replay-plan.json --output-dir traces
```

When `mocked_tools` contains edited `result` values, replayed tool steps use those values and mark the step metadata `replay_mode=edited_tool_mock`.

Use `replay-compare` after either deterministic replay or callable adapter replay to compare only the source path that begins at `source_start_step_id`:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json
```

The report checks source-run linkage, replay mode, status drift, output drift, step/token/cost/latency deltas, and per-step differences.

### Web UI

Path:

```text
packages/web-ui/
```

Responsibilities:

- load trace JSON from `public/traces/`
- normalize trace data in the browser
- render Timeline, Step Inspector, Analysis, and Run Diff views
- reuse the same cost and diff semantics as the CLI

Important modules:

- `src/trace.ts`: browser data adapter
- `src/types.ts`: TypeScript trace types
- `src/workspace.ts`: trace catalog and tab contract
- `src/App.tsx`: workbench shell
- `src/Timeline.tsx`: ordered step list
- `src/StepInspector.tsx`: selected step detail
- `src/AnalysisView.tsx`: cost and latency analysis
- `src/DiffView.tsx`: run comparison view

Important rules:

- Do not reimplement `computeCostSummary`, `buildTimeline`, `diffRuns`, or `normalizeTrace` inside UI components.
- Keep Trace Picker and Timeline keyboard accessible.
- Avoid non-ASCII symbolic markers unless the project explicitly standardizes them.
- Keep Web UI data tests in `npm run test:data`.

## Trace Contract

Minimum trace shape:

```json
{
  "schema_version": "0.1.0",
  "run": {
    "id": "run-id",
    "task": "Task description",
    "status": "success",
    "started_at": "2026-07-05T00:00:00Z"
  },
  "steps": []
}
```

Important step fields:

- `id`
- `type`
- `name`
- `status`
- `started_at`
- `ended_at`
- `duration_ms`
- `input`
- `output`
- `tool.name`
- `tool.args`
- `tool.result`
- `cost`
- `error`
- `replayable`

## Adding A New Field

1. Update `schemas/trace.schema.json`.
2. Update `docs/trace-schema.md`.
3. Update Python dataclasses in `trace.py`.
4. Update TypeScript types in `src/types.ts`.
5. Update `normalizeTrace` in `src/trace.ts`.
6. Add tests in Python and Web UI if the field affects behavior.

## Adding A New Analysis

Prefer pure functions:

```text
Trace -> Report
```

Do not read files or mutate traces inside analysis functions. File loading belongs in the CLI or Web UI loader.

## Development Commands

Python tests:

```powershell
py -m pytest
```

Web UI checks:

```powershell
cd packages\web-ui
npm run test:data
npm run lint
npm run build
```

Web UI dev server:

```powershell
cd packages\web-ui
npm run dev
```
