export type WorkspaceTab = 'timeline' | 'analysis' | 'diff' | 'replay' | 'replayCompare' | 'experiment';

export interface TraceOption {
  path: string;
  label: string;
}

export const DEFAULT_TAB: WorkspaceTab = 'timeline';

export const TRACE_CATALOG: TraceOption[] = createTraceCatalog();

export function createTraceCatalog(baseUrl = import.meta.env?.BASE_URL ?? '/'): TraceOption[] {
  const traceBasePath = getBundledTraceBasePath(baseUrl);
  return [
    { path: `${traceBasePath}sample-success.trace.json`, label: 'sample-success' },
    { path: `${traceBasePath}sample-failure.trace.json`, label: 'sample-failure' },
  ];
}

export function getBundledTraceBasePath(baseUrl = import.meta.env?.BASE_URL ?? '/'): string {
  const normalizedBaseUrl = baseUrl.startsWith('/') ? baseUrl : `/${baseUrl}`;
  return `${normalizedBaseUrl.endsWith('/') ? normalizedBaseUrl : `${normalizedBaseUrl}/`}traces/`;
}

export function isWorkspaceTab(value: string): value is WorkspaceTab {
  return value === 'timeline'
    || value === 'analysis'
    || value === 'diff'
    || value === 'replay'
    || value === 'replayCompare'
    || value === 'experiment';
}

export function getDefaultTracePath(): string {
  return TRACE_CATALOG[0]?.path ?? '';
}

export function getCompareTracePath(activePath: string): string {
  return TRACE_CATALOG.find((trace) => trace.path !== activePath)?.path ?? activePath;
}
