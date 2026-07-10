import type { Trace } from './types';
import type { TraceOption } from './workspace';

const IMPORT_STORAGE_KEY = 'agent-devtools.imported-traces.v2';
const TRACE_DB_NAME = 'agent-devtools-traces';
const TRACE_DB_VERSION = 1;
const TRACE_STORE_NAME = 'imported-traces';

interface PersistedTraceEntry {
  option: TraceOption;
}

export interface PersistedImports {
  options: TraceOption[];
  traceMap: Record<string, Trace>;
}

export interface TraceContentStore {
  load(path: string): Trace | null;
  save(path: string, trace: Trace): void;
  remove(path: string): void;
  clear(): void;
}

export interface AsyncTraceContentStore {
  load(path: string): Promise<Trace | null>;
  save(path: string, trace: Trace): Promise<void>;
  remove(path: string): Promise<void>;
  clear(): Promise<void>;
}

export function loadPersistedImportedTraces(
  storage = browserStorage(),
  traceStore?: TraceContentStore,
): PersistedImports {
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
      options.push(entry.option);
      const trace = traceStore?.load(entry.option.path);
      if (trace) traceMap[entry.option.path] = trace;
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
  traceStore?: TraceContentStore,
): void {
  const current = loadPersistedImportedTraces(storage);
  const options = current.options.filter((item) => item.path !== option.path);
  traceStore?.save(option.path, trace);
  persistImportedTraces(
    [...options, option],
    { ...current.traceMap, [option.path]: trace },
    storage,
  );
}

export function clearPersistedImportedTraces(storage = browserStorage(), traceStore?: TraceContentStore): void {
  if (!storage) return;
  try {
    storage.removeItem(IMPORT_STORAGE_KEY);
    traceStore?.clear();
  } catch {
    // Ignore unavailable storage.
  }
}

export async function restorePersistedTraceContent(
  imports: PersistedImports,
  traceStore = browserTraceContentStore(),
): Promise<PersistedImports> {
  if (!traceStore || imports.options.length === 0) return imports;
  const traceMap: Record<string, Trace> = { ...imports.traceMap };
  for (const option of imports.options) {
    if (traceMap[option.path]) continue;
    const trace = await traceStore.load(option.path);
    if (trace) traceMap[option.path] = trace;
  }
  return { options: imports.options, traceMap };
}

export async function saveImportedTraceContent(
  option: TraceOption,
  trace: Trace,
  traceStore = browserTraceContentStore(),
): Promise<void> {
  if (!traceStore) return;
  await traceStore.save(option.path, trace);
}

export function browserTraceContentStore(): AsyncTraceContentStore | null {
  if (typeof indexedDB === 'undefined') return null;
  return {
    async load(path: string) {
      const db = await openTraceDb();
      return requestResult<Trace | undefined>(db.transaction(TRACE_STORE_NAME, 'readonly').objectStore(TRACE_STORE_NAME).get(path))
        .then((trace) => trace ?? null)
        .finally(() => db.close());
    },
    async save(path: string, trace: Trace) {
      const db = await openTraceDb();
      await requestResult(db.transaction(TRACE_STORE_NAME, 'readwrite').objectStore(TRACE_STORE_NAME).put(trace, path))
        .finally(() => db.close());
    },
    async remove(path: string) {
      const db = await openTraceDb();
      await requestResult(db.transaction(TRACE_STORE_NAME, 'readwrite').objectStore(TRACE_STORE_NAME).delete(path))
        .finally(() => db.close());
    },
    async clear() {
      const db = await openTraceDb();
      await requestResult(db.transaction(TRACE_STORE_NAME, 'readwrite').objectStore(TRACE_STORE_NAME).clear())
        .finally(() => db.close());
    },
  };
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

function openTraceDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(TRACE_DB_NAME, TRACE_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(TRACE_STORE_NAME)) {
        db.createObjectStore(TRACE_STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Failed to open trace content database'));
  });
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'));
  });
}
