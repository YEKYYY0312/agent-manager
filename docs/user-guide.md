# User Guide

This guide explains how to generate, inspect, analyze, import, and compare Agent DevTools traces.

## 1. Generate Demo Traces

From the project root:

```powershell
py examples\simple-agent\demo.py
```

The demo writes `.trace.json` files into `traces/`. Each filename uses the generated run id, so names are not fixed.

List generated traces:

```powershell
py packages\cli\agent_devtools_cli\main.py list traces
```

## 2. Inspect A Trace With The CLI

Show a run summary and timeline:

```powershell
py packages\cli\agent_devtools_cli\main.py show traces\<trace-file>.trace.json
```

Show detailed step input and output:

```powershell
py packages\cli\agent_devtools_cli\main.py show traces\<trace-file>.trace.json --detail
```

List all steps:

```powershell
py packages\cli\agent_devtools_cli\main.py steps traces\<trace-file>.trace.json
```

Inspect one step as JSON:

```powershell
py packages\cli\agent_devtools_cli\main.py inspect traces\<trace-file>.trace.json <step-id>
```

## 3. Analyze Cost, Latency, And Failures

Show cost summary:

```powershell
py packages\cli\agent_devtools_cli\main.py cost traces\<trace-file>.trace.json
```

Run the full analyzer:

```powershell
py packages\cli\agent_devtools_cli\main.py analyze traces\<trace-file>.trace.json
```

The analyzer reports:

- total token and cost usage
- cost by model and step type
- total and average latency
- slowest steps
- failed or timeout steps
- simple loop and retry patterns

## 4. Compare Two Runs

Use `diff` to compare two trace files:

```powershell
py packages\cli\agent_devtools_cli\main.py diff traces\<left>.trace.json traces\<right>.trace.json
```

This is useful for comparing a successful run against a failed run, or comparing two prompt/model variants.

## 5. Compare Two Runs As An Experiment

Use `experiment` to compare two traces with a simple A/B report:

```powershell
py packages\cli\agent_devtools_cli\main.py experiment traces\<left>.trace.json traces\<right>.trace.json
```

The report shows winners by success status, cost, and latency. The recommendation rule is intentionally simple: success wins first; if success is tied, lower cost and lower latency decide.

## 6. Gate CI Regressions

Use `regression-check` when you have a known baseline trace and want CI to fail if a candidate trace gets worse:

```powershell
py packages\cli\agent_devtools_cli\main.py regression-check traces\<baseline>.trace.json traces\<candidate>.trace.json --max-token-delta 100 --max-cost-delta-usd 0.001 --max-latency-delta-ms 500 --max-step-count-delta 2
```

The command exits with `0` when all checks pass and `1` when any gate fails. It always checks status regression and failed-step increases. Threshold flags gate positive increases in tokens, cost, latency, and step count. Add `--fail-on-output-change` when changed `run.final_output` should fail the gate.

For CI artifacts, print a machine-readable report:

```powershell
py packages\cli\agent_devtools_cli\main.py regression-check traces\<baseline>.trace.json traces\<candidate>.trace.json --max-token-delta 100 --json
```

## 7. Create A Replay Trace

Use `replay` to create a deterministic replay trace from a selected step:

```powershell
py packages\cli\agent_devtools_cli\main.py replay traces\<trace-file>.trace.json --start-step <step-id>
```

The replay runner does not call external tools or models. Tool steps reuse recorded `tool.result` values, and model steps reuse recorded output and cost data.

Use a Replay Plan JSON when you want edited tool mocks to affect deterministic replay:

```powershell
py packages\cli\agent_devtools_cli\main.py replay traces\<trace-file>.trace.json --plan replay-plan.json --output-dir traces
```

The plan's `mocked_tools[].result` values replace the corresponding replayed tool step outputs.

