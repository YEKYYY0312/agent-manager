import { statusColor } from '../trace';

interface Props {
  status: string;
}

const STATUS_LABELS: Record<string, string> = {
  success: '成功',
  error: '错误',
  timeout: '超时',
  cancelled: '已取消',
};

export function StatusBadge({ status }: Props) {
  const label = STATUS_LABELS[status] ?? status;
  return (
    <span
      className={`status-badge status-badge-${status}`}
      style={{ '--status-color': statusColor(status) } as React.CSSProperties}
    >
      {label}
    </span>
  );
}
