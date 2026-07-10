import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ExperimentReport, ExperimentWinner, Trace } from './types';
import type { TraceOption } from './workspace';
import { compareExperiment, fmtMs, fmtUsd } from './trace';
import { MetricCard } from './components/MetricCard';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  baseTrace: Trace;
  basePath: string;
  comparePath: string;
  options: TraceOption[];
  onLoad: (path: string) => Promise<Trace>;
}

export function ExperimentView({ baseTrace, basePath, comparePath, options, onLoad }: Props) {
  const compareOptions = useMemo(() => {
    const otherOptions = options.filter((option) => option.path !== basePath);
    return otherOptions.length > 0 ? otherOptions : options;
  }, [basePath, options]);
  const [selectedPath, setSelectedPath] = useState(comparePath);
  const [report, setReport] = useState<ExperimentReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedPath(comparePath);
    setReport(null);
    setError(null);
  }, [baseTrace.run.id, basePath, comparePath]);

  const compare = useCallback(async (path: string) => {
    setSelectedPath(path);
    setLoading(true);
    setError(null);
    try {
      setReport(compareExperiment(baseTrace, await onLoad(path)));
    } catch (e) {
      setReport(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [baseTrace, onLoad]);

  return (
    <div className="panel-full experiment-view">
      <h2 className="panel-heading">实验对比 Experiment</h2>

      <div className="diff-picker">
        <div className="diff-side">
          <span className="diff-label">A 基准</span>
          <code>{baseTrace.run.id}</code>
          <span className="diff-path">{basePath}</span>
        </div>
        <span className="diff-vs">vs</span>
        <div className="diff-side">
          <span className="diff-label">B 对比</span>
          <div className="inline-controls">
            <select
              aria-label="选择 B Trace"
              value={selectedPath}
              onChange={e => setSelectedPath(e.target.value)}
            >
              {compareOptions.map(option => (
                <option key={option.path} value={option.path}>{option.label}</option>
              ))}
            </select>
            <button type="button" className="btn" onClick={() => compare(selectedPath)}>开始对比</button>
          </div>
        </div>
      </div>

      {loading && <p className="muted experiment-status" role="status">正在计算实验结果...</p>}
      {error && <p className="error-message experiment-status" role="alert">实验对比失败：{error}</p>}

      {!report && !loading && !error && (
        <div className="empty-state">
          <p>选择一个 B Trace，查看两次运行在成功状态、费用、延迟和输出上的差异。</p>
        </div>
      )}

      {report && (
        <>
          <div className="metrics-row">
            <MetricCard
              label="推荐"
              value={<WinnerBadge winner={report.recommendation} />}
              kind={winnerKind(report.recommendation)}
            />
            <MetricCard
              label="成功胜者"
              value={<WinnerBadge winner={report.winnerBySuccess} />}
              kind={winnerKind(report.winnerBySuccess)}
            />
            <MetricCard
              label="费用胜者"
              value={<WinnerBadge winner={report.winnerByCost} />}
              kind={winnerKind(report.winnerByCost)}
            />
            <MetricCard
              label="延迟胜者"
              value={<WinnerBadge winner={report.winnerByLatency} />}
              kind={winnerKind(report.winnerByLatency)}
            />
            <MetricCard
              label="输出变化"
              value={report.delta.outputChanged ? '有变化' : '无变化'}
              kind={report.delta.outputChanged ? 'warn' : 'default'}
            />
          </div>

          <section className="experiment-section">
            <h3>推荐理由</h3>
            <p className="experiment-reason">{recommendationText(report)}</p>
          </section>

          <div className="metrics-row">
            <MetricCard label="步骤数差" value={signed(report.delta.stepCountDelta)} kind={deltaKind(report.delta.stepCountDelta)} />
            <MetricCard label="Token 差" value={signed(report.delta.tokenDelta)} kind={deltaKind(report.delta.tokenDelta)} />
            <MetricCard label="费用差" value={signedUsd(report.delta.costDeltaUsd)} kind={costDeltaKind(report.delta.costDeltaUsd)} />
            <MetricCard label="延迟差" value={signedDuration(report.delta.latencyDeltaMs)} kind={latencyDeltaKind(report.delta.latencyDeltaMs)} />
          </div>

          <section className="experiment-section">
            <h3>运行对照</h3>
            <div className="diff-table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>分组</th>
                    <th>Trace</th>
                    <th>状态</th>
                    <th className="num">失败步骤</th>
                    <th className="num">步骤</th>
                    <th className="num">耗时</th>
                    <th className="num">Token</th>
                    <th className="num">费用</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><WinnerBadge winner="A" /></td>
                    <td className="mono">{report.left.traceId}</td>
                    <td><StatusBadge status={report.left.status} /></td>
                    <td className="num">{report.left.failedSteps}</td>
                    <td className="num">{report.left.stepCount}</td>
                    <td className="num">{fmtMs(report.left.durationMs)}</td>
                    <td className="num">{report.left.totalTokens}</td>
                    <td className="num">{fmtUsd(report.left.costUsd)}</td>
                  </tr>
                  <tr>
                    <td><WinnerBadge winner="B" /></td>
                    <td className="mono">{report.right.traceId}</td>
                    <td><StatusBadge status={report.right.status} /></td>
                    <td className="num">{report.right.failedSteps}</td>
                    <td className="num">{report.right.stepCount}</td>
                    <td className="num">{fmtMs(report.right.durationMs)}</td>
                    <td className="num">{report.right.totalTokens}</td>
                    <td className="num">{fmtUsd(report.right.costUsd)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function WinnerBadge({ winner }: { winner: ExperimentWinner }) {
  return <span className={`winner-badge winner-${winner}`}>{winnerLabel(winner)}</span>;
}

function winnerLabel(winner: ExperimentWinner): string {
  if (winner === 'tie') return '平局';
  return winner;
}

function winnerKind(winner: ExperimentWinner): 'default' | 'accent' {
  return winner === 'tie' ? 'default' : 'accent';
}

function deltaKind(value: number): 'default' | 'warn' {
  return value === 0 ? 'default' : 'warn';
}

function costDeltaKind(value: number): 'default' | 'accent' | 'error' {
  if (value === 0) return 'default';
  return value < 0 ? 'accent' : 'error';
}

function latencyDeltaKind(value: number): 'default' | 'accent' | 'warn' {
  if (value === 0) return 'default';
  return value < 0 ? 'accent' : 'warn';
}

function recommendationText(report: ExperimentReport): string {
  if (report.winnerBySuccess !== 'tie') {
    return `${report.recommendation} 的成功状态更好，优先推荐稳定完成任务的运行。`;
  }
  if (report.recommendation === 'A') return 'A 在费用和/或延迟上更优，可以作为当前更合适的版本。';
  if (report.recommendation === 'B') return 'B 在费用和/或延迟上更优，可以作为当前更合适的版本。';
  return '暂无明确胜者，需要人工继续检查输出质量和业务效果。';
}

function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

function signedDuration(value: number): string {
  return value > 0 ? `+${fmtMs(value)}` : fmtMs(value);
}

function signedUsd(value: number): string {
  return value > 0 ? `+${fmtUsd(value)}` : fmtUsd(value);
}
