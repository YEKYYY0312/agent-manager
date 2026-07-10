import type {
  Cost,
  CostSummary,
  DiffRow,
  ExperimentArm,
  ExperimentReport,
  ExperimentWinner,
  ModelCostSummary,
  ReplayPlan,
  ReplayComparisonReport,
  ReplayStepChange,
  ReplayToolMock,
  Run,
  RunDiff,
  Step,
  TimelineItem,
  Trace,
} from './types';

const emptyCost: Cost = {
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  amount_usd: 0,
};

const MAX_BROWSER_TRACE_BYTES = 5 * 1024 * 1024;
const TRACE_FETCH_TIMEOUT_MS = 10_000;
const WINDOWS_PATH_SEPARATOR = String.fromCharCode(92);

export async function loadTrace(url: string): Promise<Trace> {
  const safeUrl = safeTraceUrl(url);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TRACE_FETCH_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(safeUrl, { signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
  if (!res.ok) throw new Error(`Failed to load trace: ${res.statusText}`);
  return normalizeTrace(await res.json());
}

export async function loadTraceFromFile(file: File): Promise<Trace> {
  if (file.size > MAX_BROWSER_TRACE_BYTES) {
    throw new Error(`Trace file is too large. Maximum size is ${MAX_BROWSER_TRACE_BYTES} bytes.`);
  }
  const text = await file.text();
  return normalizeTrace(JSON.parse(text));
}

export function normalizeTrace(raw: unknown): Trace {
  if (!isRecord(raw)) {
    throw new Error('Trace root must be an object');
  }

  const rawRun = raw.run;
  if (!isRecord(rawRun)) {
    throw new Error('Trace is missing run object');
  }

  const rawSteps = Array.isArray(raw.steps) ? raw.steps : [];

  return {
    schema_version: stringValue(raw.schema_version, '0.1.0'),
    run: normalizeRun(rawRun),
    steps: rawSteps.map((step) => normalizeStep(step)),
  };
}

export function computeCostSummary(trace: Trace): CostSummary {
  const stepCosts = trace.steps.filter((step) => step.cost && step.cost.total_tokens > 0);
  const source = stepCosts.length > 0 ? 'steps' : trace.run.cost ? 'run' : 'none';
  const totals = source === 'run' && trace.run.cost
    ? trace.run.cost
    : trace.steps.reduce<Cost>((acc, step) => addCost(acc, step.cost), { ...emptyCost });

  const byModelMap = new Map<string, ModelCostSummary>();
  for (const step of stepCosts) {
    const cost = step.cost;
    if (!cost) continue;

    const model = step.model || '(unknown)';
    const current = byModelMap.get(model) ?? {
      model,
      inputTokens: 0,
      outputTokens: 0,
      totalTokens: 0,
      amountUsd: 0,
    };

    current.inputTokens += cost.input_tokens;
    current.outputTokens += cost.output_tokens;
    current.totalTokens += cost.total_tokens;
    current.amountUsd += cost.amount_usd;
    byModelMap.set(model, current);
  }

  return {
    inputTokens: totals.input_tokens,
    outputTokens: totals.output_tokens,
    totalTokens: totals.total_tokens,
    amountUsd: totals.amount_usd,
    source,
    byModel: [...byModelMap.values()].sort((a, b) => b.amountUsd - a.amountUsd),
    expensiveSteps: [...stepCosts].sort((a, b) => (b.cost?.amount_usd ?? 0) - (a.cost?.amount_usd ?? 0)),
  };
}

export function buildTimeline(trace: Trace): TimelineItem[] {
  return trace.steps.map((step, index) => ({
    id: step.id,
    index,
    kind: stepKind(step.type),
    label: step.name,
    status: step.status,
    durationMs: step.duration_ms,
    tokenCount: step.cost?.total_tokens ?? 0,
    costUsd: step.cost?.amount_usd ?? 0,
    isFailure: step.status !== 'success',
    parentId: step.parent_id,
    step,
  }));
}

export function filterSteps(steps: Step[], query: string): Step[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return steps;
  return steps.filter((step) => stepSearchText(step).includes(normalized));
}

export function diffRuns(left: Trace, right: Trace): RunDiff {
  const leftCost = computeCostSummary(left);
  const rightCost = computeCostSummary(right);
  const maxLength = Math.max(left.steps.length, right.steps.length);
  const rows: DiffRow[] = [];

  for (let index = 0; index < maxLength; index += 1) {
    const leftStep = left.steps[index] ?? null;
    const rightStep = right.steps[index] ?? null;
    rows.push(diffStep(index, leftStep, rightStep));
  }

  return {
    leftRunId: left.run.id,
    rightRunId: right.run.id,
    statusChanged: left.run.status !== right.run.status,
    stepCountDelta: right.steps.length - left.steps.length,
    durationDeltaMs: (right.run.duration_ms ?? 0) - (left.run.duration_ms ?? 0),
    tokenDelta: rightCost.totalTokens - leftCost.totalTokens,
    costDeltaUsd: rightCost.amountUsd - leftCost.amountUsd,
    rows,
  };
}

export function compareExperiment(left: Trace, right: Trace): ExperimentReport {
  const leftArm = experimentArm('A', left);
  const rightArm = experimentArm('B', right);
  const delta = {
    tokenDelta: rightArm.totalTokens - leftArm.totalTokens,
    costDeltaUsd: rightArm.costUsd - leftArm.costUsd,
    latencyDeltaMs: rightArm.durationMs - leftArm.durationMs,
    stepCountDelta: rightArm.stepCount - leftArm.stepCount,
    outputChanged: !deepEqual(leftArm.finalOutput, rightArm.finalOutput),
  };
  const winnerBySuccess = successWinner(leftArm, rightArm);
  const winnerByCost = lowerWinner(leftArm.costUsd, rightArm.costUsd);
  const winnerByLatency = lowerWinner(leftArm.durationMs, rightArm.durationMs);
  const [recommendation, recommendationReason] = recommendExperiment(
    winnerBySuccess,
    winnerByCost,
    winnerByLatency,
  );

  return {
    left: leftArm,
    right: rightArm,
    delta,
    winnerBySuccess,
    winnerByCost,
    winnerByLatency,
    recommendation,
    recommendationReason,
  };
}

export function listReplayCheckpoints(trace: Trace): Step[] {
  return trace.steps.filter((step) => step.replayable);
}

export function buildReplayPlan(trace: Trace, startStepId: string): ReplayPlan {
  const startIndex = trace.steps.findIndex((step) => step.id === startStepId);
  const stepsToReplay = startIndex >= 0 ? trace.steps.slice(startIndex) : [];

  return {
    runId: trace.run.id,
    task: trace.run.task,
    startStep: stepsToReplay[0] ?? null,
    stepsToReplay,
    mockedTools: collectToolMocks(stepsToReplay),
  };
}

export function buildReplayCliCommand(tracePath: string, startStepId: string, runId: string, planPath?: string): string {
  const cliTracePath = tracePathForCli(tracePath, runId);
  if (planPath) {
    return `py packages/cli/agent_devtools_cli/main.py replay ${shellQuote(cliTracePath)} --plan ${shellQuote(planPath)} --output-dir traces`;
  }
  return `py packages/cli/agent_devtools_cli/main.py replay ${shellQuote(cliTracePath)} --start-step ${shellQuote(startStepId)} --output-dir traces`;
}

export function buildPortableReplayPlan(plan: ReplayPlan, mockedTools: ReplayToolMock[] = plan.mockedTools) {
  return {
    run_id: plan.runId,
    task: plan.task,
    start_step_id: plan.startStep?.id ?? null,
    start_step_name: plan.startStep?.name ?? null,
    steps_to_replay: plan.stepsToReplay.map((step: Step) => ({
      id: step.id,
      type: step.type,
      name: step.name,
      status: step.status,
    })),
    mocked_tools: mockedTools,
  };
}

export function buildReplayPlanDownload(plan: ReplayPlan, mockedTools: ReplayToolMock[] = plan.mockedTools) {
  return {
    fileName: 'replay-plan.json',
    mimeType: 'application/json',
    content: JSON.stringify(buildPortableReplayPlan(plan, mockedTools), null, 2),
  };
}

export function compareReplay(source: Trace, replay: Trace): ReplayComparisonReport {
  const replaySourceRunId = replay.run.labels.source_run_id ?? '';
  const sourceStartStepId = replay.run.labels.source_start_step_id ?? '';
  const replayMode = replay.run.labels.replay_mode || (replay.run.labels.replay === 'true' ? 'deterministic' : 'unknown');
  const sourceSteps = sourceSlice(source, sourceStartStepId);
  const sourceCost = totalCost(sourceSteps);
  const replayCost = totalCost(replay.steps);
  const sourceDurationMs = stepsDuration(sourceSteps);
  const replayDurationMs = runDuration(replay);
  const sourceStatus = pathStatus(source, sourceSteps);
  const replayStatus = replay.run.status;
  const sourceOutput = pathOutput(source, sourceSteps);
  const replayOutput = traceOutput(replay);

  return {
    sourceRunId: source.run.id,
    replayRunId: replay.run.id,
    replaySourceRunId,
    sourceRunMatch: replaySourceRunId === source.run.id,
    sourceStartStepId,
    replayMode,
    sourceStatus,
    replayStatus,
    sourceStepCount: sourceSteps.length,
    replayStepCount: replay.steps.length,
    sourceDurationMs,
    replayDurationMs,
    sourceTokens: sourceCost.totalTokens,
    replayTokens: replayCost.totalTokens,
    sourceCostUsd: sourceCost.costUsd,
    replayCostUsd: replayCost.costUsd,
    statusChanged: sourceStatus !== replayStatus,
    outputChanged: !deepEqual(sourceOutput, replayOutput),
    delta: {
      tokenDelta: replayCost.totalTokens - sourceCost.totalTokens,
      costDeltaUsd: replayCost.costUsd - sourceCost.costUsd,
      latencyDeltaMs: replayDurationMs - sourceDurationMs,
      stepCountDelta: replay.steps.length - sourceSteps.length,
    },
    stepChanges: replayStepChanges(sourceSteps, replay.steps),
  };
}

export function totalCost(steps: Trace['steps']): { totalTokens: number; costUsd: number } {
  const trace: Trace = {
    schema_version: '0.1.0',
    run: normalizeRun({ id: '', task: '', status: 'success', started_at: '' }),
    steps,
  };
  const summary = computeCostSummary(trace);
  return { totalTokens: summary.totalTokens, costUsd: summary.amountUsd };
}

function tracePathForCli(tracePath: string, runId: string): string {
  if (tracePath.startsWith('/traces/')) {
    return `packages/web-ui/public/traces/${safeTraceFileName(tracePath.slice('/traces/'.length))}`;
  }
  if (tracePath.startsWith('import:')) {
    const fileName = tracePath.split(':').at(-1) || `${runId}.trace.json`;
    return `<path-to-${safeTraceFileName(fileName)}>`;
  }
  if (tracePath.endsWith('.trace.json')) {
    return normalizePathSeparators(tracePath);
  }
  return `traces/${safeTraceFileName(runId)}.trace.json`;
}

function safeTraceUrl(url: string): string {
  if (!url.startsWith('/traces/')) {
    throw new Error('Trace URL is not allowed. Only bundled /traces/*.trace.json files can be fetched.');
  }
  if (!url.endsWith('.trace.json') || url.includes('..') || hasWindowsPathSeparator(url)) {
    throw new Error('Trace URL is not allowed. Only bundled /traces/*.trace.json files can be fetched.');
  }
  return url;
}

function safeTraceFileName(value: string): string {
  const fallback = 'trace.trace.json';
  const base = normalizePathSeparators(value).split('/').at(-1) || fallback;
  const safe = base.replace(/[^A-Za-z0-9._-]/g, '-').replace(/-+/g, '-');
  return safe || fallback;
}

function normalizePathSeparators(value: string): string {
  return value.split(WINDOWS_PATH_SEPARATOR).join('/');
}

function hasWindowsPathSeparator(value: string): boolean {
  return value.includes(WINDOWS_PATH_SEPARATOR);
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

export function fmtMs(ms: number | null): string {
  if (ms === null || ms === undefined) return 'n/a';
  return `${ms.toFixed(0)}ms`;
}

export function fmtUsd(usd: number): string {
  return `$${usd.toFixed(6)}`;
}

export function statusColor(status: string): string {
  switch (status) {
    case 'success':
      return '#22c55e';
    case 'error':
      return '#ef4444';
    case 'timeout':
      return '#f59e0b';
    case 'cancelled':
      return '#94a3b8';
    default:
      return '#64748b';
  }
}

export function stepIcon(type: string): string {
  switch (type) {
    case 'model_call':
      return 'LLM';
    case 'tool_call':
      return 'TOOL';
    case 'planner':
      return 'PLAN';
    case 'retrieval':
      return 'RET';
    case 'memory':
      return 'MEM';
    case 'control':
      return 'CTRL';
    default:
      return 'STEP';
  }
}

function collectToolMocks(steps: Step[]): ReplayToolMock[] {
  return steps
    .filter((step) => step.tool && step.tool.result !== undefined && step.tool.result !== null)
    .map((step) => ({
      stepId: step.id,
      name: step.tool?.name ?? step.name,
      args: step.tool?.args ?? null,
      result: step.tool?.result ?? null,
    }));
}

function stepSearchText(step: Step): string {
  return [
    step.id,
    step.type,
    step.name,
    step.status,
    step.model,
    step.tool?.name ?? '',
    step.error?.type ?? '',
    step.error?.message ?? '',
  ].join(' ').toLowerCase();
}

function sourceSlice(source: Trace, startStepId: string): Step[] {
  if (!startStepId) return source.steps;
  const startIndex = source.steps.findIndex((step) => step.id === startStepId);
  return startIndex >= 0 ? source.steps.slice(startIndex) : source.steps;
}

function stepsDuration(steps: Step[]): number {
  return steps.reduce((total, step) => total + (step.duration_ms ?? 0), 0);
}

function pathStatus(source: Trace, steps: Step[]): string {
  if (steps.some((step) => step.status !== 'success')) {
    return source.run.status === 'success' ? 'error' : source.run.status;
  }
  return 'success';
}

function pathOutput(source: Trace, steps: Step[]): unknown {
  if (steps.length === 0) return source.run.final_output;
  if (source.run.final_output !== null && steps.at(-1)?.id === source.steps.at(-1)?.id) {
    return source.run.final_output;
  }
  return stepOutput(steps.at(-1) ?? null);
}

function traceOutput(trace: Trace): unknown {
  if (trace.run.final_output !== null) return trace.run.final_output;
  return stepOutput(trace.steps.at(-1) ?? null);
}

function stepOutput(step: Step | null): unknown {
  if (!step) return null;
  if (step.type === 'tool_call' && step.tool?.result !== null && step.tool?.result !== undefined) {
    return step.tool.result;
  }
  return step.output;
}

function replayStepChanges(sourceSteps: Step[], replaySteps: Step[]): ReplayStepChange[] {
  const changes: ReplayStepChange[] = [];
  const maxLength = Math.max(sourceSteps.length, replaySteps.length);
  for (let index = 0; index < maxLength; index += 1) {
    const source = sourceSteps[index] ?? null;
    const replay = replaySteps[index] ?? null;
    if (source && replay) {
      appendReplayPairChanges(changes, index, source, replay);
    } else if (source) {
      changes.push({
        index,
        kind: 'missing_replay_step',
        sourceStepId: source.id,
        replayStepId: null,
        detail: `missing replay step for ${source.type}/${source.name}`,
      });
    } else if (replay) {
      changes.push({
        index,
        kind: 'extra_replay_step',
        sourceStepId: null,
        replayStepId: replay.id,
        detail: `extra replay step ${replay.type}/${replay.name}`,
      });
    }
  }
  return changes;
}

function appendReplayPairChanges(changes: ReplayStepChange[], index: number, source: Step, replay: Step): void {
  if (source.type !== replay.type || source.name !== replay.name) {
    changes.push({
      index,
      kind: 'shape_changed',
      sourceStepId: source.id,
      replayStepId: replay.id,
      detail: `${source.type}/${source.name} -> ${replay.type}/${replay.name}`,
    });
  }
  if (source.status !== replay.status) {
    changes.push({
      index,
      kind: 'status_changed',
      sourceStepId: source.id,
      replayStepId: replay.id,
      detail: `${source.status} -> ${replay.status}`,
    });
  }
  if (!deepEqual(stepOutput(source), stepOutput(replay))) {
    changes.push({
      index,
      kind: 'output_changed',
      sourceStepId: source.id,
      replayStepId: replay.id,
      detail: 'step output changed',
    });
  }
  if (tokenCount(source) !== tokenCount(replay) || costUsd(source) !== costUsd(replay)) {
    changes.push({
      index,
      kind: 'cost_changed',
      sourceStepId: source.id,
      replayStepId: replay.id,
      detail: `${tokenCount(source)}t/${fmtUsd(costUsd(source))} -> ${tokenCount(replay)}t/${fmtUsd(costUsd(replay))}`,
    });
  }
}

function normalizeRun(raw: Record<string, unknown>): Run {
  return {
    id: stringValue(raw.id),
    task: stringValue(raw.task),
    status: stringValue(raw.status, 'success'),
    started_at: stringValue(raw.started_at),
    ended_at: nullableString(raw.ended_at),
    duration_ms: nullableNumber(raw.duration_ms),
    labels: recordOfString(raw.labels),
    final_output: raw.final_output ?? null,
    cost: normalizeCost(raw.cost),
  };
}

function normalizeStep(raw: unknown): Step {
  if (!isRecord(raw)) {
    throw new Error('Trace step must be an object');
  }

  return {
    id: stringValue(raw.id),
    parent_id: nullableString(raw.parent_id),
    type: stringValue(raw.type, 'custom'),
    name: stringValue(raw.name),
    status: stringValue(raw.status, 'success'),
    started_at: stringValue(raw.started_at),
    ended_at: nullableString(raw.ended_at),
    duration_ms: nullableNumber(raw.duration_ms),
    model: stringValue(raw.model),
    input: raw.input ?? null,
    output: raw.output ?? null,
    tool: normalizeTool(raw.tool),
    cost: normalizeCost(raw.cost),
    error: normalizeError(raw.error),
    events: Array.isArray(raw.events) ? raw.events : [],
    replayable: Boolean(raw.replayable),
    metadata: isRecord(raw.metadata) ? raw.metadata : {},
  };
}

function normalizeCost(raw: unknown): Cost | null {
  if (!isRecord(raw)) return null;
  return {
    input_tokens: numberValue(raw.input_tokens),
    output_tokens: numberValue(raw.output_tokens),
    total_tokens: numberValue(raw.total_tokens),
    amount_usd: numberValue(raw.amount_usd),
  };
}

function normalizeTool(raw: unknown): Step['tool'] {
  if (!isRecord(raw)) return null;
  return {
    name: stringValue(raw.name),
    args: raw.args ?? null,
    result: raw.result ?? null,
  };
}

function normalizeError(raw: unknown): Step['error'] {
  if (!isRecord(raw)) return null;
  return {
    type: stringValue(raw.type),
    message: stringValue(raw.message),
    stack: stringValue(raw.stack),
  };
}

function diffStep(index: number, left: Step | null, right: Step | null): DiffRow {
  if (!left && right) {
    return row(index, 'added', null, right);
  }
  if (left && !right) {
    return row(index, 'removed', left, null);
  }
  if (!left || !right) {
    return row(index, 'unchanged', null, null);
  }

  const comparable = left.type === right.type && left.name === right.name;
  const changed = !comparable
    || left.status !== right.status
    || tokenCount(left) !== tokenCount(right)
    || Math.abs((left.duration_ms ?? 0) - (right.duration_ms ?? 0)) > 1;

  return row(index, changed ? 'changed' : 'unchanged', left, right);
}

function row(index: number, change: DiffRow['change'], left: Step | null, right: Step | null): DiffRow {
  return {
    key: `${index}:${left?.type ?? right?.type ?? 'missing'}:${left?.name ?? right?.name ?? 'missing'}`,
    change,
    left,
    right,
    statusChanged: Boolean(left && right && left.status !== right.status),
    durationDeltaMs: (right?.duration_ms ?? 0) - (left?.duration_ms ?? 0),
    tokenDelta: tokenCount(right) - tokenCount(left),
    costDeltaUsd: costUsd(right) - costUsd(left),
  };
}

function stepKind(type: string): TimelineItem['kind'] {
  switch (type) {
    case 'model_call':
      return 'model';
    case 'tool_call':
      return 'tool';
    case 'planner':
      return 'planner';
    case 'retrieval':
      return 'retrieval';
    case 'memory':
      return 'memory';
    case 'control':
      return 'control';
    default:
      return 'custom';
  }
}

function addCost(acc: Cost, cost: Cost | null): Cost {
  if (!cost) return acc;
  acc.input_tokens += cost.input_tokens;
  acc.output_tokens += cost.output_tokens;
  acc.total_tokens += cost.total_tokens;
  acc.amount_usd += cost.amount_usd;
  return acc;
}

function tokenCount(step: Step | null): number {
  return step?.cost?.total_tokens ?? 0;
}

function costUsd(step: Step | null): number {
  return step?.cost?.amount_usd ?? 0;
}

function experimentArm(label: 'A' | 'B', trace: Trace): ExperimentArm {
  const cost = computeCostSummary(trace);
  return {
    label,
    traceId: trace.run.id,
    status: trace.run.status,
    stepCount: trace.steps.length,
    failedSteps: trace.steps.filter((step) => step.status !== 'success').length,
    durationMs: runDuration(trace),
    totalTokens: cost.totalTokens,
    costUsd: cost.amountUsd,
    finalOutput: trace.run.final_output,
  };
}

function runDuration(trace: Trace): number {
  if (trace.run.duration_ms !== null && trace.run.duration_ms !== undefined) {
    return trace.run.duration_ms;
  }
  return trace.steps.reduce((total, step) => total + (step.duration_ms ?? 0), 0);
}

function successWinner(left: ExperimentArm, right: ExperimentArm): ExperimentWinner {
  const leftOk = left.status === 'success' && left.failedSteps === 0;
  const rightOk = right.status === 'success' && right.failedSteps === 0;
  if (leftOk === rightOk) return 'tie';
  return leftOk ? 'A' : 'B';
}

function lowerWinner(leftValue: number, rightValue: number): ExperimentWinner {
  if (leftValue === rightValue) return 'tie';
  return leftValue < rightValue ? 'A' : 'B';
}

function recommendExperiment(
  success: ExperimentWinner,
  cost: ExperimentWinner,
  latency: ExperimentWinner,
): [ExperimentWinner, string] {
  if (success !== 'tie') {
    return [success, `${success} has better success status.`];
  }

  const votes = { A: 0, B: 0 };
  for (const winner of [cost, latency]) {
    if (winner === 'A' || winner === 'B') votes[winner] += 1;
  }

  if (votes.A > votes.B) return ['A', 'A is cheaper and/or faster.'];
  if (votes.B > votes.A) return ['B', 'B is cheaper and/or faster.'];
  return ['tie', 'No clear winner; review output quality manually.'];
}

function deepEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
    return left.every((value, index) => deepEqual(value, right[index]));
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    if (!deepEqual(leftKeys, rightKeys)) return false;
    return leftKeys.every((key) => deepEqual(left[key], right[key]));
  }
  return false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function nullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function recordOfString(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  const result: Record<string, string> = {};
  for (const [key, val] of Object.entries(value)) {
    if (typeof val === 'string') result[key] = val;
  }
  return result;
}
