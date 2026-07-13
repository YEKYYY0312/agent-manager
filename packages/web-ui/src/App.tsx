import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Trace } from './types';
import { computeCostSummary, loadTrace, loadTraceFromFile } from './trace';
import { isLocalTracePath, loadLocalTrace, loadLocalTraceCatalog } from './local';
import { Timeline } from './Timeline';
import { StepInspector } from './StepInspector';
import { SummaryBar } from './SummaryBar';
import { TracePicker } from './TracePicker';
import { DiffView } from './DiffView';
import { AnalysisView } from './AnalysisView';
import { ReplayView } from './ReplayView';
import { ReplayCompareView } from './ReplayCompareView';
import { ExperimentView } from './ExperimentView';
import {
  loadPersistedImportedTraces,
  persistImportedTrace,
  restorePersistedTraceContent,
} from './storage';
import {
  DEFAULT_TAB,
  TRACE_CATALOG,
  type WorkspaceTab,
  type TraceOption,
  getDefaultTracePath,
} from './workspace';

const TAB_LABELS: Record<WorkspaceTab, string> = {
  replayCompare: '回放对比 Replay Compare',
  experiment: '实验对比 Experiment',
  timeline: '时间线 Timeline',
  analysis: '分析 Analysis',
  diff: '运行对比 Diff',
  replay: '回放 Replay',
};

const TAB_ORDER: WorkspaceTab[] = ['timeline', 'analysis', 'diff', 'replay', 'replayCompare', 'experiment'];

function App() {
  const defaultPath = getDefaultTracePath();
  const persistedImports = useMemo(() => loadPersistedImportedTraces(), []);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [tracePath, setTracePath] = useState(defaultPath);
  const [error, setError] = useState<string | null>(null);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [view, setView] = useState<WorkspaceTab>(DEFAULT_TAB);
  const [importedTraces, setImportedTraces] = useState<TraceOption[]>(persistedImports.options);
  const [importedTraceMap, setImportedTraceMap] = useState<Record<string, Trace>>(persistedImports.traceMap);
  const [localTraces, setLocalTraces] = useState<TraceOption[]>([]);
  const [dropActive, setDropActive] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const restored = await restorePersistedTraceContent(persistedImports);
      if (cancelled || Object.keys(restored.traceMap).length === 0) return;
      setImportedTraceMap(restored.traceMap);
    })();
    return () => { cancelled = true; };
  }, [persistedImports]);

  const allOptions = useMemo(
    () => [...localTraces, ...TRACE_CATALOG, ...importedTraces],
    [importedTraces, localTraces],
  );

  const comparePath = useMemo(
    () => allOptions.find((t) => t.path !== tracePath)?.path ?? tracePath,
    [tracePath, allOptions],
  );

  const loadWorkspaceTrace = useCallback(async (path: string) => {
    const imported = importedTraceMap[path];
    if (imported) return imported;
    if (path.startsWith('import:')) {
      throw new Error('Imported trace content is not persisted. Please import the file again.');
    }
    if (isLocalTracePath(path)) return loadLocalTrace(path);
    return loadTrace(path);
  }, [importedTraceMap]);

  const handlePick = useCallback(async (path: string) => {
    try {
      setError(null);
      const nextTrace = await loadWorkspaceTrace(path);
      setTrace(nextTrace);
      setTracePath(path);
      setSelectedStepId(null);
    } catch (e) {
      setError(String(e));
    }
  }, [loadWorkspaceTrace]);

  const handleImport = useCallback(async (file: File) => {
    try {
      setError(null);
      const imported = await loadTraceFromFile(file);
      const path = `import:${Date.now()}:${file.name}`;
      const option = { path, label: file.name };
      await persistImportedTrace(option, imported);
      setImportedTraceMap((prev) => ({ ...prev, [path]: imported }));
      setImportedTraces((prev) => [...prev, option]);
      setTrace(imported);
      setTracePath(path);
      setSelectedStepId(null);
    } catch (e) {
      setError(`无效的 Trace 文件：${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropActive(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropActive(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropActive(false);
    const file = e.dataTransfer.files[0];
    if (file) handleImport(file);
  }, [handleImport]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const options = await loadLocalTraceCatalog();
        if (!cancelled) setLocalTraces(options);
      } catch {
        // The static GitHub Pages build has no local API and uses bundled traces.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setError(null);
        const localOptions = await loadLocalTraceCatalog().catch(() => []);
        const initialPath = localOptions[0]?.path ?? defaultPath;
        const initialTrace = isLocalTracePath(initialPath)
          ? await loadLocalTrace(initialPath)
          : await loadTrace(initialPath);
        if (cancelled) return;
        setTrace(initialTrace);
        setTracePath(initialPath);
        setSelectedStepId(null);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [defaultPath]);

  const selected = trace?.steps.find(s => s.id === selectedStepId) ?? null;
  const costSummary = trace ? computeCostSummary(trace) : null;

  return (
    <div className="app">
      <header className="app-header">
        <h1>Agent DevTools</h1>
        <span className="subtitle">Agent 运行可观测性工作台 — 录制、查看、对比 AI Agent 执行 Trace</span>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <div className="layout">
        <aside
            className={`sidebar ${dropActive ? 'drop-target' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <TracePicker
            options={allOptions}
            selectedPath={tracePath}
            onLoad={loadWorkspaceTrace}
            onPick={handlePick}
            onImport={handleImport}
          />
        </aside>

        <main className="main-area">
          {trace && (
            <>
              <SummaryBar trace={trace} />

              <div className="view-tabs">
                {TAB_ORDER.map(tab => (
                  <button
                    key={tab}
                    className={`view-tab ${tab === view ? 'active' : ''}`}
                    onClick={() => setView(tab)}
                  >
                    {TAB_LABELS[tab]}
                  </button>
                ))}
              </div>

              <div className="main-grid">
                {view === 'timeline' && (
                  <>
                    <Timeline
                      steps={trace.steps}
                      selectedId={selectedStepId}
                      onSelect={setSelectedStepId}
                    />
                    <StepInspector step={selected} />
                  </>
                )}

                {view === 'analysis' && costSummary && (
                  <AnalysisView cost={costSummary} steps={trace.steps} />
                )}

                {view === 'diff' && (
                  <DiffView
                    baseTrace={trace}
                    basePath={tracePath}
                    comparePath={comparePath}
                    options={allOptions}
                    onLoad={loadWorkspaceTrace}
                  />
                )}

                {view === 'replay' && (
                  <ReplayView trace={trace} tracePath={tracePath} />
                )}

                {view === 'replayCompare' && (
                  <ReplayCompareView
                    baseTrace={trace}
                    basePath={tracePath}
                    comparePath={comparePath}
                    options={allOptions}
                    onLoad={loadWorkspaceTrace}
                  />
                )}

                {view === 'experiment' && (
                  <ExperimentView
                    baseTrace={trace}
                    basePath={tracePath}
                    comparePath={comparePath}
                    options={allOptions}
                    onLoad={loadWorkspaceTrace}
                  />
                )}
              </div>
            </>
          )}

          {!trace && !error && <div className="loading">正在加载 Trace...</div>}
        </main>
      </div>
    </div>
  );
}

export default App;
