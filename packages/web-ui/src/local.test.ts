import {
  localTraceOptions,
  subscribeToLiveTraces,
  type LocalTraceSummary,
} from './local.ts';

function assertEqual(actual: unknown, expected: unknown, label: string): void {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

const summaries: LocalTraceSummary[] = [
  {
    run_id: 'run-123',
    task: 'Inspect local workspace',
    status: 'success',
    started_at: '2026-07-13T00:00:00Z',
    duration_ms: 12,
    step_count: 2,
    total_tokens: 24,
    cost_usd: 0.001,
    source_path: 'traces/run.trace.json',
  },
];

const options = localTraceOptions(summaries);
assertEqual(options.length, 1, 'local option count');
assertEqual(options[0]?.path, 'local:run-123', 'local option path');
assertEqual(options[0]?.label, 'Inspect local workspace', 'local option label');

const focusedOptions = localTraceOptions([
  { ...summaries[0]!, run_id: 'success-new', task: 'Newest success', started_at: '2026-07-13T03:00:00Z' },
  { ...summaries[0]!, run_id: 'error-new', task: 'Latest error', status: 'error', started_at: '2026-07-13T02:00:00Z' },
  { ...summaries[0]!, run_id: 'cancelled-old', task: 'Cancelled run', status: 'cancelled', started_at: '2026-07-13T01:00:00Z' },
  { ...summaries[0]!, run_id: 'success-old', task: 'Older success', started_at: '2026-07-13T00:00:00Z' },
]);
assertEqual(focusedOptions.length, 3, 'focused local option count');
assertEqual(focusedOptions[0]?.path, 'local:error-new', 'errors appear first');
assertEqual(focusedOptions[1]?.path, 'local:cancelled-old', 'cancelled traces are retained');
assertEqual(focusedOptions[2]?.path, 'local:success-new', 'only newest success is sampled');

let messageListener: ((event: { data: string }) => void) | undefined;
let closed = false;
const liveTraces: string[] = [];
const unsubscribe = subscribeToLiveTraces(
  (trace) => liveTraces.push(trace.run.task),
  () => {
    throw new Error('live trace subscription failed');
  },
  (url) => {
    assertEqual(url, '/api/live/traces', 'live trace stream URL');
    return {
      addEventListener(type, listener) {
        if (type === 'message') messageListener = listener;
      },
      close() {
        closed = true;
      },
    };
  },
);

messageListener?.({
  data: JSON.stringify({
    hook_event_name: 'UserPromptSubmit',
    trace: {
      schema_version: '0.1.0',
      run: {
        id: 'claude-code-live',
        task: 'Stream this run',
        status: 'success',
        started_at: '2026-07-27T00:00:00Z',
        ended_at: null,
        duration_ms: null,
        labels: { source: 'claude-code-http-hooks' },
        final_output: null,
        cost: null,
      },
      steps: [],
    },
  }),
});
assertEqual(liveTraces[0], 'Stream this run', 'live trace task');
unsubscribe();
assertEqual(closed, true, 'live trace connection closed');
