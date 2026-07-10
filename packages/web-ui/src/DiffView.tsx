import { useCallback, useEffect, useState } from 'react';
import type { RunDiff, Trace } from './types';
import type { TraceOption } from './workspace';
import { diffRuns, fmtMs, fmtUsd } from './trace';
import { MetricCard } from './components/MetricCard';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  baseTrace: Trace;
  basePath: string;
  comparePath: string;
  options: TraceOption[];
  onLoad: (path: string) => Promise<Trace>;
}

export function DiffView({ baseTrace, basePath, comparePath, options, onLoad }: Props) {
  const [selectedPath, setSelectedPath] = useState(comparePath);
  const [diff, setDiff] = useState<RunDiff | null>(null);
  const [loading, setLoading] = useState(false);

  // Reset when the base trace or comparison selection changes.
  useEffect(() => {
    setSelectedPath(comparePath);
    setDiff(null);
  }, [baseTrace.run.id, basePath, comparePath]);

  const compare = useCallback(async (path: string) => {
    setSelectedPath(path);
    setLoading(true);
    try {
      setDiff(diffRuns(baseTrace, await onLoad(path)));
    } catch {
      setDiff(null);
    } finally {
      setLoading(false);
    }
  }, [baseTrace, onLoad]);

  return (
    <div className="panel-full">
      <h2 className="panel-heading">运行对比 Run Diff</h2>

      <div className="diff-picker">
        <div className="diff-side">
          <span className="diff-label">基准</span>
          <code>{baseTrace.run.id}</code>
          <span className="diff-path">{basePath}</span>
        </div>
        <span className="diff-vs">vs</span>
        <div className="diff-side">
          <span className="diff-label">对比</span>
          <div className="inline-controls">
            <select value={selectedPath} onChange={e => setSelectedPath(e.target.value)}>
              {options.map(o => (
                <option key={o.path} value={o.path}>{o.label}</option>
              ))}
            </select>
            <button className="btn" onClick={() => compare(selectedPath)}>对比</button>
          </div>
        </div>
      </div>

      {loading && <p className="muted" style={{ textAlign: 'center' }}>正在计算差异...</p>}

      {!diff && !loading && (
        <div className="empty-state">
          <p>选择一个对比 Trace，点击"对比"查看两次运行的差异。</p>
        </div>
      )}

      {diff && (
        <>
          <div className="metrics-row">
            <MetricCard
              label="步骤数差"
              value={signed(diff.stepCountDelta)}
              kind={diff.stepCountDelta !== 0 ? 'warn' : 'default'}
            />
            <MetricCard
              label="耗时差"
              value={signedDuration(diff.durationDeltaMs)}
              kind={diff.durationDeltaMs !== 0 ? 'warn' : 'default'}
            />
            <MetricCard
              label="Token 差"
              value={signed(diff.tokenDelta)}
              kind={diff.tokenDelta !== 0 ? 'warn' : 'default'}
            />
            <MetricCard
              label="费用差"
              value={signedUsd(diff.costDeltaUsd)}
              kind={diff.costDeltaUsd !== 0 ? (diff.costDeltaUsd > 0 ? 'error' : 'accent') : 'default'}
            />
          </div>

          <div className="diff-table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="d-col"></th>
                  <th>基准步骤</th>
                  <th>对比步骤</th>
                  <th className="num">耗时</th>
                  <th className="num">Token</th>
                </tr>
              </thead>
              <tbody>
                {diff.rows.map(row => (
                  <tr key={row.key} className={`diff-row diff-${row.change}`}>
                    <td className="d-col">{changeIcon(row.change)}</td>
                    <td>
                      {row.left && (
                        <div className="diff-step-info">
                          <span className="step-name">{row.left.name}</span>
                          <StatusBadge status={row.left.status} />
                        </div>
                      )}
                    </td>
                    <td>
                      {row.right && (
                        <div className="diff-step-info">
                          <span className="step-name">{row.right.name}</span>
                          <StatusBadge status={row.right.status} />
                        </div>
                      )}
                    </td>
                    <td className={`num ${row.durationDeltaMs !== 0 ? 'delta-nonzero' : ''}`}>
                      {row.durationDeltaMs !== 0 ? signedDuration(row.durationDeltaMs) : '-'}
                    </td>
                    <td className={`num ${row.tokenDelta !== 0 ? 'delta-nonzero' : ''}`}>
                      {row.tokenDelta !== 0 ? signed(row.tokenDelta) : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function signed(n: number): string {
  return n > 0 ? `+${n}` : String(n);
}
function signedDuration(n: number): string {
  return n > 0 ? `+${fmtMs(n)}` : fmtMs(n);
}
function signedUsd(n: number): string {
  return n > 0 ? `+${fmtUsd(n)}` : fmtUsd(n);
}
function changeIcon(c: string): string {
  switch (c) {
    case 'added': return '+';
    case 'removed': return '-';
    case 'changed': return '~';
    default: return '=';
  }
}
