import { useEffect, useState } from 'react';
import type { Trace } from './types';
import type { TraceOption } from './workspace';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  options: TraceOption[];
  selectedPath: string;
  onLoad: (path: string) => Promise<Trace>;
  onPick: (path: string) => void;
  onImport?: (file: File) => void;
}

interface Entry {
  path: string;
  label: string;
  id: string;
  task: string;
  status: string;
}

export function TracePicker({ options, selectedPath, onLoad, onPick, onImport }: Props) {
  const [entries, setEntries] = useState<Entry[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const resolved: Entry[] = [];
      for (const o of options) {
        try {
          const t = await onLoad(o.path);
          if (cancelled) return;
          resolved.push({ path: o.path, label: o.label, id: t.run.id, task: t.run.task, status: t.run.status });
        } catch {
          resolved.push({ path: o.path, label: o.label, id: '--', task: 'unavailable', status: 'error' });
        }
      }
      setEntries(resolved);
    })();
    return () => { cancelled = true; };
  }, [options, onLoad]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && onImport) {
      onImport(file);
      e.target.value = '';
    }
  };

  return (
    <div className="trace-picker">
      <h2 className="sidebar-heading">Trace 列表</h2>
      {onImport && (
        <div className="import-bar">
          <button
            type="button"
            className="import-btn"
            onClick={() => {
              const input = document.getElementById('import-file-input') as HTMLInputElement;
              input?.click();
            }}
          >
            + 导入
          </button>
          <input
            id="import-file-input"
            type="file"
            accept=".json,.trace.json"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
        </div>
      )}
      <div className="trace-list">
        {entries.map(e => (
          <button
            key={e.path}
            type="button"
            className={`trace-entry ${e.path === selectedPath ? 'selected' : ''}`}
            onClick={() => onPick(e.path)}
          >
            <div className="trace-entry-top">
              <span className="trace-entry-label">{e.label}</span>
              <StatusBadge status={e.status} />
            </div>
            <div className="trace-entry-id">{e.id}</div>
            <div className="trace-entry-task">{e.task}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
