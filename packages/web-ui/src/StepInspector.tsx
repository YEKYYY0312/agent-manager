import type { Step } from './types';
import { fmtMs, fmtUsd } from './trace';
import { StatusBadge } from './components/StatusBadge';
import { EmptyState } from './components/EmptyState';

interface Props {
  step: Step | null;
}

export function StepInspector({ step }: Props) {
  if (!step) {
    return (
      <div className="panel inspector-panel">
        <EmptyState
          message="请从左侧时间线中选择一个步骤查看详情"
          title="步骤检查器 Step Inspector"
        />
      </div>
    );
  }

  const isError = step.status === 'error' || step.status === 'timeout';

  return (
    <div className={`panel inspector-panel ${isError ? 'inspector-error' : ''}`}>
      <div className="inspector-header">
        <div className="inspector-title-row">
          <h2 className="panel-heading">{step.name}</h2>
          <StatusBadge status={step.status} />
        </div>
        <div className="inspector-meta-row">
          <span className="inspector-type">{step.type}</span>
          <span className="inspector-id">ID: {step.id}</span>
        </div>
      </div>

      <dl className="inspector-dl">
        <dt>耗时</dt>
        <dd>{fmtMs(step.duration_ms)}</dd>

        {step.model && (
          <>
            <dt>模型</dt>
            <dd className="mono">{step.model}</dd>
          </>
        )}

        {step.cost && step.cost.total_tokens > 0 && (
          <>
            <dt>Token 数</dt>
            <dd>{step.cost.total_tokens.toLocaleString()} <span className="muted">(输入 {step.cost.input_tokens} / 输出 {step.cost.output_tokens})</span></dd>
            <dt>费用</dt>
            <dd className="mono accent">{fmtUsd(step.cost.amount_usd)}</dd>
          </>
        )}

        {step.tool && (
          <>
            <dt>工具</dt>
            <dd className="mono">{step.tool.name}</dd>
            {step.tool.args && (
              <>
                <dt>参数</dt>
                <dd><pre>{JSON.stringify(step.tool.args, null, 2)}</pre></dd>
              </>
            )}
            {step.tool.result !== undefined && step.tool.result !== null && (
              <>
                <dt>结果</dt>
                <dd><pre>{JSON.stringify(step.tool.result, null, 2)}</pre></dd>
              </>
            )}
          </>
        )}

        {step.input !== undefined && step.input !== null && (
          <>
            <dt>输入</dt>
            <dd><pre>{JSON.stringify(step.input, null, 2)}</pre></dd>
          </>
        )}

        {step.output !== undefined && step.output !== null && !step.tool && (
          <>
            <dt>输出</dt>
            <dd><pre>{JSON.stringify(step.output, null, 2)}</pre></dd>
          </>
        )}

        {step.error && (
          <>
            <dt>错误</dt>
            <dd>
              <div className="error-block">
                <div className="error-type">{step.error.type}</div>
                <div className="error-message">{step.error.message}</div>
                {step.error.stack && <pre className="error-stack">{step.error.stack}</pre>}
              </div>
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}