For real execution replay from local Python code, use `replay-adapter`:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\<trace-file>.trace.json --start-step <step-id> --callable path\to\agent.py:run --allow-unsafe-code --output-dir traces
```

The `--callable` value can be `module:function` or `path\to\file.py:function`. This command executes local Python code, so it refuses to run unless you add `--allow-unsafe-code`. The callable runs in a child Python process, keeping temporary import paths out of the main CLI process, but it is not a sandbox. Use it only with code you trust. Add `--input-json '{"question":"override"}'` when you want to override the recorded step input.

Compare the replay trace with the original trace:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json
```

The comparison uses the replay trace labels to find the original start step, then reports whether status, final output, token usage, cost, latency, step count, or individual steps changed.

You can also call the SDK adapter API directly:

```python
from agent_devtools import CallableAgentAdapter, Trace, replay_with_adapter

source = Trace.from_file("traces/source.trace.json")
adapter = CallableAgentAdapter(lambda payload: {"answer": payload}, name="demo-agent")
result = replay_with_adapter(source, start_step_id="<step-id>", adapter=adapter)
```

This creates a new trace by calling the adapter instead of copying recorded outputs.

## 8. Open The Web UI

From the Web UI package:

```powershell
cd packages\web-ui
npm run dev
```

Open the local URL printed by Vite. It is usually:

```text
http://127.0.0.1:5173/
```

The Web UI includes:

- Trace list with sample traces and local file import
- Timeline
- Step Inspector
- Analysis view for cost and latency
- Run Diff view
- Replay Workbench for replay plan generation
- Replay Compare view for imported replay traces
- Experiment view for browser-side A/B comparison
- Chinese user-facing labels and helper text

The shipped sample traces live in:

```text
packages/web-ui/public/traces/
```

## 9. Import Local Trace Files In The Browser

In the Web UI, use the Trace list import button to select a local `.trace.json` file. You can also drag a `.trace.json` file onto the Trace list area.

Imported trace labels are saved in the current browser's localStorage as lightweight metadata. Full imported trace contents are encrypted with browser Web Crypto AES-GCM before being saved in IndexedDB, so the same browser can reopen them after refresh without keeping plaintext trace JSON in localStorage. Clearing site data or switching browsers removes the import history and encrypted content.

Invalid JSON or invalid trace-shaped files show an error banner instead of crashing the page.

## 10. What To Look For In A Trace

Start with these questions:

- Did the run finish with `success`, `error`, `timeout`, or `cancelled`?
- Which step failed first?
- Which model call used the most tokens?
- Which tool call was slowest?
- Did a tool call preserve both `tool.args` and `tool.result`?
- Do two runs diverge at a planner, tool, or model step?

## 11. Replay Workbench

Open the Replay tab to select a replayable step. The workbench shows the downstream steps that would be replayed, extracts tool mocks from recorded tool results, lets you edit mock result JSON, produces a portable Replay Plan JSON, and generates a copyable CLI replay command.

The Web UI generates the plan. The CLI `replay` command can generate a deterministic replay trace from the same kind of selected step.

## 12. Replay Compare View

Open the Replay Compare tab after importing a replay trace. Keep the original source trace selected in the Trace list, choose the replay trace in the tab, and click Compare. The view reports source match, replay mode, status drift, output drift, token/cost/latency/step deltas, and per-step changes.

## 13. Experiment View

Open the Experiment tab to compare the active trace as A against another trace as B. The browser uses the same transparent rules as the CLI: success status wins first; if success is tied, lower cost and lower latency decide. The view also shows token, cost, latency, step-count, and output-change deltas.

## 14. Privacy Scan, Redaction, And Local Store

Use `privacy-scan` to check a trace before sharing, storing, or exporting it:

```powershell
py packages\cli\agent_devtools_cli\main.py privacy-scan traces\<trace>.trace.json
```

The scan reports locations and finding types only. It does not print raw secret values.

Use `redact` to write a sanitized trace copy before sharing:

```powershell
py packages\cli\agent_devtools_cli\main.py redact traces\<trace>.trace.json --output traces\<trace>.safe.trace.json
```

