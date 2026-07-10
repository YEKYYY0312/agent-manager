interface Props {
  title?: string;
  message: string;
}

export function EmptyState({ title, message }: Props) {
  return (
    <div className="empty-state">
      {title && <h3>{title}</h3>}
      <p>{message}</p>
    </div>
  );
}
