# Test Plan

This test plan defines the minimum checks before considering a phase complete.

## Test Matrix

| Area | Command | Purpose |
|---|---|---|
| Python SDK and CLI | `py -m pytest` | Runs SDK, analysis, and CLI tests. |
| Demo trace generation | `py examples\simple-agent\demo.py` | Verifies the SDK can create real traces. |
| CLI list | `py packages\cli\agent_devtools_cli\main.py list traces` | Verifies generated traces are discoverable. |
| CLI show | `py packages\cli\agent_devtools_cli\main.py show traces\<trace>.trace.json --detail` | Verifies timeline and step details are readable. |
| CLI cost | `py packages\cli\agent_devtools_cli\main.py cost traces\<trace>.trace.json` | Verifies cost summary uses trace cost data. |
| CLI analyze | `py packages\cli\agent_devtools_cli\main.py analyze traces\<trace>.trace.json` | Verifies full analysis report works. |
| CLI diff | `py packages\cli\agent_devtools_cli\main.py diff traces\<left>.trace.json traces\<right>.trace.json` | Verifies run comparison works. |
| CLI experiment | `py packages\cli\agent_devtools_cli\main.py experiment traces\<left>.trace.json traces\<right>.trace.json` | Verifies A/B experiment comparison works. |
| CLI regression check | `py packages\cli\agent_devtools_cli\main.py regression-check traces\<baseline>.trace.json traces\<candidate>.trace.json --max-token-delta 100` | Verifies CI gates can fail on status, failed-step, token, cost, latency, step-count, or output regressions. |
| CLI replay | `py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --start-step <step-id>` | Verifies deterministic replay trace generation works. |
| CLI replay plan | `py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --plan replay-plan.json --output-dir traces` | Verifies deterministic replay applies edited tool mocks from Replay Plan JSON. |
| CLI adapter replay | `py -m pytest packages\cli\tests\test_cli.py` | Verifies explicit local callable replay through `replay-adapter`, including unsafe-code opt-in, child-process execution, file callables, temporary pythonpath, and input override. |
| CLI replay comparison | `py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json` | Verifies original-vs-replay status, output, step, token, cost, and latency comparison. |
| SDK adapter replay | `py -m pytest packages\python-sdk\tests\test_adapters.py packages\python-sdk\tests\test_replay.py` | Verifies callable adapter execution, LangGraph invoke/stream execution, OpenAI Responses/Chat execution, OpenAI Responses output item expansion, Anthropic Messages execution, Anthropic content block expansion, and adapter replay metadata. |
| CLI privacy scan | `py packages\cli\agent_devtools_cli\main.py privacy-scan traces\<trace>.trace.json` | Verifies sensitive values are reported by location without printing raw secrets. |
| CLI redact | `py packages\cli\agent_devtools_cli\main.py redact traces\<trace>.trace.json --output traces\<trace>.safe.trace.json` | Verifies sensitive fields can be removed before sharing. |
| CLI store | `py packages\cli\agent_devtools_cli\main.py store import traces --redact` | Verifies traces can be indexed in local SQLite after privacy preflight. |
| CLI OTLP JSON export | `py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace>.trace.json --redact --output traces\<trace>.otlp.json` | Verifies traces can be exported as OpenTelemetry-compatible OTLP JSON after privacy preflight. |
| CLI OTLP HTTP push | `py packages\cli\agent_devtools_cli\main.py otel-push traces\<trace>.trace.json --redact --endpoint http://localhost:4318/v1/traces` | Verifies traces can be pushed to an OTLP HTTP endpoint after privacy preflight. |
| Web UI data | `npm run test:data` | Verifies browser data adapter and workspace contract. |
| Web UI lint | `npm run lint` | Verifies static UI checks. |
| Web UI build | `npm run build` | Verifies production build. |
| Web UI import smoke | `node --experimental-strip-types -e "...normalizeTrace(...)"` | Verifies generated trace files parse through the browser data adapter. |

## Full Local Verification

From the project root:

```powershell
py -m pytest
py examples\simple-agent\demo.py
py packages\cli\agent_devtools_cli\main.py list traces
```

Pick a generated trace file, then run:

```powershell
py packages\cli\agent_devtools_cli\main.py show traces\<trace>.trace.json --detail
py packages\cli\agent_devtools_cli\main.py cost traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py analyze traces\<trace>.trace.json
```

Pick two generated traces, then run:

```powershell
py packages\cli\agent_devtools_cli\main.py diff traces\<left>.trace.json traces\<right>.trace.json
py packages\cli\agent_devtools_cli\main.py experiment traces\<left>.trace.json traces\<right>.trace.json
py packages\cli\agent_devtools_cli\main.py regression-check traces\<left>.trace.json traces\<right>.trace.json --max-token-delta 100 --json
```

