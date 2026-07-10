import {
  DEFAULT_TAB,
  TRACE_CATALOG,
  getCompareTracePath,
  getDefaultTracePath,
  isWorkspaceTab,
} from './workspace.ts';

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

test('workspace exposes Phase 10 tabs including experiment comparison', () => {
  assertEqual(DEFAULT_TAB, 'timeline', 'default tab');
  assertEqual(isWorkspaceTab('timeline'), true, 'timeline tab');
  assertEqual(isWorkspaceTab('analysis'), true, 'analysis tab');
  assertEqual(isWorkspaceTab('diff'), true, 'diff tab');
  assertEqual(isWorkspaceTab('replay'), true, 'replay tab');
  assertEqual(isWorkspaceTab('replayCompare'), true, 'replay compare tab');
  assertEqual(isWorkspaceTab('experiment'), true, 'experiment tab');
  assertEqual(isWorkspaceTab('cost'), false, 'old cost tab removed');
  assertEqual(isWorkspaceTab('latency'), false, 'old latency tab removed');
});

test('trace catalog points at the shipped public traces', () => {
  assertEqual(TRACE_CATALOG.length, 2, 'trace count');
  assertEqual(getDefaultTracePath(), '/traces/sample-success.trace.json', 'default trace');
});

test('diff compare path chooses another trace when possible', () => {
  assertEqual(
    getCompareTracePath('/traces/sample-success.trace.json'),
    '/traces/sample-failure.trace.json',
    'success compares to failure',
  );
  assertEqual(
    getCompareTracePath('/traces/sample-failure.trace.json'),
    '/traces/sample-success.trace.json',
    'failure compares to success',
  );
});
