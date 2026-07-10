import type { CostSummary } from './types';
import { fmtMs, fmtUsd } from './trace';
import { MetricCard } from './components/MetricCard';
import { DataBar } from './components/DataBar';

interface Props {
  cost: CostSummary;
}

export function CostPanel({ cost }: Props) {
  if (cost.source === 'none') {
    return (
      <div className="panel-full">
        <h2 className="panel-heading">费用 Cost</h2>
        <p className="muted">此 Trace 没有费用数据。在 SDK 中配置模型价格后即可自动计算。</p>
      </div>
    );
  }

  const maxModelTokens = cost.byModel.reduce((m, x) => Math.max(m, x.totalTokens), 0);
  const maxStepCost = cost.expensiveSteps.reduce((m, s) => Math.max(m, s.cost?.amount_usd ?? 0), 0);

  return (
    <div className="panel-full">
      <h2 className="panel-heading">费用 Cost</h2>
      <p className="muted" style={{ fontSize: 11, marginBottom: 12 }}>按模型和步骤维度分解 Token 消耗与费用。</p>

      <div className="metrics-row">
        <MetricCard label="总费用" value={fmtUsd(cost.amountUsd)} kind="accent" />
        <MetricCard label="总 Token 数" value={cost.totalTokens.toLocaleString()} />
        <MetricCard label="输入 Token" value={cost.inputTokens.toLocaleString()} />
        <MetricCard label="输出 Token" value={cost.outputTokens.toLocaleString()} />
        <MetricCard label="计费步骤数" value={String(cost.expensiveSteps.length)} />
      </div>

      {cost.byModel.length > 0 && (
        <div className="panel-section">
          <h3>按模型分解</h3>
          {cost.byModel.map(m => (
            <DataBar
              key={m.model}
              label={`${m.model}  ${m.totalTokens.toLocaleString()}t  ${fmtUsd(m.amountUsd)}`}
              value={m.totalTokens}
              max={maxModelTokens}
              kind="accent"
            />
          ))}
        </div>
      )}

      {cost.expensiveSteps.length > 0 && (
        <div className="panel-section">
          <h3>费用最高步骤</h3>
          {cost.expensiveSteps.map(s => (
            <DataBar
              key={s.id}
              label={`${s.name}  ${fmtUsd(s.cost?.amount_usd ?? 0)}  ${fmtMs(s.duration_ms)}`}
              value={s.cost?.amount_usd ?? 0}
              max={maxStepCost}
              kind="accent"
            />
          ))}
        </div>
      )}
    </div>
  );
}
