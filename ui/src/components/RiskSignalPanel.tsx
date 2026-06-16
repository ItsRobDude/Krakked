import type { MarketRiskSignal } from '../services/api';

export type RiskSignalPanelProps = {
  riskSignal: MarketRiskSignal | null;
};

const formatPercent = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
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

const statusLabel = (riskSignal: MarketRiskSignal | null) => {
  if (!riskSignal) return 'Unavailable';
  if (riskSignal.status === 'insufficient_data') return 'Insufficient data';
  if (riskSignal.status === 'stale_data') return 'Stale';
  if (riskSignal.status === 'pair_unavailable') return 'Pair unavailable';
  if (riskSignal.status === 'error') return 'Unavailable';
  if (riskSignal.risk_level === 'stressed') return 'Stressed';
  if (riskSignal.risk_level === 'elevated') return 'Elevated';
  return 'Normal';
};

const statusPillClass = (riskSignal: MarketRiskSignal | null) => {
  if (!riskSignal) return 'pill--muted';
  if (riskSignal.status === 'stale_data' || riskSignal.status === 'insufficient_data') {
    return 'pill--warning';
  }
  if (riskSignal.status !== 'ready') return 'pill--danger';
  if (riskSignal.risk_level === 'stressed') return 'pill--danger';
  if (riskSignal.risk_level === 'elevated') return 'pill--warning';
  return 'pill--success';
};

export function RiskSignalPanel({ riskSignal }: RiskSignalPanelProps) {
  const note = riskSignal?.notes?.[0] ?? null;
  const tradingEffectLabel = riskSignal?.trading_effect ? 'Trading effect reported' : 'No trading effect';
  const wiringLabel = riskSignal?.runtime_wiring_approved ? 'Runtime wiring approved' : 'Not wired to trading';
  const truthNote = riskSignal?.trading_effect
    ? 'This signal is reported as trading-active. Review runtime wiring before relying on it.'
    : 'Shown for market context only. It does not change strategy selection, sizing, risk limits, or order flow.';

  return (
    <section className="panel risk-signal-panel" aria-label="BTC Risk Signal">
      <div className="panel__header">
        <div>
          <h2>BTC Risk Signal</h2>
          <p className="panel__hint">
            {riskSignal
              ? `${riskSignal.source} ${riskSignal.benchmark_pair} ${riskSignal.timeframe}`
              : 'EWMA context unavailable'}
          </p>
        </div>
        <div className="risk-signal-panel__badges">
          <span className={`pill ${statusPillClass(riskSignal)}`}>{statusLabel(riskSignal)}</span>
          <span className="pill pill--muted">Display only</span>
          <span className={`pill ${riskSignal?.trading_effect ? 'pill--danger' : 'pill--success'}`}>
            {tradingEffectLabel}
          </span>
        </div>
      </div>

      <div className="operator-truth-note">
        <strong>{wiringLabel}</strong>
        <span>{truthNote}</span>
      </div>

      <div className="risk-signal-panel__grid">
        <div className="risk-signal-panel__item">
          <span>Horizon vol</span>
          <strong>{formatPercent(riskSignal?.ewma_horizon_volatility_pct)}</strong>
        </div>
        <div className="risk-signal-panel__item">
          <span>Percentile</span>
          <strong>{formatPercent(riskSignal?.volatility_percentile)}</strong>
        </div>
        <div className="risk-signal-panel__item">
          <span>Bars</span>
          <strong>{riskSignal ? `${riskSignal.bars_used}/${riskSignal.lookback_bars}` : '—'}</strong>
        </div>
        <div className="risk-signal-panel__item">
          <span>Latest bar</span>
          <strong>{formatDateTime(riskSignal?.latest_bar_time)}</strong>
        </div>
      </div>

      <p className="panel__hint">
        {note ?? 'No trading effect.'}
      </p>
    </section>
  );
}

export default RiskSignalPanel;
