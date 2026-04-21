import { useMemo, useState } from 'react';

import type { ReplayLatestSummary } from '../services/api';
import { getReplayTrustBadge, takeImportantWarnings } from '../utils/operatorTrust';

export type ReplaySummaryPanelProps = {
  replay: ReplayLatestSummary | null;
};

const formatCurrency = (value: number | null | undefined) => {
  const formatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  });
  if (typeof value !== 'number' || Number.isNaN(value)) return formatter.format(0);
  return formatter.format(value);
};

const formatPercent = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '0.00%';
  return `${value.toFixed(2)}%`;
};

const formatDateTime = (timestamp: string | null | undefined) => {
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

const formatUtcDateTime = (timestamp: string | null | undefined) => {
  if (!timestamp) return 'Unknown UTC';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'Unknown UTC';
  return parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    timeZoneName: 'short',
  });
};

export function ReplaySummaryPanel({ replay }: ReplaySummaryPanelProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const trustBadge = getReplayTrustBadge(replay);
  const warnings = useMemo(
    () => takeImportantWarnings(replay?.notable_warnings, 2),
    [replay?.notable_warnings],
  );
  const replayInputs = replay?.replay_inputs ?? {};
  const enabledStrategies = Array.isArray(replayInputs.enabled_strategies)
    ? replayInputs.enabled_strategies.filter((value): value is string => typeof value === 'string')
    : [];
  const blockedReasons = Object.entries(replay?.blocked_reason_counts ?? {})
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3);
  const hasReplayWindow =
    typeof replayInputs.start === 'string' && typeof replayInputs.end === 'string';

  return (
    <section className="panel replay-panel" aria-label="Latest replay summary">
      <div className="panel__header replay-panel__header">
        <div>
          <h2>Latest Replay</h2>
          <p className="panel__hint">
            {replay?.available
              ? `Published ${formatDateTime(replay.generated_at)}`
              : 'Publish a replay when you want a quick operator-facing learning snapshot here.'}
          </p>
        </div>
        <div className="replay-panel__controls">
          <span className={trustBadge.className}>{trustBadge.label}</span>
          {replay?.available ? (
            <button
              type="button"
              className="ghost-button"
              onClick={() => setShowAdvanced((current) => !current)}
            >
              {showAdvanced ? 'Hide advanced' : 'Advanced'}
            </button>
          ) : null}
        </div>
      </div>

      {!replay?.available ? (
        <div className="panel__empty replay-panel__empty">
          <p>No published replay is available yet.</p>
          <code className="replay-panel__hint-code">
            poetry run krakked backtest --start 2026-04-01T00:00:00Z --end 2026-04-20T00:00:00Z --publish-latest
          </code>
        </div>
      ) : (
        <div className="replay-panel__content">
          <div className="replay-panel__headline">
            <p className="replay-panel__window">
              {hasReplayWindow
                ? `${formatDateTime(replayInputs.start as string)} to ${formatDateTime(replayInputs.end as string)}`
                : `Published ${formatDateTime(replay.generated_at)}`}
            </p>
            {hasReplayWindow ? (
              <p className="panel__hint">
                UTC window: {formatUtcDateTime(replayInputs.start as string)} to {formatUtcDateTime(replayInputs.end as string)}
              </p>
            ) : null}
            <p className="replay-panel__trust-note">{replay.trust_note || 'Replay trust unavailable.'}</p>
            <p className="panel__hint">Offline replay only. Separate from the live or paper account.</p>
          </div>

          <div className="replay-panel__stats">
            <div className="replay-panel__stat">
              <span className="replay-panel__label">Synthetic replay ending equity</span>
              <strong>{formatCurrency(replay.end_equity_usd)}</strong>
            </div>
            <div className="replay-panel__stat">
              <span className="replay-panel__label">Return</span>
              <strong>{formatPercent(replay.return_pct)}</strong>
            </div>
            <div className="replay-panel__stat">
              <span className="replay-panel__label">Filled orders</span>
              <strong>{typeof replay.fills === 'number' ? replay.fills : '0'}</strong>
            </div>
          </div>

          {warnings.length > 0 ? (
            <div className="replay-panel__warnings">
              {warnings.map((warning) => (
                <p key={warning} className="replay-panel__warning">
                  {warning}
                </p>
              ))}
            </div>
          ) : null}

          {showAdvanced ? (
            <div className="replay-panel__advanced">
              <div className="replay-panel__advanced-grid">
                <div className="replay-panel__advanced-item">
                  <span className="replay-panel__label">Coverage</span>
                  <strong>
                    {replay.coverage_status || 'unknown'} ({replay.usable_series_count ?? 0} usable)
                  </strong>
                  <p className="panel__hint">
                    {replay.missing_series.length} missing, {replay.partial_series.length} partial.
                  </p>
                </div>
                <div className="replay-panel__advanced-item">
                  <span className="replay-panel__label">Cost model</span>
                  <strong>{replay.cost_model || 'Unknown'}</strong>
                  <p className="panel__hint">
                    Fee {String(replayInputs.fee_bps ?? '—')} bps, slippage {String(replayInputs.slippage_bps ?? '—')} bps.
                  </p>
                </div>
                <div className="replay-panel__advanced-item">
                  <span className="replay-panel__label">Enabled strategies</span>
                  <strong>{enabledStrategies.length > 0 ? enabledStrategies.join(', ') : 'Unknown'}</strong>
                  <p className="panel__hint">
                    Generated {formatDateTime(replay.generated_at)} ({formatUtcDateTime(replay.generated_at)}).
                  </p>
                </div>
              </div>

              {blockedReasons.length > 0 ? (
                <div className="replay-panel__advanced-list">
                  <span className="replay-panel__label">Top blocked reasons</span>
                  <ul>
                    {blockedReasons.map(([reason, count]) => (
                      <li key={reason}>
                        {reason} ({count})
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

export default ReplaySummaryPanel;
