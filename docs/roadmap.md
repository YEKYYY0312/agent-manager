# Roadmap

## Completed

### Phase 0: Product Contract

- Product scope defined.
- Trace schema documented.
- `schemas/trace.schema.json` created.
- Collaboration workflow documented.

### Phase 1: Project Skeleton

- Repository layout created.
- `docs/`, `schemas/`, `examples/`, `packages/`, and `traces/` directories created.
- Python SDK, CLI, and Web UI package boundaries established.

### Phase 2: Core Tracing Engine

- Python trace dataclasses implemented.
- `TraceContext` lifecycle implemented.
- Step context managers implemented.
- `traced_step`, `traced_tool`, and `traced_model` decorators implemented.
- Trace writer implemented.
- Simple agent demo implemented.
- SDK tests added.

### Phase 3: CLI Tool

- `list` command implemented.
- `show` command implemented.
- `steps` command implemented.
- `inspect` command implemented.
- `cost` command implemented.
- `diff` command implemented.
- CLI tests added.

### Phase 4: Analysis Module

- Cost analysis implemented.
- Latency analysis implemented.
- Failure analysis implemented.
- Loop detection implemented.
- Retry detection implemented.
- `analyze` CLI command added.
- Analysis tests added.

### Phase 5: Web UI

- React/Vite workbench created.
- Trace Picker implemented.
- Timeline view implemented.
- Step Inspector implemented.
- Analysis view implemented.
- Run Diff view implemented.
- Web UI data adapter and workspace tests added.
- Accessibility and UI polish pass completed.

### Phase 6: Documentation And Test Plan

- Root README updated.
- User guide added.
- Developer guide added.
- Test plan added.
- Roadmap updated.

### Phase 7: Usability And Real-World Trial

- Web UI Chinese labels and helper text added.
- Browser local `.trace.json` import added.
- Drag-and-drop trace import added.
- Imported trace state fixed for Trace list and Run Diff.
- Demo traces generated and verified end-to-end through SDK, CLI, analysis, and Web UI data layer.
- Chinese quick start guide added.

### Phase 8: Replay Workbench MVP

- Replay tab added to the Web UI.
- Replayable checkpoints listed from trace step metadata.
- Replay plan generation added in the browser data layer.
- Tool output mocks extracted from downstream tool steps.
- Replay Plan JSON can be copied from the UI.
- Replay CLI command can be copied from the UI.

### Phase 9: Deterministic Replay Execution

- Python replay runner added.
- CLI `replay` command added.
- Replay traces are generated from recorded steps.
- Tool calls reuse recorded tool results.
- Model calls reuse recorded outputs and cost data.
- Replay traces are labeled with source run and start step metadata.

### Phase 10: Experiment Comparison

- Python experiment comparison module added.
- CLI `experiment` command added.
- Reports success, cost, latency, token, step, and output deltas.
- Uses transparent recommendation rules: success first, then cost and latency.
- Web UI Experiment tab added.
- Browser-side experiment comparison uses the same semantics as the CLI.
- Recommendation, deltas, and output-change status are shown in the browser.

### Phase 11: Local Product Hardening

- Privacy redaction module added.
- `redact` CLI command added for sanitized trace copies.
- Browser-imported traces persist across page refresh through local storage.
- Local SQLite `TraceStore` added.
- `store import/list/search/show` CLI commands added.
- Python package metadata and `agent-devtools` console script added.
- GitHub Actions CI workflow added.
- Large trace analysis regression test added.
- Product positioning documented as local-first Agent DevTools.

### Phase 12: Adapter Replay Foundation

- `AgentAdapter` protocol added.
- `AdapterRunResult` added.
- `CallableAgentAdapter` added for custom Python agents and framework experiments.
- Adapter replay API added through `replay_with_adapter`.
- Adapter replay runs are labeled with source run and start step metadata.
- Failed adapter executions are captured as failed traces instead of being lost.
- Framework adapter contract documented.

