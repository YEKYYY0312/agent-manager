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
  assertEqual(loaded.traceMap['import:1:demo.trace.json'], undefined, 'full trace is not persisted');
});

test('appendPersistedImportedTrace does not store sensitive trace payloads', () => {
  const storage = new MemoryStorage() as Storage;
  const sensitiveTrace: Trace = {
    ...trace,
    run: {
      ...trace.run,
      task: 'Use api key',
      final_output: { api_key: 'sk-live-secret123' },
    },
    steps: [
      {
        id: 'step-secret',
        parent_id: null,
        type: 'tool_call',
        name: 'secret.lookup',
        status: 'success',
        started_at: '2026-07-08T00:00:00Z',
        ended_at: null,
        duration_ms: null,
        model: '',
        input: { password: 'hunter2' },
        output: { token: 'github_pat_secret' },
        tool: {
          name: 'secret.lookup',
          args: { password: 'hunter2' },
          result: { token: 'github_pat_secret' },
        },
        cost: null,
        error: null,
        events: [],
        replayable: false,
        metadata: {},
      },
    ],
  };

  appendPersistedImportedTrace(
    { path: 'import:2:sensitive.trace.json', label: 'sensitive.trace.json' },
    sensitiveTrace,
    storage,
  );

  const raw = storage.getItem('agent-devtools.imported-traces.v2') ?? '';
  assertEqual(raw.includes('sk-live-secret123'), false, 'api key not persisted');
  assertEqual(raw.includes('hunter2'), false, 'password not persisted');
  assertEqual(raw.includes('github_pat_secret'), false, 'token not persisted');
});

test('clearPersistedImportedTraces removes saved imports', () => {
  const storage = new MemoryStorage() as Storage;
  appendPersistedImportedTrace({ path: 'import:1:demo.trace.json', label: 'demo.trace.json' }, trace, storage);

  clearPersistedImportedTraces(storage);
  const loaded = loadPersistedImportedTraces(storage);

  assertEqual(loaded.options.length, 0, 'cleared option count');
});
