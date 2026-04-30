export type LogEntry = {
  level: 'info' | 'warning' | 'error';
  message: string;
  timestamp: string;
  source?: string;
  details?: string[];
  sortKey?: number;
};

export type LogPanelProps = {
  entries: LogEntry[];
  title?: string;
  hint?: string;
};

const levelToBadge: Record<LogEntry['level'], string> = {
  info: 'pill--neutral',
  warning: 'pill--warning',
  error: 'pill--danger',
};

export function LogPanel({
  entries,
  title = 'Recent Logs',
  hint = 'Feed from the trading runtime',
}: LogPanelProps) {
  return (
    <div className="panel log-panel">
      <div className="panel__header">
        <h2>{title}</h2>
        <p className="panel__hint">{hint}</p>
      </div>
      {entries.length === 0 ? (
        <div className="panel__empty">No recent activity yet.</div>
      ) : (
        <ul className="log-panel__list" aria-live="polite">
          {entries.map((entry) => (
            <li key={entry.timestamp + entry.message} className="log-panel__item">
              <div>
                <p className="log-panel__message">{entry.message}</p>
                {entry.details && entry.details.length > 0 ? (
                  <ul className="log-panel__details">
                    {entry.details.map((detail) => (
                      <li key={detail}>{detail}</li>
                    ))}
                  </ul>
                ) : null}
                <p className="log-panel__meta">
                  <span className={`pill ${levelToBadge[entry.level]}`}>{entry.level}</span>
                  {entry.source ? <span className="log-panel__source">{entry.source}</span> : null}
                </p>
              </div>
              <time className="log-panel__time">{entry.timestamp}</time>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default LogPanel;