Use `store` to index traces in local SQLite:

```powershell
py packages\cli\agent_devtools_cli\main.py store import traces --redact
py packages\cli\agent_devtools_cli\main.py store list
py packages\cli\agent_devtools_cli\main.py store search "weather"
py packages\cli\agent_devtools_cli\main.py store show <run-id>
```

Use PostgreSQL for shared or production storage by installing the optional extra and passing the DSN explicitly:

```powershell
python -m pip install -e ".[postgres]"
$env:AGENT_DEVTOOLS_DATABASE_URL = "postgresql://agent:change-me@db.example:5432/agent_devtools"
py packages\cli\agent_devtools_cli\main.py store import traces --redact --database-url $env:AGENT_DEVTOOLS_DATABASE_URL
py packages\cli\agent_devtools_cli\main.py store list --database-url $env:AGENT_DEVTOOLS_DATABASE_URL
```

Keep real credentials out of `.env` files committed to the repository. `.env.example` is only a placeholder template.

`store import` stops when sensitive values are found unless you pass `--redact` or `--allow-sensitive`.

Set `AGENT_DEVTOOLS_REDACT_ON_WRITE=true` when you want SDK `TraceWriter` and `TraceStore` writes to redact automatically:

```powershell
$env:AGENT_DEVTOOLS_REDACT_ON_WRITE = "true"
```

## 15. Export Or Push OpenTelemetry Data

Use `otel-export` to convert a local `.trace.json` file into OTLP JSON:

```powershell
py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace>.trace.json --redact --output traces\<trace>.otlp.json
```

Omit `--output` to print the JSON to stdout:

```powershell
py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace>.trace.json --redact
```

The exporter creates one root run span and one child span per trace step. It maps parent step IDs into span parent IDs, and includes timing, status, model, token, cost, tool, error, and replay metadata as span attributes.

Use `otel-push` to send the same OTLP JSON payload to an OpenTelemetry Collector:

```powershell
py packages\cli\agent_devtools_cli\main.py otel-push traces\<trace>.trace.json --redact --endpoint http://localhost:4318/v1/traces
```

If `--endpoint` is omitted, the SDK uses `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, then `OTEL_EXPORTER_OTLP_ENDPOINT` with `/v1/traces` appended, then `http://localhost:4318/v1/traces`.

Endpoint safety defaults: HTTPS endpoints are allowed with normal certificate verification, and loopback HTTP collectors such as `http://localhost:4318/v1/traces` are allowed for local development. Non-loopback HTTP requires `--allow-insecure-endpoint`. Private, link-local, multicast, reserved, or unspecified network targets require `--allow-private-endpoint`.

Add Collector headers with either environment variables or CLI flags:

```powershell
$env:OTEL_EXPORTER_OTLP_HEADERS = "authorization=Bearer local-token"
py packages\cli\agent_devtools_cli\main.py otel-push traces\<trace>.trace.json --redact --header x-team=dev
```

`otel-export` and `otel-push` stop when sensitive values are found unless you pass `--redact` or `--allow-sensitive`. By default, prompt/input/output/tool payloads are omitted. Add `--include-payloads` only when you explicitly want those JSON payloads in the exported span attributes.

## 16. Known MVP Limits

- CLI replay is deterministic and uses recorded outputs, with optional edited tool mocks through `--plan`.
- CLI `replay-adapter` and SDK adapter replay can execute a real Python callable.
- CLI `replay-compare` can compare a replay trace against the source path it came from.
- SDK `LangGraphAdapter` supports graph-level `invoke` and node-level streaming updates.
- SDK `OpenAIAdapter` supports Responses API and Chat Completions calls, with opt-in Responses output item expansion.
- SDK `AnthropicAdapter` supports Claude Messages API calls, with opt-in content block expansion.
- OpenTelemetry OTLP JSON file export and direct Collector HTTP push exist.
- Claude Code and Codex adapters are still planned.
- Hosted storage, user accounts, and team permissions are out of scope for this MVP.
