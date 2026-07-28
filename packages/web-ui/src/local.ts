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

export interface LocalTraceOption extends TraceOption {
  status: string;
}

interface LiveTraceStream {
  addEventListener(type: string, listener: (event: { data?: string }) => void): void;
  close(): void;
}

type LiveTraceStreamFactory = (url: string) => LiveTraceStream;

export function localTraceOptions(summaries: LocalTraceSummary[]): LocalTraceOption[] {
  return focusLocalTraceOptions(summaries.map((summary) => ({
    path: `local:${summary.run_id}`,
    label: summary.task || summary.run_id,
    status: summary.status,
  })));
}

export function focusLocalTraceOptions(options: LocalTraceOption[]): LocalTraceOption[] {
  const failures = options.filter((option) => option.status !== 'success');
  const latestSuccess = options.find((option) => option.status === 'success');
  return latestSuccess ? [...failures, latestSuccess] : failures;
}

export async function loadLocalTraceCatalog(fetcher: typeof fetch = fetch): Promise<LocalTraceOption[]> {
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

export function subscribeToLiveTraces(
  onTrace: (trace: Trace) => void,
  onError: (error: Error) => void = () => {},
  createStream: LiveTraceStreamFactory = defaultLiveTraceStream,
): () => void {
  const stream = createStream('/api/live/traces');
  stream.addEventListener('message', (event) => {
    try {
      if (typeof event.data !== 'string') throw new Error('Live Trace event is missing data');
      const payload = JSON.parse(event.data) as { trace?: unknown };
      onTrace(normalizeTrace(payload.trace));
    } catch (error) {
      onError(error instanceof Error ? error : new Error(String(error)));
    }
  });
  return () => stream.close();
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

function defaultLiveTraceStream(url: string): LiveTraceStream {
  return new EventSource(url) as unknown as LiveTraceStream;
}
