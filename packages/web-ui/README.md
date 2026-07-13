# Agent DevTools Web UI

React + TypeScript + Vite workbench for visual trace inspection.

## Start Locally

From the repository root, start the local Trace API:

```powershell
py packages\cli\agent_devtools_cli\main.py serve --root .
```

Then start Vite in a second terminal:

```powershell
cd packages\web-ui
npm install
npm run dev
```

Open the local URL printed by Vite, usually `http://127.0.0.1:5173/`. The Trace
Picker automatically shows the latest records from `.agent-devtools/traces.db`.
When the local API is unavailable, the static deployment falls back to bundled
sample traces and browser-imported files.

## Views

- Trace Picker
- Timeline
- Step Inspector
- Analysis
- Run Diff
- Replay Workbench
- Experiment

## Data Layer

The browser data layer lives in:

- `src/types.ts`
- `src/trace.ts`
- `src/workspace.ts`
- `src/local.ts`

Important APIs:

- `loadTrace(url)`
- `normalizeTrace(raw)`
- `computeCostSummary(trace)`
- `buildTimeline(trace)`
- `diffRuns(left, right)`
- `buildReplayPlan(trace, startStepId)`
- `compareExperiment(left, right)`
- `loadPersistedImportedTraces(storage)`
- `appendPersistedImportedTrace(option, trace, storage)`
- `loadLocalTraceCatalog()`
- `loadLocalTrace(path)`

Rules:

- Step costs are the primary source for totals.
- `run.cost` is only a fallback when no step carries cost data.
- Missing `step.cost` remains `null`.
- Run diff aligns repeated runs by position plus `type/name`, not random step ids.
- Experiment comparison follows the CLI rule: success first, then lower cost and lower latency.
- Imported trace labels are saved in browser local storage as history; full trace contents stay in memory and must be re-imported after refresh.

## Commands

```powershell
npm install
npm run test:data
npm run lint
npm run build
npm run dev
```

`test:data` uses Node's built-in TypeScript stripping, so it does not add a test framework dependency.

For CI or release checks, prefer:

```powershell
npm ci --ignore-scripts --registry=https://registry.npmjs.org/
npm audit --audit-level=high --registry=https://registry.npmjs.org/
npm run test:data
npm run lint
npm run build
```

## Sample Traces

The workbench loads sample traces from:

```text
public/traces/
```
