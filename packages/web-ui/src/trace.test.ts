import {
  buildReplayCliCommand,
  buildReplayPlanDownload,
  buildReplayPlan,
  buildTimeline,
  compareReplay,
  compareExperiment,
  computeCostSummary,
  diffRuns,
  listReplayCheckpoints,
  loadTrace,
  loadTraceFromFile,
  normalizeTrace,
} from './trace.ts';
import type { Trace } from './types.ts';

const baseTrace: Trace = {
  schema_version: '0.1.0',
  run: {
    id: 'run-a',
    task: 'Answer with weather',
    status: 'success',
    started_at: '2026-07-05T00:00:00Z',
    ended_at: '2026-07-05T00:00:02Z',
    duration_ms: 2000,
    labels: {},
    final_output: 'done',
    cost: {
      input_tokens: 999,
      output_tokens: 999,
      total_tokens: 1998,
      amount_usd: 10,
    },
  },
  steps: [
    {
      id: 'step-plan-a',
      parent_id: null,
      type: 'planner',
      name: 'Create answer plan',
      status: 'success',
      started_at: '2026-07-05T00:00:00Z',
      ended_at: '2026-07-05T00:00:00.1Z',
      duration_ms: 100,
      model: '',
      input: 'question',
      output: 'call tool',
      tool: null,
      cost: null,
      error: null,
      events: [],
      replayable: true,
      metadata: {},
    },
    {
      id: 'step-tool-a',
      parent_id: null,
      type: 'tool_call',
      name: 'weather.lookup',
      status: 'success',
      started_at: '2026-07-05T00:00:00.1Z',
      ended_at: '2026-07-05T00:00:00.6Z',
      duration_ms: 500,
      model: '',
      input: { city: 'Shanghai' },
      output: { summary: 'warm' },
      tool: {
        name: 'weather.lookup',
        args: { city: 'Shanghai' },
        result: { summary: 'warm' },
      },
      cost: null,
      error: null,
      events: [],
      replayable: true,
      metadata: {},
    },
    {
      id: 'step-model-a',
      parent_id: null,
      type: 'model_call',
      name: 'Generate final answer',
      status: 'success',
      started_at: '2026-07-05T00:00:00.6Z',
      ended_at: '2026-07-05T00:00:02Z',
      duration_ms: 1400,
      model: 'gpt-4.1-mini',
      input: { weather_summary: 'warm' },
      output: 'done',
      tool: null,
      cost: {
        input_tokens: 420,
        output_tokens: 36,
        total_tokens: 456,
        amount_usd: 0.0000312,
      },
      error: null,
      events: [],
      replayable: true,
      metadata: {},
    },
  ],
};

function cloneTrace(trace: Trace): Trace {
  return JSON.parse(JSON.stringify(trace)) as Trace;
}

function assertEqual(actual: unknown, expected: unknown, label: string): void {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function test(name: string, fn: () => void): void {
  try {
    fn();
    console.log(`ok ${name}`);
  } catch (error) {
    console.error(`not ok ${name}`);
    throw error;
  }
}

async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
    console.log(`ok ${name}`);
  } catch (error) {
    console.error(`not ok ${name}`);
    throw error;
  }
}

test('computeCostSummary uses step costs before run cost fallback', () => {
  const summary = computeCostSummary(baseTrace);
  assertEqual(summary.totalTokens, 456, 'step tokens');
  assertEqual(summary.amountUsd, 0.0000312, 'step cost');
  assertEqual(summary.byModel[0].model, 'gpt-4.1-mini', 'model name');
});

test('computeCostSummary falls back to run cost when steps have no cost', () => {
  const trace = cloneTrace(baseTrace);
  trace.steps = trace.steps.map((step) => ({ ...step, cost: null }));

  const summary = computeCostSummary(trace);
  assertEqual(summary.totalTokens, 1998, 'fallback tokens');
  assertEqual(summary.amountUsd, 10, 'fallback cost');
});

test('buildTimeline keeps step order and marks failures', () => {
  const trace = cloneTrace(baseTrace);
  trace.steps[1].status = 'timeout';

  const timeline = buildTimeline(trace);
  assertEqual(timeline.length, 3, 'timeline length');
  assertEqual(timeline[1].kind, 'tool', 'tool kind');
  assertEqual(timeline[1].isFailure, true, 'failure flag');
});

test('diffRuns aligns repeated runs by type name and position instead of random ids', () => {
  const left = cloneTrace(baseTrace);
  const right = cloneTrace(baseTrace);
  right.run.id = 'run-b';
  right.steps[0].id = 'step-plan-b';
  right.steps[1].id = 'step-tool-b';
  right.steps[1].status = 'timeout';
  right.steps[1].error = { type: 'ToolTimeout', message: 'timed out', stack: '' };
  right.steps = right.steps.slice(0, 2);

  const diff = diffRuns(left, right);
  assertEqual(diff.stepCountDelta, -1, 'step count delta');
  assertEqual(diff.rows[0].change, 'unchanged', 'planner unchanged despite id change');
  assertEqual(diff.rows[1].change, 'changed', 'tool status changed');
  assertEqual(diff.rows[2].change, 'removed', 'model removed');
});

