export type LogEntry = {
  level: 'info' | 'warning' | 'error';
  message: string;
  timestamp: string;
  source?: string;
};

export type LogPanelProps = {
  entries: LogEntry[];
};

const levelToBadge: Record<LogEntry['level'], string> = {
  info: 'pill--neutral',
  warning: 'pill--warning',
  error: 'pill--danger',
};

export function LogPanel({ entries }: LogPanelProps) {
  return (
    <div className="panel log-panel">
      <div className="panel__header">
        <h2>Recent Logs</h2>
        <p className="panel__hint">Feed from the bot process</p>
      </div>
      <ul className="log-panel__list" aria-live="polite">
        {entries.map((entry) => (
          <li key={entry.timestamp + entry.message} className="log-panel__item">
            <div>
              <p className="log-panel__message">{entry.message}</p>
              <p className="log-panel__meta">
                <span className={`pill ${levelToBadge[entry.level]}`}>{entry.level}</span>
                {entry.source ? <span className="log-panel__source">{entry.source}</span> : null}
              </p>
            </div>
            <time className="log-panel__time">{entry.timestamp}</time>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default LogPanel;
