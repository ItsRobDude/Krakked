import type {
  StrategyPerformance,
  StrategyRiskProfile,
  StrategyState,
} from '../services/api';
import { STRATEGY_TAGS } from '../constants/strategies';

const riskProfiles: StrategyRiskProfile[] = ['conservative', 'balanced', 'aggressive'];

const formatTimestamp = (timestamp: string | null) => {
  if (!timestamp) return 'Unknown';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'Unknown';
  return parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};

export type StrategiesPanelProps = {
  strategies: StrategyState[];
  performance: Record<string, StrategyPerformance>;
  riskSelections: Record<string, StrategyRiskProfile>;
  learningSelections: Record<string, boolean>;
  busy: Set<string>;
  readOnly: boolean;
  feedback?: string | null;
  onToggle: (strategyId: string, enabled: boolean) => void;
  onRiskProfileChange: (strategyId: string, profile: StrategyRiskProfile) => void;
  onLearningToggle: (strategyId: string, enabled: boolean) => void;
  mlEnabled: boolean;
  onMlToggle: (enabled: boolean) => void;
};

export function StrategiesPanel({
  strategies,
  performance,
  riskSelections,
  learningSelections,
  busy,
  readOnly,
  feedback,
  onToggle,
  onRiskProfileChange,
  onLearningToggle,
  mlEnabled,
  onMlToggle,
}: StrategiesPanelProps) {
  const formatPnl = (value?: number) => {
    if (value === undefined) return '—';
    return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  const formatExposure = (value?: number) => {
    if (value === undefined) return '—';
    return `${value.toFixed(2)}%`;
  };

  const formatWinRate = (value?: number) => {
    if (value === undefined) return '—';
    return `${Math.round(value * 100)}%`;
  };

  const drawdownBadge = (value?: number) => {
    if (value === undefined) return { label: 'Unknown', tone: 'pill--neutral' as const };
    if (value < 10) return { label: 'OK', tone: 'pill--long' as const };
    if (value < 25) return { label: 'Cooling', tone: 'pill--warning' as const };
    return { label: 'In trouble', tone: 'pill--danger' as const };
  };

  return (
    <section className="panel strategy-panel" aria-live="polite">
      <div className="panel__header">
        <div>
          <h2>Strategies</h2>
          <p className="panel__hint">Toggle live strategies and pick a risk posture</p>
        </div>
        <label className="strategy-toggle__label">
          <input
            type="checkbox"
            className="strategy-toggle"
            checked={mlEnabled}
            disabled={readOnly}
            onChange={(event) => onMlToggle(event.target.checked)}
          />
          <span className="pill pill--info">{mlEnabled ? 'ML: On' : 'ML: Off'}</span>
        </label>
      </div>
      <p className="panel__description">
        Enable or pause each strategy and set its risk profile. Changes respect backend read-only mode.
      </p>

      <div className="table table--strategies" role="table" aria-label="Strategy controls">
        <div className="table__head" role="row">
          <span role="columnheader">Strategy</span>
          <span role="columnheader">Enabled</span>
          <span role="columnheader">Last action</span>
          <span role="columnheader">Exposure</span>
          <span role="columnheader">Realized PnL</span>
          <span role="columnheader">Latest signal</span>
          <span role="columnheader">72h PnL</span>
          <span role="columnheader">Win rate</span>
          <span role="columnheader">Drawdown</span>
          <span role="columnheader">Risk profile</span>
          <span role="columnheader">Learning</span>
        </div>
        <div className="table__body">
          {strategies.map((strategy) => {
            const isBusy = busy.has(strategy.strategy_id);
            const riskProfile = riskSelections[strategy.strategy_id] ?? 'balanced';
            const lastAction = strategy.last_actions_at || strategy.last_intents_at;
            const perf = performance[strategy.strategy_id];
            const drawdown = drawdownBadge(perf?.max_drawdown_pct);
            const latestIntent = strategy.last_intents?.[0];

            return (
              <div key={strategy.strategy_id} className="table__row" role="row">
                <span role="cell" className="strategy__label">
                  <span className="strategy__name">{strategy.strategy_id}</span>
                  {STRATEGY_TAGS[strategy.strategy_id] ? (
                    <span className="pill pill--muted strategy__tag">
                      {STRATEGY_TAGS[strategy.strategy_id]}
                    </span>
                  ) : null}
                </span>
                <span role="cell">
                  <label className="strategy-toggle__label">
                    <input
                      type="checkbox"
                      className="strategy-toggle"
                      checked={strategy.enabled}
                      disabled={readOnly || isBusy}
                      onChange={(event) => onToggle(strategy.strategy_id, event.target.checked)}
                    />
                    <span className="pill pill--info">{strategy.enabled ? 'On' : 'Off'}</span>
                  </label>
                </span>
                <span role="cell" className="strategy__meta">
                  {formatTimestamp(lastAction)}
                </span>
                <span role="cell" className="strategy__meta">
                  {formatExposure(strategy.pnl_summary?.exposure_pct)}
                </span>
                <span role="cell" className="strategy__meta">
                  {formatPnl(strategy.pnl_summary?.realized_pnl_usd)}
                </span>
                <span role="cell" className="strategy__meta" title={latestIntent ? JSON.stringify(latestIntent) : undefined}>
                  {latestIntent
                    ? `${latestIntent.side} ${latestIntent.pair} (${latestIntent.timeframe})`
                    : 'No recent signals'}
                </span>
                <span role="cell" className="strategy__meta">
                  {perf ? formatPnl(perf.realized_pnl_quote) : 'No trades'}
                </span>
                <span role="cell" className="strategy__meta">
                  {perf ? formatWinRate(perf.win_rate) : '—'}
                </span>
                <span role="cell">
                  <span className={`pill ${drawdown.tone}`}>{perf ? drawdown.label : 'No data'}</span>
                </span>
                <span role="cell">
                  <select
                    className="strategy-select"
                    value={riskProfile}
                    onChange={(event) =>
                      onRiskProfileChange(strategy.strategy_id, event.target.value as StrategyRiskProfile)
                    }
                    disabled={readOnly || isBusy}
                  >
                    {riskProfiles.map((profile) => (
                      <option key={profile} value={profile}>
                        {profile}
                      </option>
                    ))}
                  </select>
                </span>
                <span role="cell">
                  {Object.prototype.hasOwnProperty.call(strategy.params ?? {}, 'continuous_learning') ? (
                    <label className="strategy-toggle__label">
                      <input
                        type="checkbox"
                        className="strategy-toggle"
                        checked={learningSelections[strategy.strategy_id] ?? true}
                        disabled={readOnly || isBusy}
                        onChange={(event) => onLearningToggle(strategy.strategy_id, event.target.checked)}
                      />
                      <span className="pill pill--info">
                        {learningSelections[strategy.strategy_id] ?? true ? 'Learning' : 'Paused'}
                      </span>
                    </label>
                  ) : (
                    <span className="strategy__meta">—</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {feedback ? <div className="feedback feedback--info">{feedback}</div> : null}
    </section>
  );
}

export default StrategiesPanel;