test('normalizeTrace preserves missing cost as null', () => {
  const raw = {
    schema_version: '0.1.0',
    run: {
      id: 'run-min',
      task: 'minimal',
      status: 'success',
      started_at: '2026-07-05T00:00:00Z',
    },
    steps: [
      {
        id: 'step-min',
        type: 'tool_call',
        name: 'tool',
        status: 'success',
        started_at: '2026-07-05T00:00:00Z',
      },
    ],
  };

  const trace = normalizeTrace(raw);
  assertEqual(trace.steps[0].cost, null, 'missing cost remains null');
  assertEqual(trace.steps[0].tool, null, 'missing tool remains null');
});

test('listReplayCheckpoints returns only replayable steps in order', () => {
  const trace = cloneTrace(baseTrace);
  trace.steps[1].replayable = false;

  const checkpoints = listReplayCheckpoints(trace);
  assertEqual(checkpoints.length, 2, 'checkpoint count');
  assertEqual(checkpoints[0].id, 'step-plan-a', 'first checkpoint');
  assertEqual(checkpoints[1].id, 'step-model-a', 'second checkpoint');
});

test('buildReplayPlan captures downstream steps and tool mocks from selected checkpoint', () => {
  const plan = buildReplayPlan(baseTrace, 'step-plan-a');

  assertEqual(plan.runId, 'run-a', 'run id');
  assertEqual(plan.startStep?.id, 'step-plan-a', 'start step');
  assertEqual(plan.stepsToReplay.length, 3, 'steps to replay');
  assertEqual(plan.mockedTools.length, 1, 'mocked tool count');
  assertEqual(plan.mockedTools[0].name, 'weather.lookup', 'mocked tool name');
  assertEqual(plan.mockedTools[0].stepId, 'step-tool-a', 'mocked tool step id');
});

test('buildReplayCliCommand targets shipped public sample traces', () => {
  const command = buildReplayCliCommand('/traces/sample-success.trace.json', 'step-plan-a', 'run-a');

  assertEqual(
    command,
    "py packages/cli/agent_devtools_cli/main.py replay 'packages/web-ui/public/traces/sample-success.trace.json' --start-step 'step-plan-a' --output-dir traces",
    'sample replay command',
  );
});

test('buildReplayCliCommand uses editable placeholder for imported traces', () => {
  const command = buildReplayCliCommand('import:123456:custom.trace.json', 'step-plan-a', 'run-a');

  assertEqual(
    command,
    "py packages/cli/agent_devtools_cli/main.py replay '<path-to-custom.trace.json>' --start-step 'step-plan-a' --output-dir traces",
    'import replay command',
  );
});

test('buildReplayCliCommand can use a replay plan file', () => {
  const command = buildReplayCliCommand('/traces/sample-success.trace.json', 'step-plan-a', 'run-a', 'replay-plan.json');

  assertEqual(
    command,
    "py packages/cli/agent_devtools_cli/main.py replay 'packages/web-ui/public/traces/sample-success.trace.json' --plan 'replay-plan.json' --output-dir traces",
    'plan replay command',
  );
});

test('buildReplayCliCommand quotes malicious imported filenames safely', () => {
  const command = buildReplayCliCommand('import:123:a";calc#.trace.json', 'step";calc#', 'run-a', "plan';calc#.json");

  assertEqual(command.includes('"'), false, 'no double quote injection surface');
  assertEqual(command.includes('";'), false, 'no double quote command break');
  assertEqual(
    command,
    "py packages/cli/agent_devtools_cli/main.py replay '<path-to-a-calc-.trace.json>' --plan 'plan'';calc#.json' --output-dir traces",
    'malicious filename command',
  );
});

test('buildReplayPlanDownload emits replay-plan.json with edited tool mock result', () => {
  const plan = buildReplayPlan(baseTrace, 'step-plan-a');
  const download = buildReplayPlanDownload(plan, [
    {
      ...plan.mockedTools[0],
      result: { summary: 'Cold and windy, no rain, 12 C' },
    },
  ]);
  const payload = JSON.parse(download.content);

  assertEqual(download.fileName, 'replay-plan.json', 'download filename');
  assertEqual(download.mimeType, 'application/json', 'download mime type');
  assertEqual(payload.run_id, 'run-a', 'payload run id');
  assertEqual(payload.mocked_tools[0].result.summary, 'Cold and windy, no rain, 12 C', 'edited tool result');
});

