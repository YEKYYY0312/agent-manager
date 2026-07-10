import { useMemo, useState } from 'react';
import type { Step } from './types';
import { filterSteps, fmtMs, fmtUsd, stepIcon } from './trace';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  steps: Step[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function Timeline({ steps, selectedId, onSelect }: Props) {
  const [query, setQuery] = useState('');
  const visibleSteps = useMemo(() => filterSteps(steps, query), [steps, query]);

  return (
    <div className="panel timeline-panel">
      <div className="timeline-header">
        <h2 className="panel-heading">时间线 Timeline</h2>
        <span className="timeline-count">{visibleSteps.length}/{steps.length}</span>
      </div>
      <input
        className="timeline-search"
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索步骤、类型、状态、错误"
        aria-label="搜索 Timeline 步骤"
      />
      {steps.length === 0 && (
        <p className="muted" style={{ fontSize: 11, marginBottom: 8 }}>运行后将在这里展示每个步骤的执行时序。</p>
      )}
      {steps.length > 0 && visibleSteps.length === 0 && (
        <p className="muted" style={{ fontSize: 11, marginBottom: 8 }}>没有匹配的步骤。</p>
      )}
      <div className="step-list">
        {visibleSteps.map((s) => {
          const i = steps.indexOf(s);
          const isError = s.status === 'error' || s.status === 'timeout';
          return (
            <button
              key={s.id}
              type="button"
              className={`step-row ${s.id === selectedId ? 'selected' : ''} ${isError ? `step-row-${s.status}` : ''}`}
              onClick={() => onSelect(s.id)}
            >
              <span className="step-pos">{i + 1}</span>
              <span className={`step-kind kind-${s.type}`}>{stepIcon(s.type)}</span>
              <div className="step-info">
                <span className="step-name">{s.name}</span>
                <span className="step-meta">{s.type}{s.tool?.name ? ` / ${s.tool.name}` : ''}</span>
              </div>
              {isError && s.error && (
                <span className="step-error-hint">{s.error.message}</span>
              )}
              <span className="step-duration">{fmtMs(s.duration_ms)}</span>
              {s.cost && s.cost.total_tokens > 0 && (
                <span className="step-cost">{fmtUsd(s.cost.amount_usd)}</span>
              )}
              <StatusBadge status={s.status} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
