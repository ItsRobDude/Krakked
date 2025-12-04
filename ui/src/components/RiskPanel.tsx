import type { ReactNode } from 'react';
import type { RiskConfig, RiskPresetName, RiskStatus } from '../services/api';
import { RISK_PRESET_META, formatPresetSummary } from '../constants/riskPresets';

export type RiskPanelProps = {
  status: RiskStatus | null;
  riskConfig?: RiskConfig | null;
  readOnly: boolean;
  onToggle: () => void;
  presetOptions: RiskPresetName[];
  onPresetChange: (name: RiskPresetName) => void;
  presetBusy?: boolean;
  busy?: boolean;
  currentPreset?: RiskPresetName | null;
  feedback?: { tone: 'info' | 'error' | 'success'; message: ReactNode } | null;
};

const statusCopy = {
  on: {
    label: 'Trading paused',
    description: 'Kill switch is active. Orders will not be sent until trading is resumed.',
    tone: 'pill--danger',
  },
  off: {
    label: 'Trading live (subject to mode)',
    description: 'Kill switch is off. Execution still obeys paper/live mode and risk caps.',
    tone: 'pill--long',
  },
  unknown: {
    label: 'Kill switch pending',
    description: 'Waiting for the latest risk status from the backend.',
    tone: 'pill--info',
  },
};

export function RiskPanel({
  status,
  riskConfig,
  readOnly,
  onToggle,
  presetOptions,
  onPresetChange,
  presetBusy = false,
  busy = false,
  currentPreset,
  feedback,
}: RiskPanelProps) {
  const killSwitchState = status ? (status.kill_switch_active ? statusCopy.on : statusCopy.off) : statusCopy.unknown;
  const buttonLabel = status?.kill_switch_active ? 'Resume trading' : 'Pause trading';
  const drawdownLimit = riskConfig?.max_daily_drawdown_pct;
  const drawdownGuard = Boolean(
    status && typeof drawdownLimit === 'number' && status.daily_drawdown_pct >= drawdownLimit,
  );
  const driftGuard = Boolean(status?.drift_flag);
  const isResume = Boolean(status?.kill_switch_active);
  const disableReason = !status
    ? 'Awaiting latest risk status.'
    : readOnly
      ? 'Backend read-only: changes are simulated only.'
      : isResume && drawdownGuard
        ? `Daily drawdown ${status.daily_drawdown_pct.toFixed(1)}% exceeds limit.`
        : isResume && driftGuard
          ? 'Price drift detected. Risk controls locked.'
          : undefined;
  const buttonDisabled = busy || readOnly || !status || (isResume && (drawdownGuard || driftGuard));
  const hotStrategies = status
    ? Object.entries(status.per_strategy_exposure_pct)
        .filter(([, value]) => value > 0)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 5)
    : [];
  const currentPresetMeta = currentPreset ? RISK_PRESET_META[currentPreset] : undefined;

  return (
    <section className={`panel risk-panel${readOnly ? ' risk-panel--readonly' : ''}`} aria-live="polite">
      <div className="panel__header">
        <h2>Risk controls</h2>
        <span className={`pill ${killSwitchState.tone}`}>{killSwitchState.label}</span>
      </div>
      {readOnly ? <p className="panel__hint">Backend read-only: changes are simulated only.</p> : null}
      {status ? (
        <dl className="risk-kpis">
          <div className="risk-kpi">
            <dt>Total exposure</dt>
            <dd>{status.total_exposure_pct.toFixed(1)}%</dd>
          </div>
          <div className="risk-kpi">
            <dt>Manual exposure</dt>
            <dd>{status.manual_exposure_pct.toFixed(1)}%</dd>
          </div>
          <div className="risk-kpi">
            <dt>Daily drawdown</dt>
            <dd>{status.daily_drawdown_pct.toFixed(1)}%</dd>
          </div>
        </dl>
      ) : null}
      <p className="panel__description">{killSwitchState.description}</p>

      <div className="field risk-panel__preset">
        <label className="field__label-row" htmlFor="risk-preset">
          <span>Portfolio preset</span>
          {currentPresetMeta ? (
            <span className="pill pill--muted">{currentPresetMeta.label}</span>
          ) : null}
        </label>
        <select
          id="risk-preset"
          defaultValue=""
          disabled={readOnly || presetBusy}
          onChange={(event) => {
            const value = event.target.value as RiskPresetName | '';
            if (!value) return;
            onPresetChange(value);
            event.currentTarget.value = '';
          }}
        >
          <option value="" disabled>
            Select a preset…
          </option>
          {presetOptions.map((preset) => (
            <option key={preset} value={preset}>
              {RISK_PRESET_META[preset].label}
            </option>
          ))}
        </select>
        <p className="field__hint risk-preset__preview">
          {currentPreset
            ? formatPresetSummary(currentPreset)
            : 'Apply a saved profile to risk budgets and strategy aggressiveness.'}
        </p>
      </div>

      <div className="risk-panel__controls">
        <div className="risk-panel__meta">
          <p className="risk-panel__label">Start / Stop trading</p>
          <p className="risk-panel__status">{status ? (status.kill_switch_active ? 'Paused' : 'Active') : 'Loading…'}</p>
          {readOnly ? <span className="pill pill--warning">Read-only mode</span> : null}
          <p className="risk-panel__hint">
            Toggle requires backend write access and updates alongside other dashboard refreshes.
          </p>
        </div>
        <button
          type="button"
          className="primary-button"
          disabled={buttonDisabled}
          aria-busy={busy}
          title={disableReason}
          onClick={onToggle}
        >
          {busy ? 'Updating…' : buttonLabel}
        </button>
      </div>

      {hotStrategies.length > 0 ? (
        <div className="risk-panel__list">
          <h3>Top strategy exposure</h3>
          <ul>
            {hotStrategies.map(([strategyId, pct]) => (
              <li key={strategyId} className="risk-panel__list-item">
                <span>{strategyId}</span>
                <span>{pct.toFixed(1)}%</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {feedback ? <div className={`feedback feedback--${feedback.tone}`}>{feedback.message}</div> : null}
    </section>
  );
}

export default RiskPanel;
