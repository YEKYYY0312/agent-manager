import type { Trace } from './types.ts';
import {
  appendPersistedImportedTrace,
  clearPersistedImportedTraces,
  loadPersistedImportedTraces,
} from './storage.ts';

class MemoryStorage {
  private values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  clear(): void {
    this.values.clear();
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }
}

const trace: Trace = {
  schema_version: '0.1.0',
  run: {
    id: 'persisted-run',
    task: 'Persist imported trace',
    status: 'success',
    started_at: '2026-07-08T00:00:00Z',
    ended_at: null,
    duration_ms: null,
    labels: {},
    final_output: null,
    cost: null,
  },
  steps: [],
};

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

test('loadPersistedImportedTraces returns empty state when storage is empty', () => {
  const loaded = loadPersistedImportedTraces(new MemoryStorage() as Storage);

  assertEqual(loaded.options.length, 0, 'option count');
  assertEqual(Object.keys(loaded.traceMap).length, 0, 'trace map count');
});

test('appendPersistedImportedTrace stores imported traces for reload', () => {
  const storage = new MemoryStorage() as Storage;

  appendPersistedImportedTrace(
    { path: 'import:1:demo.trace.json', label: 'demo.trace.json' },
    trace,
    storage,
  );
  const loaded = loadPersistedImportedTraces(storage);

  assertEqual(loaded.options[0].label, 'demo.trace.json', 'persisted label');
  assertEqual(loaded.options[0].path, 'import:1:demo.trace.json', 'persisted path');
  assertEqual(loaded.traceMap['import:1:demo.trace.json'].run.id, 'persisted-run', 'persisted trace');
});

test('clearPersistedImportedTraces removes saved imports', () => {
  const storage = new MemoryStorage() as Storage;
  appendPersistedImportedTrace({ path: 'import:1:demo.trace.json', label: 'demo.trace.json' }, trace, storage);

  clearPersistedImportedTraces(storage);
  const loaded = loadPersistedImportedTraces(storage);

  assertEqual(loaded.options.length, 0, 'cleared option count');
});
