import type { ReactNode } from 'react';
import type { RiskPresetName, RiskStatus } from '../services/api';

export type RiskPanelProps = {
  status: RiskStatus | null;
  readOnly: boolean;
  onToggle: () => void;
  presetOptions: RiskPresetName[];
  onPresetChange: (name: RiskPresetName) => void;
  presetBusy?: boolean;
  busy?: boolean;
  feedback?: { tone: 'info' | 'error' | 'success'; message: ReactNode } | null;
};

const statusCopy = {
  on: {
    label: 'Kill switch active',
    description: 'Trading is halted until the kill switch is disabled.',
    tone: 'pill--danger',
  },
  off: {
    label: 'Kill switch inactive',
    description: 'Orders may route normally unless other limits block them.',
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
  readOnly,
  onToggle,
  presetOptions,
  onPresetChange,
  presetBusy = false,
  busy = false,
  feedback,
}: RiskPanelProps) {
  const killSwitchState = status ? (status.kill_switch_active ? statusCopy.on : statusCopy.off) : statusCopy.unknown;
  const buttonLabel = status?.kill_switch_active ? 'Disable kill switch' : 'Activate kill switch';
  const buttonDisabled = busy || readOnly || !status;

  return (
    <section className={`panel risk-panel${readOnly ? ' risk-panel--readonly' : ''}`} aria-live="polite">
      <div className="panel__header">
        <h2>Risk controls</h2>
        <span className={`pill ${killSwitchState.tone}`}>{killSwitchState.label}</span>
      </div>
      <p className="panel__description">{killSwitchState.description}</p>

      <div className="field risk-panel__preset">
        <label htmlFor="risk-preset">Portfolio preset</label>
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
              {preset.charAt(0).toUpperCase() + preset.slice(1)}
            </option>
          ))}
        </select>
        <p className="field__hint">Apply a saved profile to risk budgets and strategy aggressiveness.</p>
      </div>

      <div className="risk-panel__controls">
        <div className="risk-panel__meta">
          <p className="risk-panel__label">Kill switch</p>
          <p className="risk-panel__status">{status ? (status.kill_switch_active ? 'Enabled' : 'Disabled') : 'Loading…'}</p>
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
          onClick={onToggle}
        >
          {busy ? 'Updating…' : buttonLabel}
        </button>
      </div>

      {feedback ? <div className={`feedback feedback--${feedback.tone}`}>{feedback.message}</div> : null}
    </section>
  );
}

export default RiskPanel;
