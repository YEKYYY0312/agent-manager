import type { Trace } from './types.ts';
import { normalizeTrace } from './trace.ts';
import type { TraceOption } from './workspace.ts';

export interface LocalTraceSummary {
  run_id: string;
  task: string;
  status: string;
  started_at: string;
  duration_ms: number | null;
  step_count: number;
  total_tokens: number;
  cost_usd: number;
  source_path: string;
}

interface LocalTraceListResponse {
  traces: LocalTraceSummary[];
}

export function localTraceOptions(summaries: LocalTraceSummary[]): TraceOption[] {
  return summaries.map((summary) => ({
    path: `local:${summary.run_id}`,
    label: summary.task || summary.run_id,
  }));
}

export async function loadLocalTraceCatalog(fetcher: typeof fetch = fetch): Promise<TraceOption[]> {
  const response = await fetcher('/api/traces', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Local Trace API returned ${response.status}`);
  const payload = await response.json() as LocalTraceListResponse;
  if (!Array.isArray(payload.traces)) throw new Error('Local Trace API returned an invalid trace list');
  return localTraceOptions(payload.traces.filter(isLocalTraceSummary));
}

export async function loadLocalTrace(path: string, fetcher: typeof fetch = fetch): Promise<Trace> {
  const runId = localRunId(path);
  if (!runId) throw new Error(`Invalid local Trace path: ${path}`);
  const response = await fetcher(`/api/traces/${encodeURIComponent(runId)}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Local Trace API returned ${response.status}`);
  return normalizeTrace(await response.json());
}

export function isLocalTracePath(path: string): boolean {
  return Boolean(localRunId(path));
}

function localRunId(path: string): string | null {
  if (!path.startsWith('local:')) return null;
  const runId = path.slice('local:'.length);
  return runId && !runId.includes('/') ? runId : null;
}

function isLocalTraceSummary(value: unknown): value is LocalTraceSummary {
  if (!value || typeof value !== 'object') return false;
  const summary = value as Partial<LocalTraceSummary>;
  return typeof summary.run_id === 'string'
    && typeof summary.task === 'string'
    && typeof summary.status === 'string';
}
