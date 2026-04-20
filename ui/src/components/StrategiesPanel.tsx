import { useEffect, useState } from 'react';
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
  liveMode?: boolean;
  feedback?: string | null;
  onToggle: (strategyId: string, enabled: boolean) => void;
  onWeightChange: (strategyId: string, weight: number) => void;
  onRiskProfileChange: (strategyId: string, profile: StrategyRiskProfile) => void;
  onLearningToggle: (strategyId: string, enabled: boolean) => void;
};

export function StrategiesPanel({
  strategies,
  performance,
  riskSelections,
  learningSelections,
  busy,
  readOnly,
  liveMode = false,
  feedback,
  onToggle,
  onWeightChange,
  onRiskProfileChange,
  onLearningToggle,
}: StrategiesPanelProps) {
  const [weightDrafts, setWeightDrafts] = useState<Record<string, number>>({});

  useEffect(() => {
    setWeightDrafts((current) => {
      const next: Record<string, number> = {};
      strategies.forEach((strategy) => {
        next[strategy.strategy_id] = current[strategy.strategy_id] ?? strategy.configured_weight ?? 100;
      });
      return next;
    });
  }, [strategies]);

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

  const momentumBadge = (strategy: StrategyState, realizedPnl?: number, drawdownPct?: number) => {
    if (!strategy.enabled) return { label: 'Paused', tone: 'pill--neutral' as const };
    if (strategy.conflict_summary?.some((entry) => entry.outcome === 'winner')) {
      return { label: 'Leading', tone: 'pill--long' as const };
    }
    if ((drawdownPct ?? 0) >= 25) return { label: 'Under pressure', tone: 'pill--danger' as const };
    if ((realizedPnl ?? 0) < 0 || (drawdownPct ?? 0) >= 10) {
      return { label: 'Cooling', tone: 'pill--warning' as const };
    }
    return { label: 'Stable', tone: 'pill--info' as const };
  };

  const setWeightDraft = (strategyId: string, value: number) => {
    setWeightDrafts((current) => ({ ...current, [strategyId]: value }));
  };

  const commitWeightDraft = (strategyId: string) => {
    const strategy = strategies.find((entry) => entry.strategy_id === strategyId);
    if (!strategy) return;

    const clampedWeight = Math.min(100, Math.max(1, weightDrafts[strategyId] ?? strategy.configured_weight ?? 100));
    const currentWeight = strategy.configured_weight ?? 100;

    if (clampedWeight !== currentWeight) {
      if (liveMode) {
        const confirmed = window.confirm(
          `Change ${strategyId} weight from ${currentWeight} to ${clampedWeight} in the active live session? Weight changes affect effective share and conflict winners immediately.`,
        );
        if (!confirmed) {
          setWeightDraft(strategyId, currentWeight);
          return;
        }
      }

      setWeightDraft(strategyId, clampedWeight);
      onWeightChange(strategyId, clampedWeight);
      return;
    }

    setWeightDraft(strategyId, clampedWeight);
  };

  return (
    <section className="panel strategy-panel" aria-live="polite">
      <div className="panel__header">
        <div>
          <h2>Strategies</h2>
          <p className="panel__hint">Toggle strategies, tune weights, and see which strategies are currently leading or cooling.</p>
        </div>
      </div>
      <p className="panel__description">
        Enable or pause each strategy, choose a simple relative weight, and set its risk profile. The profile-level ML
        switch lives in Startup strategy setup while the session is stopped. Learning here only controls continuous
        training for enabled ML strategies.
      </p>
      {liveMode ? (
        <div className="feedback feedback--warning strategy-panel__warning">
          Live strategy changes apply to the active session immediately. Confirm toggles and committed weight changes
          carefully, because they can change conflict winners and capital share on the fly.
        </div>
      ) : null}

      <div className="table table--strategies" role="table" aria-label="Strategy controls">
        <div className="table__head" role="row">
          <span role="columnheader">Strategy</span>
          <span role="columnheader">Enabled</span>
          <span role="columnheader">Weight</span>
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
            const latestConflict = strategy.conflict_summary?.[0];
            const momentum = momentumBadge(strategy, perf?.realized_pnl_quote, perf?.max_drawdown_pct);

            return (
              <div key={strategy.strategy_id} className="table__row" role="row">
                <span role="cell" className="strategy__label">
                  <span className="strategy__name" title={strategy.strategy_id}>{strategy.label}</span>
                  <span className="strategy__id">{strategy.strategy_id}</span>
                  <span className={`pill ${momentum.tone}`}>{momentum.label}</span>
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
                      title={liveMode
                        ? 'Live change: enabling or disabling this strategy affects the active session immediately.'
                        : 'Toggle this strategy on or off for the current session.'}
                      onChange={(event) => onToggle(strategy.strategy_id, event.target.checked)}
                    />
                    <span className="pill pill--info">{strategy.enabled ? 'On' : 'Off'}</span>
                  </label>
                </span>
                <span role="cell">
                  <div className="strategy__meta" style={{ display: 'grid', gap: '0.35rem' }}>
                    <input
                      type="number"
                      min={1}
                      max={100}
                      value={weightDrafts[strategy.strategy_id] ?? strategy.configured_weight ?? 100}
                      disabled={readOnly || isBusy}
                      title={liveMode
                        ? 'Live change: commit a new weight to change effective share and conflict winners.'
                        : 'Set the relative weight used to calculate this strategy’s effective share.'}
                      onChange={(event) => {
                        const nextWeight = Math.min(100, Math.max(1, Number(event.target.value) || 1));
                        setWeightDraft(strategy.strategy_id, nextWeight);
                        if (!liveMode) {
                          onWeightChange(strategy.strategy_id, nextWeight);
                        }
                      }}
                      onBlur={() => {
                        if (liveMode) {
                          commitWeightDraft(strategy.strategy_id);
                        }
                      }}
                      onKeyDown={(event) => {
                        if (liveMode && event.key === 'Enter') {
                          event.currentTarget.blur();
                        }
                      }}
                    />
                    <span className="pill pill--muted">
                      Share {typeof strategy.effective_weight_pct === 'number'
                        ? `${strategy.effective_weight_pct.toFixed(1)}%`
                        : '—'}
                    </span>
                  </div>
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
                  <div style={{ display: 'grid', gap: '0.35rem' }}>
                    <span>
                      {latestIntent
                        ? `${latestIntent.side} ${latestIntent.pair} (${latestIntent.timeframe})`
                        : 'No recent signals'}
                    </span>
                    <span>
                      {latestConflict
                        ? `Conflict: ${latestConflict.pair} ${latestConflict.winning_reason}`
                        : 'Conflict: clear'}
                    </span>
                  </div>
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
                        title="Per-strategy learning only controls continuous training. It does not replace the global ML master switch in Startup."
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
