import type { Trace } from './types';
import { totalCost, fmtMs, fmtUsd } from './trace';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  trace: Trace;
}

export function SummaryBar({ trace }: Props) {
  const { run, steps } = trace;
  const { totalTokens, costUsd } = totalCost(steps);
  const errors = steps.filter(s => s.status !== 'success').length;

  return (
    <div className="summary-bar">
      <div className="summary-item">
        <span className="summary-label">运行 Run</span>
        <span className="summary-value mono">{run.id}</span>
      </div>
      <div className="summary-item">
        <span className="summary-label">状态</span>
        <StatusBadge status={run.status} />
      </div>
      <div className="summary-item">
        <span className="summary-label">耗时</span>
        <span className="summary-value">{fmtMs(run.duration_ms)}</span>
      </div>
      <div className="summary-item">
        <span className="summary-label">步骤 Steps</span>
        <span className="summary-value">{steps.length}</span>
      </div>
      <div className="summary-item">
        <span className="summary-label">Token 数</span>
        <span className="summary-value">{totalTokens.toLocaleString()}</span>
      </div>
      <div className="summary-item">
        <span className="summary-label">费用</span>
        <span className="summary-value mono">{fmtUsd(costUsd)}</span>
      </div>
      {errors > 0 && (
        <div className="summary-item">
          <span className="summary-label">异常</span>
          <span className="summary-value error">{errors}</span>
        </div>
      )}
    </div>
  );
}
