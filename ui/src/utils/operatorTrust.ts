import type { CockpitMarketDataSnapshot, ReplayLatestSummary, SystemHealth } from '../services/api';

export type TrustBadge = {
  label: string;
  className: string;
};

export type RuntimeTrust = {
  label: string;
  sidebarTone: 'ok' | 'warning' | 'danger';
  hint: string;
};

export const takeImportantWarnings = (warnings: string[] | null | undefined, limit = 2) =>
  (warnings ?? []).filter(Boolean).slice(0, limit);

export const getReplayTrustBadge = (
  replay: Pick<ReplayLatestSummary, 'available' | 'trust_level'> | null,
): TrustBadge => {
  if (!replay?.available) {
    return { label: 'No replay yet', className: 'pill pill--muted' };
  }

  switch (replay.trust_level) {
    case 'decision_helpful':
      return { label: 'Decision-helpful', className: 'pill pill--success' };
    case 'limited':
      return { label: 'Limited signal', className: 'pill pill--warning' };
    case 'weak_signal':
      return { label: 'Weak signal', className: 'pill pill--danger' };
    default:
      return { label: 'Replay signal', className: 'pill pill--muted' };
  }
};

export const getRuntimeTrust = (
  health: SystemHealth | null,
  _connectionState: 'connected' | 'degraded',
  marketData?: CockpitMarketDataSnapshot | null,
): RuntimeTrust => {
  if (!health) {
    return {
      label: 'Needs attention',
      sidebarTone: 'warning',
      hint: 'Runtime health is unavailable. Showing the last successful state where possible.',
    };
  }

  if (health.lifecycle === 'initializing' || health.market_data_status === 'warming_up') {
    return {
      label: 'Warming up',
      sidebarTone: 'warning',
      hint: 'Krakked is online but still building fresh startup state.',
    };
  }

  const marketDataSessionOk = health.market_data_ok || Boolean(
    marketData &&
      !marketData.session_critical &&
      (marketData.classification === 'watchlist_only' || marketData.classification === 'global_only'),
  );
  const coreRuntimeOk =
    health.portfolio_sync_ok &&
    health.execution_ok &&
    marketDataSessionOk &&
    !health.drift_detected &&
    !health.kill_switch_active;

  if (coreRuntimeOk) {
    return {
      label: 'Healthy',
      sidebarTone: 'ok',
      hint: marketData?.message
        ? `Session-critical checks look good. ${marketData.message}`
        : 'Portfolio sync, market data, and execution checks look good.',
    };
  }

  if (health.kill_switch_active) {
    return {
      label: 'Paused',
      sidebarTone: 'danger',
      hint: 'Trading is paused by the kill switch.',
    };
  }

  return {
    label: 'Needs attention',
    sidebarTone: 'warning',
    hint:
      health.portfolio_sync_reason ||
      health.market_data_detail ||
      health.market_data_reason ||
      'One or more runtime checks are degraded.',
  };
};
