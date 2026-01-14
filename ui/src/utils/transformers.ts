import {
  PositionPayload,
  ExposureBreakdown,
  RecentExecution,
  RiskDecision,
} from '../services/api';
import { PositionRow } from '../components/PositionsTable';
import { WalletRow } from '../components/WalletTable';
import { LogEntry } from '../components/LogPanel';
import { formatCurrency, formatPercent, formatTimestamp } from './formatters';

export const transformPositions = (payload: PositionPayload[]): PositionRow[] =>
  payload.map((position) => {
    const side: PositionRow['side'] = position.base_size < 0 ? 'short' : 'long';
    const size = `${Math.abs(position.base_size).toFixed(4)} ${position.base_asset}`;
    const entry = position.avg_entry_price
      ? formatCurrency(position.avg_entry_price)
      : '—';
    const mark = position.current_price
      ? formatCurrency(position.current_price)
      : '—';
    const pnlValue = position.unrealized_pnl_usd ?? 0;
    const pnl = pnlValue === 0 ? '$0.00' : formatCurrency(pnlValue);

    let status = position.strategy_tag || 'Tracking';
    if (position.is_dust) {
      status = 'Dust';
    }

    return { pair: position.pair, side, size, entry, mark, pnl, status };
  });

export const transformBalances = (exposure: ExposureBreakdown): WalletRow[] =>
  exposure.by_asset.map((asset) => ({
    asset: asset.asset,
    total: formatPercent(asset.pct_of_equity || 0),
    available: '—',
    valueUsd: formatCurrency(asset.value_usd || 0),
  }));

export const transformLogs = (executions: RecentExecution[]): LogEntry[] =>
  executions.map((execution) => {
    const source =
      execution.errors[0] || execution.warnings[0] || 'Execution summary';
    const completedAt = execution.completed_at || execution.started_at;
    const timestamp = formatTimestamp(completedAt);
    const message = `${execution.plan_id} ${
      execution.success ? 'succeeded' : 'failed'
    } (${execution.orders.length} orders)`;
    const level: LogEntry['level'] = execution.success ? 'info' : 'error';

    return {
      level,
      message,
      timestamp,
      source,
      sortKey: completedAt ? new Date(completedAt).getTime() : undefined,
    };
  });

const getDecisionReasons = (decision: RiskDecision): string => {
  if (decision.block_reasons.length > 0) {
    return ` (${decision.block_reasons.join(', ')})`;
  }
  if (decision.kill_switch_active) {
    return ' (Kill switch active)';
  }
  return '';
};

export const transformRiskDecisions = (decisions: RiskDecision[]): LogEntry[] =>
  decisions
    .filter((decision) => decision.blocked || decision.kill_switch_active)
    .map((decision) => {
      const timestamp = formatTimestamp(decision.decided_at);
      const reasons = getDecisionReasons(decision);

      const status = decision.blocked ? 'blocked' : 'allowed';
      const message = `${decision.pair}: ${status} ${decision.action_type}${reasons}`;
      const level: LogEntry['level'] = decision.blocked ? 'warning' : 'info';

      return {
        level,
        message,
        timestamp,
        source: decision.strategy_id || 'Risk',
        sortKey: new Date(decision.decided_at).getTime(),
      };
    });
