import type { ReactNode } from 'react';
import type { RiskStatus } from '../services/api';

export type RiskPanelProps = {
  status: RiskStatus | null;
  readOnly: boolean;
  onToggle: () => void;
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

export function RiskPanel({ status, readOnly, onToggle, busy = false, feedback }: RiskPanelProps) {
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