### Phase 12.2: LangGraph Adapter MVP

- `LangGraphAdapter` added.
- Compiled LangGraph-like graph objects are executed through `graph.invoke(...)`.
- LangGraph adapter runs are captured as trace files without adding LangGraph as a core dependency.
- LangGraph adapter success, failure, config forwarding, and invalid graph tests added.

### Phase 12.3: LangGraph Node Streaming

- `LangGraphAdapter(trace_stream=True)` added.
- Streaming mode calls `graph.stream(..., stream_mode="updates", version="v2")`.
- Each LangGraph node update is recorded as a separate trace step.
- Stream step metadata includes node name, namespace, stream mode, chunk type, and stream index.
- Streaming graph output is collected by node name for run final output.

### Phase 12.4: OpenAI SDK Adapter

- `OpenAIAdapter` added.
- Responses API calls are executed through `client.responses.create(...)`.
- Chat Completions calls are executed through `client.chat.completions.create(...)`.
- OpenAI response usage is mapped into step `Cost`.
- Responses output uses `response.output_text` when available.
- Chat Completions output uses the first choice message content when available.
- OpenAI adapter errors are captured as failed traces.

### Phase 12.5: Anthropic SDK Adapter

- `AnthropicAdapter` added.
- Claude Messages API calls are executed through `client.messages.create(...)`.
- String input is normalized to a user message.
- Existing `{"messages": [...]}` input is preserved.
- Anthropic response usage is mapped into step `Cost`.
- Text output is extracted from `response.content` text blocks.
- Anthropic adapter errors are captured as failed traces.

### Phase 12.6: Adapter CLI Runtime Replay

- CLI `replay-adapter` command added.
- Runtime replay remains explicit: local Python code runs only when `--callable` and `--allow-unsafe-code` are provided.
- Callable import paths support `module:function` and `path/to/file.py:function`.
- `--input-json` can override the selected step input.
- Adapter replay traces are written to `--output-dir` and preserve source run metadata.
- Windows absolute callable file paths are supported.

### Phase 12.7: Original Vs Replay Comparison

- SDK `compare_replay` report added.
- CLI `replay-compare` command added.
- Reports source-run match, source start step, replay mode, status drift, output drift, token/cost/latency/step deltas, and per-step differences.
- Compares only the original source path that begins at `source_start_step_id`, not the entire source run.

### Phase 12.8: Editable Tool Mock Replay

- SDK `create_replay_trace(..., tool_mocks=...)` added.
- CLI `replay --plan replay-plan.json` added.
- Replay Plan `mocked_tools[].result` values are applied to replayed tool steps.
- Edited mock steps are labeled with `replay_mode=edited_tool_mock`.
- Web UI Replay Workbench can edit tool mock result JSON and generate a plan-backed CLI command.

### Phase 12.9: Web Replay Compare

- Browser-side `compareReplay` added with the same source-path semantics as CLI `replay-compare`.
- Web UI `Replay Compare` tab added.
- Replay Compare shows source-run match, replay mode, status/output drift, step/token/cost/latency deltas, and per-step changes.
- Imported replay traces can be selected and compared against the active source trace.

### Phase 12.10: OpenAI Responses Output Expansion

- `OpenAIAdapter(expand_output_items=True)` added.
- Responses `output` items are recorded as child steps under the main `responses.create` step.
- `message` items are recorded as `model_call` child steps.
- `file_search_call` and `web_search_call` items are recorded as `retrieval` child steps.
- `function_call` and other `*_call` items are recorded as `tool_call` child steps.
- Function-call arguments are parsed from JSON strings when possible.

### Phase 12.11: Anthropic Content Block Expansion

- `AnthropicAdapter(expand_content_blocks=True)` added.
- Claude Messages `content` blocks are recorded as child steps under the main `messages.create` step.
- `text` blocks are recorded as `model_call` child steps.
- `thinking` blocks are recorded as `planner` child steps.
- `tool_use` and `server_tool_use` blocks are recorded as `tool_call` child steps.
- Tool-use child steps populate `tool.args` from Claude's structured tool input.

