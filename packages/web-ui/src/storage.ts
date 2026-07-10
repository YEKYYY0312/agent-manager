import type { Trace } from './types';
import type { TraceOption } from './workspace';

const IMPORT_STORAGE_KEY = 'agent-devtools.imported-traces.v2';

interface PersistedTraceEntry {
  option: TraceOption;
}

export interface PersistedImports {
  options: TraceOption[];
  traceMap: Record<string, Trace>;
}

export function loadPersistedImportedTraces(storage = browserStorage()): PersistedImports {
  if (!storage) return emptyImports();

  try {
    const raw = storage.getItem(IMPORT_STORAGE_KEY);
    if (!raw) return emptyImports();

    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return emptyImports();

    const options: TraceOption[] = [];
    for (const entry of parsed) {
      if (!isPersistedEntry(entry)) continue;
      options.push(entry.option);
    }
    return { options, traceMap: {} };
  } catch {
    return emptyImports();
  }
}

export function persistImportedTraces(
  options: TraceOption[],
  traceMap: Record<string, Trace>,
  storage = browserStorage(),
): void {
  if (!storage) return;

  const entries = options
    .filter((option) => traceMap[option.path])
    .map((option) => ({ option: sanitizeOption(option) }));

  try {
    storage.setItem(IMPORT_STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Browser storage can be disabled or full. Import still works in memory.
  }
}

export function appendPersistedImportedTrace(
  option: TraceOption,
  trace: Trace,
  storage = browserStorage(),
): void {
  const current = loadPersistedImportedTraces(storage);
  const options = current.options.filter((item) => item.path !== option.path);
  persistImportedTraces(
    [...options, option],
    { ...current.traceMap, [option.path]: trace },
    storage,
  );
}

export function clearPersistedImportedTraces(storage = browserStorage()): void {
  if (!storage) return;
  try {
    storage.removeItem(IMPORT_STORAGE_KEY);
  } catch {
    // Ignore unavailable storage.
  }
}

function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function emptyImports(): PersistedImports {
  return { options: [], traceMap: {} };
}

function isPersistedEntry(value: unknown): value is PersistedTraceEntry {
  if (!isRecord(value) || !isRecord(value.option)) return false;
  return typeof value.option.path === 'string'
    && value.option.path.startsWith('import:')
    && typeof value.option.label === 'string';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function sanitizeOption(option: TraceOption): TraceOption {
  return {
    path: option.path,
    label: option.label,
  };
}
