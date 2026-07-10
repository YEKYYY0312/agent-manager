# Agent DevTools Web UI

React + TypeScript + Vite workbench for visual trace inspection.

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

Rules:

- Step costs are the primary source for totals.
- `run.cost` is only a fallback when no step carries cost data.
- Missing `step.cost` remains `null`.
- Run diff aligns repeated runs by position plus `type/name`, not random step ids.
- Experiment comparison follows the CLI rule: success first, then lower cost and lower latency.
- Imported traces are saved in browser local storage and restored after refresh.

## Commands

```powershell
npm run test:data
npm run lint
npm run build
npm run dev
```

`test:data` uses Node's built-in TypeScript stripping, so it does not add a test framework dependency.

## Sample Traces

The workbench loads sample traces from:

```text
public/traces/
```
