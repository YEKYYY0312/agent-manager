# Technical Design

## Architecture

Agent DevTools starts as a local-first monorepo with three independent surfaces:

1. Python SDK writes trace files.
2. CLI reads and analyzes trace files.
3. Web UI renders the same trace files later.

The trace file is the boundary between components. This keeps early work decoupled and lets each contributor move independently.

## Data Flow

```text
Agent code -> Python SDK -> trace.json -> CLI / Analysis / Web UI
```

## Python SDK Responsibilities

- Start and end runs.
- Record steps and events.
- Capture timing, status, cost, model usage, tool args, tool results, and errors.
- Write schema-compatible JSON files.

## CLI Responsibilities

- List trace files.
- Show run summaries.
- Inspect a specific step.
- Summarize cost and latency.
- Compare two runs.

## Web UI Responsibilities

- Timeline view.
- Step inspector.
- Cost and latency panels.
- Run diff view.

## Storage

MVP stores traces as local JSON files under `traces/`. The optional local SQLite store indexes those same trace files for search and long-running local use. Hosted storage can be added later without changing the trace contract.

## Error Handling

SDK and CLI should preserve partial traces. A failed run is still valuable if it records enough context to debug the failure.

## Testing Strategy

- Schema validation tests for sample traces.
- CLI snapshot-style output tests.
- SDK tests for nested steps, failed steps, and partial writes.
- Demo traces for success, failure, retry, and tool-heavy runs.
