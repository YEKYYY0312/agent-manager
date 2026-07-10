import type { ReactNode } from 'react';

interface Props {
  label: string;
  value: ReactNode;
  kind?: 'default' | 'accent' | 'warn' | 'error';
}

const CLASSES: Record<string, string> = {
  default: '',
  accent: 'metric-accent',
  warn: 'metric-warn',
  error: 'metric-error',
};

export function MetricCard({ label, value, kind = 'default' }: Props) {
  return (
    <div className={`metric-card ${CLASSES[kind]}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}
