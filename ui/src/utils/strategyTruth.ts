import type { StrategyState } from '../services/api';

export type StrategyTradingEffect = {
  label: string;
  className: string;
};

export const getStrategyTradingEffect = (
  strategy: StrategyState,
  liveMode: boolean,
): StrategyTradingEffect => {
  if (!strategy.enabled) {
    return { label: 'No trading effect while paused', className: 'pill pill--neutral' };
  }
  if (strategy.evidence_status === 'data_not_ready') {
    return { label: 'Enabled; data not ready', className: 'pill pill--warning' };
  }
  if (strategy.evidence_status === 'utility') {
    return {
      label: liveMode ? 'Can affect live orders' : 'Can affect paper orders',
      className: 'pill pill--info',
    };
  }
  if (strategy.evidence_status === 'research_stage') {
    return {
      label: liveMode ? 'Can trade; unproven' : 'Can paper-trade; unproven',
      className: 'pill pill--warning',
    };
  }
  return {
    label: liveMode ? 'Can affect live orders' : 'Can affect paper orders',
    className: 'pill pill--warning',
  };
};

export const getStrategyTruthNote = (strategy: StrategyState) => {
  const note = strategy.evidence_note || 'No current evidence label is registered for this strategy.';
  if (!strategy.enabled) {
    return `${note} Paused strategies do not influence order flow.`;
  }
  if (strategy.evidence_status === 'research_stage') {
    return `${note} Being active means it can still influence paper order flow; it is not a profitability endorsement.`;
  }
  if (strategy.evidence_status === 'utility') {
    return `${note} Utility overlays can affect order flow when enabled, but they are not promoted alpha claims.`;
  }
  if (strategy.evidence_status === 'data_not_ready') {
    return `${note} Treat any enabled state as a research setup until required data coverage exists.`;
  }
  return `${note} Treat enabled unreviewed strategies as unproven until an evidence gate promotes them.`;
};
