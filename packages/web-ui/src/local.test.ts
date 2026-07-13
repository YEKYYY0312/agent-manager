import { localTraceOptions, type LocalTraceSummary } from './local.ts';

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
