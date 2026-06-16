import type { DecisionTrace } from '../services/api';

export type DecisionTracePanelProps = {
  traces: DecisionTrace[];
};

const statusClass: Record<DecisionTrace['status'], string> = {
  orders_sent: 'pill--success',
  risk_blocked: 'pill--warning',
  execution_failed: 'pill--danger',
  no_action: 'pill--muted',
  pending: 'pill--info',
};

const statusLabel: Record<DecisionTrace['status'], string> = {
  orders_sent: 'Orders sent',
  risk_blocked: 'Risk blocked',
  execution_failed: 'Execution failed',
  no_action: 'No action',
  pending: 'Pending',
};

const formatTraceTime = (timestamp: string | null) => {
  if (!timestamp) return 'Unknown';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'Unknown';
  return parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const formatList = (values: string[]) => (values.length > 0 ? values.join(', ') : 'None');

export function DecisionTracePanel({ traces }: DecisionTracePanelProps) {
  return (
    <section className="panel decision-trace-panel" aria-label="Decision Trace">
      <div className="panel__header">
        <div>
          <h2>Decision Trace</h2>
          <p className="panel__hint">Signal, risk, OMS, and order result grouped by execution plan.</p>
        </div>
      </div>

      {traces.length === 0 ? (
        <div className="panel__empty">No decision traces yet.</div>
      ) : (
        <ul className="decision-trace-panel__list">
          {traces.map((trace) => (
            <li key={trace.plan_id} className="decision-trace-panel__item">
              <div className="decision-trace-panel__topline">
                <div>
                  <p className="decision-trace-panel__plan">{trace.plan_id}</p>
                  <p className="decision-trace-panel__summary">{trace.summary}</p>
                </div>
                <span className={`pill ${statusClass[trace.status]}`}>{statusLabel[trace.status]}</span>
              </div>

              <div className="decision-trace-panel__grid">
                <div>
                  <span>Strategies</span>
                  <strong>{formatList(trace.strategy_ids)}</strong>
                </div>
                <div>
                  <span>Pairs</span>
                  <strong>{formatList(trace.pairs)}</strong>
                </div>
                <div>
                  <span>Actions</span>
                  <strong>
                    {trace.allowed_action_count} allowed / {trace.blocked_action_count} blocked
                  </strong>
                </div>
                <div>
                  <span>Orders</span>
                  <strong>
                    {trace.order_count} sent / {trace.filled_order_count} filled
                  </strong>
                </div>
              </div>

              {trace.details.length > 0 ? (
                <ul className="decision-trace-panel__details">
                  {trace.details.map((detail) => (
                    <li key={detail}>{detail}</li>
                  ))}
                </ul>
              ) : null}

              <p className="decision-trace-panel__meta">
                Generated {formatTraceTime(trace.generated_at)}
                {trace.completed_at ? ` - Completed ${formatTraceTime(trace.completed_at)}` : ''}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default DecisionTracePanel;
