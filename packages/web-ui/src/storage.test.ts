import type { Trace } from './types.ts';
import {
  appendPersistedImportedTrace,
  clearPersistedImportedTraces,
  decryptTraceForStorage,
  encryptTraceForStorage,
  loadPersistedImportedTraces,
  persistImportedTrace,
  type AsyncTraceContentStore,
  type TraceContentStore,
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

class MemoryTraceContentStore implements TraceContentStore {
  private values = new Map<string, Trace>();

  load(path: string): Trace | null {
    return this.values.get(path) ?? null;
  }

  save(path: string, trace: Trace): void {
    this.values.set(path, JSON.parse(JSON.stringify(trace)) as Trace);
  }

  remove(path: string): void {
    this.values.delete(path);
  }

  clear(): void {
    this.values.clear();
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

async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
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
  const traceStore = new MemoryTraceContentStore();

  appendPersistedImportedTrace(
    { path: 'import:1:demo.trace.json', label: 'demo.trace.json' },
    trace,
    storage,
    traceStore,
  );
  const loaded = loadPersistedImportedTraces(storage, traceStore);

  assertEqual(loaded.options[0].label, 'demo.trace.json', 'persisted label');
  assertEqual(loaded.options[0].path, 'import:1:demo.trace.json', 'persisted path');
  assertEqual(loaded.traceMap['import:1:demo.trace.json']?.run.id, 'persisted-run', 'full trace is restored');
});

test('appendPersistedImportedTrace keeps sensitive payloads out of localStorage', () => {
  const storage = new MemoryStorage() as Storage;
  const traceStore = new MemoryTraceContentStore();
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
    traceStore,
  );

  const raw = storage.getItem('agent-devtools.imported-traces.v2') ?? '';
  assertEqual(raw.includes('sk-live-secret123'), false, 'api key not persisted');
  assertEqual(raw.includes('hunter2'), false, 'password not persisted');
  assertEqual(raw.includes('github_pat_secret'), false, 'token not persisted');
  const loaded = loadPersistedImportedTraces(storage, traceStore);
  assertEqual(loaded.traceMap['import:2:sensitive.trace.json']?.steps.length, 1, 'trace content restored from content store');
});

await testAsync('persistImportedTrace does not publish an import when encrypted storage fails', async () => {
  const storage = new MemoryStorage() as Storage;
  const failingStore: AsyncTraceContentStore = {
    async load() { return null; },
    async save() { throw new Error('quota exceeded'); },
    async remove() {},
    async clear() {},
  };

  await Promise.resolve(persistImportedTrace(
    { path: 'import:3:failed.trace.json', label: 'failed.trace.json' },
    trace,
    storage,
    failingStore,
  )).catch(() => undefined);

  assertEqual(loadPersistedImportedTraces(storage).options.length, 0, 'failed import is not persisted');
});

await testAsync('persistImportedTrace does not publish an import without encrypted storage', async () => {
  const storage = new MemoryStorage() as Storage;

  await persistImportedTrace(
    { path: 'import:4:memory-only.trace.json', label: 'memory-only.trace.json' },
    trace,
    storage,
    null,
  );

  assertEqual(loadPersistedImportedTraces(storage).options.length, 0, 'memory-only import is not persisted');
});

test('clearPersistedImportedTraces removes saved imports', () => {
  const storage = new MemoryStorage() as Storage;
  const traceStore = new MemoryTraceContentStore();
  appendPersistedImportedTrace({ path: 'import:1:demo.trace.json', label: 'demo.trace.json' }, trace, storage, traceStore);

  clearPersistedImportedTraces(storage, traceStore);
  const loaded = loadPersistedImportedTraces(storage, traceStore);

  assertEqual(loaded.options.length, 0, 'cleared option count');
  assertEqual(traceStore.load('import:1:demo.trace.json'), null, 'cleared trace content');
});

await testAsync('trace storage encryption roundtrips without plaintext payload', async () => {
  const sensitiveTrace: Trace = {
    ...trace,
    run: {
      ...trace.run,
      task: 'Use API key',
      final_output: { token: 'sk-live-secret123' },
    },
  };
  const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);

  const encrypted = await encryptTraceForStorage(sensitiveTrace, key);
  const rendered = JSON.stringify(encrypted);
  const decrypted = await decryptTraceForStorage(encrypted, key);
  const finalOutput = decrypted.run.final_output as Record<string, string>;

  assertEqual(rendered.includes('sk-live-secret123'), false, 'ciphertext hides token');
  assertEqual(rendered.includes('Use API key'), false, 'ciphertext hides task');
  assertEqual(finalOutput.token, 'sk-live-secret123', 'trace decrypts');
});