test('compareExperiment recommends the successful run over a cheaper failed run', () => {
  const left = cloneTrace(baseTrace);
  const right = cloneTrace(baseTrace);
  right.run.id = 'run-b';
  right.run.status = 'error';
  right.run.duration_ms = 800;
  right.run.final_output = null;
  right.run.cost = {
    input_tokens: 120,
    output_tokens: 20,
    total_tokens: 140,
    amount_usd: 0.00001,
  };
  right.steps = right.steps.slice(0, 2);
  right.steps[1].status = 'error';
  right.steps[1].error = { type: 'ToolError', message: 'failed', stack: '' };

  const report = compareExperiment(left, right);

  assertEqual(report.left.traceId, 'run-a', 'left trace id');
  assertEqual(report.right.traceId, 'run-b', 'right trace id');
  assertEqual(report.right.failedSteps, 1, 'right failed steps');
  assertEqual(report.delta.stepCountDelta, -1, 'step count delta');
  assertEqual(report.delta.outputChanged, true, 'output changed');
  assertEqual(report.winnerBySuccess, 'A', 'success winner');
  assertEqual(report.winnerByCost, 'B', 'cost winner');
  assertEqual(report.winnerByLatency, 'B', 'latency winner');
  assertEqual(report.recommendation, 'A', 'recommendation');
});

test('compareExperiment falls back to summed step durations when run duration is missing', () => {
  const left = cloneTrace(baseTrace);
  const right = cloneTrace(baseTrace);
  left.run.duration_ms = null;
  right.run.id = 'run-b';
  right.run.duration_ms = null;
  right.steps[2].duration_ms = 900;

  const report = compareExperiment(left, right);

  assertEqual(report.left.durationMs, 2000, 'left summed duration');
  assertEqual(report.right.durationMs, 1500, 'right summed duration');
  assertEqual(report.delta.latencyDeltaMs, -500, 'latency delta');
  assertEqual(report.winnerByLatency, 'B', 'latency winner');
});

test('compareExperiment treats structurally equal final outputs as unchanged', () => {
  const left = cloneTrace(baseTrace);
  const right = cloneTrace(baseTrace);
  left.run.final_output = { answer: 'done', sources: ['tool'] };
  right.run.final_output = { sources: ['tool'], answer: 'done' };

  const report = compareExperiment(left, right);

  assertEqual(report.delta.outputChanged, false, 'structural output equality');
});

test('compareReplay uses source start step path', () => {
  const source = cloneTrace(baseTrace);
  const replay = cloneTrace(baseTrace);
  replay.run.id = 'replay-a';
  replay.run.task = `Replay: ${source.run.task}`;
  replay.run.labels = {
    replay: 'true',
    source_run_id: source.run.id,
    source_start_step_id: source.steps[1].id,
  };
  replay.steps = source.steps.slice(1).map((step) => ({
    ...JSON.parse(JSON.stringify(step)),
    id: `replay-${step.id}`,
    metadata: { source_step_id: step.id, replay_mode: 'mocked_tool_result' },
  }));
  replay.run.final_output = source.run.final_output;
  replay.run.duration_ms = 1900;

  const report = compareReplay(source, replay);

  assertEqual(report.sourceRunMatch, true, 'source run match');
  assertEqual(report.sourceStartStepId, source.steps[1].id, 'source start step');
  assertEqual(report.sourceStepCount, 2, 'source slice count');
  assertEqual(report.replayStepCount, 2, 'replay step count');
  assertEqual(report.delta.stepCountDelta, 0, 'step delta');
  assertEqual(report.outputChanged, false, 'output unchanged');
});

test('compareReplay reports output drift', () => {
  const source = cloneTrace(baseTrace);
  const replay = cloneTrace(baseTrace);
  replay.run.id = 'replay-b';
  replay.run.labels = {
    replay: 'true',
    replay_mode: 'deterministic',
    source_run_id: source.run.id,
    source_start_step_id: source.steps[1].id,
  };
  replay.steps = source.steps.slice(1).map((step) => JSON.parse(JSON.stringify(step)));
  replay.steps[0].output = { summary: 'Cold and windy, no rain, 12 C' };
  if (replay.steps[0].tool) {
    replay.steps[0].tool.result = { summary: 'Cold and windy, no rain, 12 C' };
  }

  const report = compareReplay(source, replay);

  assertEqual(report.stepChanges.some((change) => change.kind === 'output_changed'), true, 'step output changed');
});

await testAsync('loadTrace rejects external URLs before fetch', async () => {
  const originalFetch = globalThis.fetch;
  let called = false;
  globalThis.fetch = (() => {
    called = true;
    throw new Error('fetch should not be called');
  }) as typeof fetch;
  try {
    let message = '';
    try {
      await loadTrace('https://evil.example/exfil.trace.json');
    } catch (error) {
      message = String((error as Error).message);
    }
    assertEqual(called, false, 'external fetch was blocked before network');
    assertEqual(message.includes('Trace URL is not allowed'), true, 'blocked message');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

await testAsync('loadTraceFromFile rejects oversized trace files', async () => {
  const file = new File(['x'.repeat(5 * 1024 * 1024 + 1)], 'large.trace.json', { type: 'application/json' });
  let message = '';
  try {
    await loadTraceFromFile(file);
  } catch (error) {
    message = String((error as Error).message);
  }

  assertEqual(message.includes('Trace file is too large'), true, 'oversized import rejected');
});