### Phase 12.12: OpenTelemetry JSON Export

- Dependency-free OTLP JSON exporter added.
- SDK `trace_to_otlp_json(...)` converts Agent DevTools traces into `resourceSpans -> scopeSpans -> spans`.
- SDK `write_otlp_json(...)` writes exported traces to `.otlp.json` files.
- CLI `otel-export` command added.
- Exported data includes a root run span plus one span per trace step.
- Parent/child step relationships are mapped into span parent IDs.
- Model, token, cost, tool, error, status, timing, and replayable metadata are mapped into span attributes.
- Step payloads are omitted by default and can be enabled explicitly with `--include-payloads`.

### Phase 12.13: Privacy Hardening

- SDK `scan_trace_for_secrets(...)` added for location-only sensitive data findings.
- CLI `privacy-scan` command added.
- `TraceWriter` and `TraceStore` support `AGENT_DEVTOOLS_REDACT_ON_WRITE=true` for automatic redaction on write.
- `store import` runs privacy preflight and supports `--redact` or `--allow-sensitive`.
- `otel-export` runs privacy preflight and supports `--redact` or `--allow-sensitive`.
- Sensitive finding reports never include raw secret values.
- `.gitignore` protects local trace, OTLP, SQLite, replay plan, and build artifacts from accidental commits.

### Phase 12.14: OpenTelemetry Collector HTTP Push

- SDK `push_trace_to_otlp_http(...)` added.
- SDK `OtlpHttpExportResult` and `OtlpHttpExportError` added.
- CLI `otel-push` command added for OTLP HTTP JSON export to Collector endpoints.
- Default endpoint is `http://localhost:4318/v1/traces`.
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is honored when set.
- `OTEL_EXPORTER_OTLP_ENDPOINT` is honored by appending `/v1/traces`.
- `OTEL_EXPORTER_OTLP_HEADERS` and `OTEL_EXPORTER_OTLP_TRACES_HEADERS` are honored.
- CLI `--header name=value`, `--timeout`, `--service-name`, `--include-payloads`, `--redact`, and `--allow-sensitive` options added.
- `otel-push` uses the same privacy preflight behavior as `otel-export`.

### Phase 12.15: CI Regression Check

- SDK `check_regression(...)` added for baseline-vs-candidate trace gates.
- SDK `RegressionThresholds`, `RegressionCheck`, and `RegressionReport` added.
- CLI `regression-check` command added with pass/fail exit codes for CI.
- Gates cover status regression, failed-step increases, token, cost, latency, step-count thresholds, and optional output-change failure.
- `--json` output added for CI artifacts and automated reporting.

### Phase 12.16: Anthropic Tool-Use Loop

- `AnthropicAdapter(tools=...)` added for local Claude tool-use loop execution.
- Claude `tool_use` blocks are matched to trusted local Python callables by name.
- Tool executions are recorded as `tool_call` steps with args, result, `anthropic_tool_use_id`, round, and index metadata.
- Tool results are sent back to Claude as Anthropic-compatible `tool_result` messages until a final answer is returned.
- Unknown tools and tool exceptions are recorded as error tool steps and returned to Claude as error `tool_result` messages.
- `max_tool_rounds` prevents accidental infinite tool loops.

## Next

### Replay Execution Hardening

- Compare edited mock replay outputs across multiple replay attempts.

### Framework Adapters

- Anthropic streaming event expansion.
- OpenAI Agents SDK tracing and streaming event expansion.
- Claude Code/Codex trace import or execution bridge.
- Broader CLI runtime replay for framework-specific adapters.
- Generic Python decorator API improvements.

### Product Hardening

- Larger trace performance testing.
- Schema version migration strategy.
- More robust diff alignment for branching agent graphs.
