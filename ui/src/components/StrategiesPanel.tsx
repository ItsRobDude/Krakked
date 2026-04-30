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

      <div className="strategy-card-list" aria-label="Strategy controls">
        {strategies.map((strategy) => {
          const isBusy = busy.has(strategy.strategy_id);
          const riskProfile = riskSelections[strategy.strategy_id] ?? 'balanced';
          const lastAction = strategy.last_actions_at || strategy.last_intents_at;
          const perf = performance[strategy.strategy_id];
          const drawdown = drawdownBadge(perf?.max_drawdown_pct);
          const latestIntent = strategy.last_intents?.[0];
          const latestConflict = strategy.conflict_summary?.[0];
          const momentum = momentumBadge(strategy, perf?.realized_pnl_quote, perf?.max_drawdown_pct);
          const learningEnabled = learningSelections[strategy.strategy_id] ?? true;

          return (
            <article key={strategy.strategy_id} className="strategy-control-card">
              <div className="strategy-control-card__header">
                <div>
                  <h3 className="strategy-control-card__title">{strategy.label}</h3>
                  <p className="strategy-control-card__id">{strategy.strategy_id}</p>
                </div>
                <div className="strategy-control-card__badges">
                  <span className={`pill ${strategy.enabled ? 'pill--long' : 'pill--neutral'}`}>
                    {strategy.enabled ? 'Active' : 'Paused'}
                  </span>
                  <span className={`pill ${momentum.tone}`}>{momentum.label}</span>
                  {STRATEGY_TAGS[strategy.strategy_id] ? (
                    <span className="pill pill--muted strategy__tag">
                      {STRATEGY_TAGS[strategy.strategy_id]}
                    </span>
                  ) : null}
                </div>
              </div>

              <div className="strategy-control-card__metrics">
                <div>
                  <span>Effective share</span>
                  <strong>{typeof strategy.effective_weight_pct === 'number' ? `${strategy.effective_weight_pct.toFixed(1)}%` : '—'}</strong>
                </div>
                <div>
                  <span>Exposure</span>
                  <strong>{formatExposure(strategy.pnl_summary?.exposure_pct)}</strong>
                </div>
                <div>
                  <span>Realized PnL</span>
                  <strong>{formatPnl(strategy.pnl_summary?.realized_pnl_usd)}</strong>
                </div>
                <div>
                  <span>72h PnL</span>
                  <strong>{perf ? formatPnl(perf.realized_pnl_quote) : 'No trades'}</strong>
                </div>
                <div>
                  <span>Win rate</span>
                  <strong>{perf ? formatWinRate(perf.win_rate) : '—'}</strong>
                </div>
                <div>
                  <span>Drawdown</span>
                  <strong><span className={`pill ${drawdown.tone}`}>{perf ? drawdown.label : 'No data'}</span></strong>
                </div>
              </div>

              <div className="strategy-control-card__signals">
                <div>
                  <span>Latest signal</span>
                  <strong title={latestIntent ? JSON.stringify(latestIntent) : undefined}>
                    {latestIntent
                      ? `${latestIntent.side} ${latestIntent.pair} (${latestIntent.timeframe})`
                      : 'No recent signals'}
                  </strong>
                </div>
                <div>
                  <span>Conflict</span>
                  <strong>
                    {latestConflict
                      ? `${latestConflict.pair}: ${latestConflict.winning_reason}`
                      : 'Clear'}
                  </strong>
                </div>
                <div>
                  <span>Freshness</span>
                  <strong>{formatTimestamp(lastAction)}</strong>
                </div>
              </div>

              <div className="strategy-control-card__configure" aria-label={`${strategy.label} configuration`}>
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
                <label className="strategy-control-card__field">
                  <span>Weight</span>
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
                </label>
                <label className="strategy-control-card__field">
                  <span>Risk profile</span>
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
                </label>
                {Object.prototype.hasOwnProperty.call(strategy.params ?? {}, 'continuous_learning') ? (
                  <label className="strategy-toggle__label">
                    <input
                      type="checkbox"
                      className="strategy-toggle"
                      checked={strategy.enabled && learningEnabled}
                      disabled={readOnly || isBusy || !strategy.enabled}
                      title="Per-strategy learning only controls continuous training. It does not replace the global ML master switch in Startup."
                      onChange={(event) => onLearningToggle(strategy.strategy_id, event.target.checked)}
                    />
                    <span className="pill pill--info">
                      {strategy.enabled && learningEnabled ? 'Learning' : 'Paused'}
                    </span>
                  </label>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>

      {feedback ? <div className="feedback feedback--info">{feedback}</div> : null}
    </section>
  );
}

export default StrategiesPanel;