Pick one generated trace and a step id from `steps`, then run:

```powershell
py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --start-step <step-id>
```

To verify edited tool mocks, use a Replay Plan JSON:

```powershell
py packages\cli\agent_devtools_cli\main.py replay traces\<trace>.trace.json --plan replay-plan.json --output-dir traces
```

To verify real local callable replay, run the CLI test coverage or execute:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-adapter traces\<trace>.trace.json --start-step <step-id> --callable path\to\agent.py:run --allow-unsafe-code --output-dir traces
```

Then compare the source trace and replay trace:

```powershell
py packages\cli\agent_devtools_cli\main.py replay-compare traces\<source>.trace.json traces\<replay>.trace.json
```

Check redaction and local SQLite storage:

```powershell
py packages\cli\agent_devtools_cli\main.py privacy-scan traces\<trace>.trace.json
py packages\cli\agent_devtools_cli\main.py redact traces\<trace>.trace.json --output traces\<trace>.safe.trace.json
py packages\cli\agent_devtools_cli\main.py store import traces --redact
py packages\cli\agent_devtools_cli\main.py store list
```

Export one trace as OTLP JSON:

```powershell
py packages\cli\agent_devtools_cli\main.py otel-export traces\<trace>.trace.json --redact --output traces\<trace>.otlp.json
```

Push one trace to a local OpenTelemetry Collector:

```powershell
py packages\cli\agent_devtools_cli\main.py otel-push traces\<trace>.trace.json --redact --endpoint http://localhost:4318/v1/traces
```

From the Web UI package:

```powershell
cd packages\web-ui
npm run test:data
npm run lint
npm run build
```

## Manual Web UI Checks

Start the Web UI:

```powershell
cd packages\web-ui
npm run dev
```

Check these workflows:

1. Trace list switches between `sample-success` and `sample-failure`.
2. Import button accepts a local `.trace.json` file generated in `traces/`.
3. Drag-and-drop import accepts a local `.trace.json` file.
4. Imported traces appear in the Trace list and remain selectable after refresh in the same browser.
5. Timeline shows all steps in order.
6. Step Inspector shows model input/output, tool args/result, cost, and error fields.
7. Analysis view shows cost, latency, slowest steps, and failures.
8. Run Diff compares sample traces and imported traces.
9. Replay tab lists replayable checkpoints, edits tool mock result JSON, generates Replay Plan JSON, and shows a copyable replay CLI command.
10. Replay Compare tab can compare an imported replay trace against the active source trace.
11. Invalid JSON or invalid trace-shaped files show an error banner without crashing the page.
12. Trace list and Timeline are keyboard accessible.
13. No visible garbled characters appear in the UI.
14. Layout remains readable around narrow widths.

## Phase Completion Criteria

Phase work is complete only when:

- all required commands pass
- generated trace files are schema-compatible
- CLI can inspect at least one successful and one failed run
- CLI can compare two traces as an experiment
- CLI can run a CI regression check with threshold-based pass/fail exit codes
- CLI can generate at least one replay trace
- CLI can apply edited tool mocks from a Replay Plan JSON
- CLI can execute explicit local callable adapter replay
- CLI can compare an original trace path against a replay trace
- SDK can execute adapter replay and capture failed callable or LangGraph adapter runs as failed traces
- SDK can record LangGraph streaming node updates as separate trace steps
- SDK can trace OpenAI Responses and Chat Completions calls with usage mapped into cost
- SDK can expand OpenAI Responses output items into child steps when requested
- SDK can trace Anthropic Messages calls with usage mapped into cost
- SDK can expand Anthropic Messages content blocks into child steps when requested
- CLI can privacy-scan, redact a trace, and import traces into SQLite
- CLI can export a trace as OTLP JSON and push a trace to OTLP HTTP with privacy preflight
- Web UI can load, import, inspect, analyze, compare, and generate replay plans for traces
- Web UI can compare replay traces against their source trace
- docs explain how to reproduce the result

## Known Gaps To Track

- CLI `replay` execution is deterministic and does not call real framework agents.
- CLI `replay-adapter` can execute explicit local Python callables after `--allow-unsafe-code`, but framework-specific CLI replay is not implemented yet.
- Runtime adapter replay exists for Python callables, LangGraph `invoke` and node-level `stream` updates, OpenAI Responses/Chat calls, OpenAI Responses output item expansion, Anthropic Messages calls, and Anthropic content block expansion; Claude Code and Codex adapters are not implemented.
- OpenTelemetry OTLP JSON file export and OTLP HTTP push exist; Collector availability is environment-dependent.
- Hosted storage and multi-user features are not implemented.
