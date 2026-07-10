import type { Trace } from './types';
import type { TraceOption } from './workspace';
import { normalizeTrace } from './trace.ts';

const IMPORT_STORAGE_KEY = 'agent-devtools.imported-traces.v1';

interface PersistedTraceEntry {
  option: TraceOption;
  trace: Trace;
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
    const traceMap: Record<string, Trace> = {};
    for (const entry of parsed) {
      if (!isPersistedEntry(entry)) continue;
      try {
        const trace = normalizeTrace(entry.trace);
        options.push(entry.option);
        traceMap[entry.option.path] = trace;
      } catch {
        // Skip malformed persisted entries so one bad trace cannot break startup.
      }
    }
    return { options, traceMap };
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
    .map((option) => ({ option, trace: traceMap[option.path] }));

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
    && typeof value.option.label === 'string'
    && isRecord(value.trace);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
