import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReplayComparisonReport, Trace } from './types';
import type { TraceOption } from './workspace';
import { compareReplay, fmtMs, fmtUsd } from './trace';
import { EmptyState } from './components/EmptyState';
import { MetricCard } from './components/MetricCard';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  baseTrace: Trace;
  basePath: string;
  comparePath: string;
  options: TraceOption[];
  onLoad: (path: string) => Promise<Trace>;
}

export function ReplayCompareView({ baseTrace, basePath, comparePath, options, onLoad }: Props) {
  const replayOptions = useMemo(
    () => options.filter((option) => option.path !== basePath),
    [basePath, options],
  );
  const [selectedPath, setSelectedPath] = useState(comparePath);
  const [report, setReport] = useState<ReplayComparisonReport | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setSelectedPath(comparePath);
    setReport(null);
  }, [baseTrace.run.id, basePath, comparePath]);

  const compare = useCallback(async (path: string) => {
    setSelectedPath(path);
    setLoading(true);
    try {
      setReport(compareReplay(baseTrace, await onLoad(path)));
    } catch {
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, [baseTrace, onLoad]);

  if (replayOptions.length === 0) {
    return (
      <div className="panel-full">
        <EmptyState
          title="回放对比 Replay Compare"
          message="导入 replay trace 后，可以在这里对比原始路径和 replay 结果。"
        />
      </div>
    );
  }

  return (
    <div className="panel-full replay-compare-view">
      <h2 className="panel-heading">回放对比 Replay Compare</h2>

      <div className="diff-picker">
        <div className="diff-side">
          <span className="diff-label">原始 Trace</span>
          <code>{baseTrace.run.id}</code>
          <span className="diff-path">{basePath}</span>
        </div>
        <span className="diff-vs">vs</span>
        <div className="diff-side">
          <span className="diff-label">Replay Trace</span>
          <div className="inline-controls">
            <select value={selectedPath} onChange={(event) => setSelectedPath(event.target.value)}>
              {replayOptions.map((option) => (
                <option key={option.path} value={option.path}>{option.label}</option>
              ))}
            </select>
            <button className="btn" type="button" onClick={() => compare(selectedPath)}>对比</button>
          </div>
        </div>
      </div>

      {loading && <p className="muted replay-compare-status">正在计算 replay 差异...</p>}

      {!report && !loading && (
        <div className="empty-state">
          <p>选择一个 replay trace，点击“对比”查看 replay 是否复现原始路径。</p>
        </div>
      )}

      {report && (
        <>
          <div className="metrics-row">
            <MetricCard label="来源匹配" value={report.sourceRunMatch ? '是' : '否'} kind={report.sourceRunMatch ? 'accent' : 'error'} />
            <MetricCard label="状态变化" value={report.statusChanged ? '有' : '无'} kind={report.statusChanged ? 'warn' : 'default'} />
            <MetricCard label="输出变化" value={report.outputChanged ? '有' : '无'} kind={report.outputChanged ? 'warn' : 'default'} />
            <MetricCard label="Replay 模式" value={report.replayMode} />
          </div>

          <div className="metrics-row">
            <MetricCard label="步骤差" value={signed(report.delta.stepCountDelta)} kind={report.delta.stepCountDelta !== 0 ? 'warn' : 'default'} />
            <MetricCard label="耗时差" value={signedDuration(report.delta.latencyDeltaMs)} kind={report.delta.latencyDeltaMs !== 0 ? 'warn' : 'default'} />
            <MetricCard label="Token 差" value={signed(report.delta.tokenDelta)} kind={report.delta.tokenDelta !== 0 ? 'warn' : 'default'} />
            <MetricCard label="费用差" value={signedUsd(report.delta.costDeltaUsd)} kind={report.delta.costDeltaUsd !== 0 ? 'warn' : 'default'} />
          </div>

          <section className="replay-compare-summary">
            <div>
              <span className="diff-label">原始片段</span>
              <p><StatusBadge status={report.sourceStatus} /> {report.sourceStepCount} steps / {fmtMs(report.sourceDurationMs)} / {report.sourceTokens}t / {fmtUsd(report.sourceCostUsd)}</p>
            </div>
            <div>
              <span className="diff-label">Replay</span>
              <p><StatusBadge status={report.replayStatus} /> {report.replayStepCount} steps / {fmtMs(report.replayDurationMs)} / {report.replayTokens}t / {fmtUsd(report.replayCostUsd)}</p>
            </div>
            <div>
              <span className="diff-label">起点</span>
              <p className="mono">{report.sourceStartStepId || 'n/a'}</p>
            </div>
          </section>

          {report.stepChanges.length === 0 ? (
            <div className="empty-state replay-compare-clean">
              <p>没有发现 replay step 差异。</p>
            </div>
          ) : (
            <div className="diff-table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>类型</th>
                    <th>说明</th>
                    <th>原始 step</th>
                    <th>Replay step</th>
                  </tr>
                </thead>
                <tbody>
                  {report.stepChanges.map((change) => (
                    <tr key={`${change.index}:${change.kind}:${change.sourceStepId ?? ''}:${change.replayStepId ?? ''}`} className="diff-row diff-changed">
                      <td>{change.index + 1}</td>
                      <td className="mono">{change.kind}</td>
                      <td>{change.detail}</td>
                      <td className="mono">{change.sourceStepId ?? '-'}</td>
                      <td className="mono">{change.replayStepId ?? '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
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
