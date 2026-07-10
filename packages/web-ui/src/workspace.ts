export type WorkspaceTab = 'timeline' | 'analysis' | 'diff' | 'replay' | 'replayCompare' | 'experiment';

export interface TraceOption {
  path: string;
  label: string;
}

export const DEFAULT_TAB: WorkspaceTab = 'timeline';

export const TRACE_CATALOG: TraceOption[] = [
  { path: '/traces/sample-success.trace.json', label: 'sample-success' },
  { path: '/traces/sample-failure.trace.json', label: 'sample-failure' },
];

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
