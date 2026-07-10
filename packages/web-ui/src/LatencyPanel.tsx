import type { Step } from './types';
import { fmtMs } from './trace';
import { MetricCard } from './components/MetricCard';
import { DataBar } from './components/DataBar';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  steps: Step[];
}

export function LatencyPanel({ steps }: Props) {
  if (steps.length === 0) {
    return (
      <div className="panel-full">
        <h2 className="panel-heading">延迟 Latency</h2>
        <p className="muted">没有可分析的步骤。</p>
      </div>
    );
  }

  const durations = steps.map(s => s.duration_ms ?? 0).sort((a, b) => a - b);
  const total = durations.reduce((sum, d) => sum + d, 0);
  const avg = total / steps.length;
  const p50 = durations[Math.floor(steps.length * 0.5)] ?? 0;
  const p99 = durations[Math.floor(steps.length * 0.99)] ?? 0;

  const byType = new Map<string, number>();
  for (const s of steps) {
    byType.set(s.type, (byType.get(s.type) ?? 0) + (s.duration_ms ?? 0));
  }

  const slowest = [...steps]
    .sort((a, b) => (b.duration_ms ?? 0) - (a.duration_ms ?? 0))
    .slice(0, 5);

  const failed = steps.filter(s => s.status === 'error' || s.status === 'timeout');
  const maxTypeDur = Math.max(...byType.values(), 1);
  const maxStepDur = slowest[0]?.duration_ms ?? 1;

  return (
    <div className="panel-full">
      <h2 className="panel-heading">延迟 Latency</h2>
      <p className="muted" style={{ fontSize: 11, marginBottom: 12 }}>步骤耗时分布、P50/P99 延迟、异常步骤定位。</p>

      <div className="metrics-row">
        <MetricCard label="总计" value={fmtMs(total)} />
        <MetricCard label="平均" value={fmtMs(avg)} />
        <MetricCard label="P50" value={fmtMs(p50)} />
        <MetricCard label="P99" value={fmtMs(p99)} />
        <MetricCard label="步骤数" value={String(steps.length)} />
      </div>

      <div className="panel-section">
        <h3>按步骤类型分解</h3>
        {[...byType.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([type, dur]) => (
            <DataBar
              key={type}
              label={`${type}  ${fmtMs(dur)}  ${total > 0 ? ((dur / total) * 100).toFixed(0) : '0'}%`}
              value={dur}
              max={maxTypeDur}
            />
          ))}
      </div>

      <div className="panel-section">
        <h3>最慢步骤</h3>
        {slowest.map(s => (
          <div key={s.id} className="bar-row">
            <DataBar
              label={`${s.name}  ${fmtMs(s.duration_ms)}`}
              value={s.duration_ms ?? 0}
              max={maxStepDur}
              kind={s.status !== 'success' ? 'error' : 'default'}
            />
            <StatusBadge status={s.status} />
          </div>
        ))}
      </div>

      {failed.length > 0 && (
        <div className="panel-section">
          <h3>失败 / 超时</h3>
          <div className="error-list">
            {failed.map(s => (
              <div key={s.id} className="error-item">
                <StatusBadge status={s.status} />
                <div className="error-item-body">
                  <span className="error-item-name">{s.name}</span>
                  <span className="error-item-msg">{s.error?.message ?? '--'}</span>
                </div>
                <span className="error-item-dur">{fmtMs(s.duration_ms)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
