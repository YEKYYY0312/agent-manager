interface Props {
  value: number;
  max: number;
  label?: string;
  kind?: 'default' | 'accent' | 'warn' | 'error';
}

const BAR_COLORS: Record<string, string> = {
  default: 'var(--accent-blue)',
  accent: 'var(--accent-green)',
  warn: 'var(--accent-amber)',
  error: 'var(--accent-red)',
};

export function DataBar({ value, max, label, kind = 'default' }: Props) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="data-bar">
      {label && <span className="data-bar-label">{label}</span>}
      <div className="data-bar-track">
        <div
          className={`data-bar-fill data-bar-${kind}`}
          style={{
            width: `${pct}%`,
            backgroundColor: BAR_COLORS[kind],
          }}
        />
      </div>
      <span className="data-bar-value">{pct.toFixed(0)}%</span>
    </div>
  );
}
