export type ApiEnvelope<T> = {
  data: T | null;
  error: string | null;
};

export type PortfolioSummary = {
  equity_usd: number | null;
  cash_usd: number | null;
  realized_pnl_usd: number | null;
  unrealized_pnl_usd: number | null;
  drift_flag: boolean | null;
  last_snapshot_ts: string | null;
};

export type PositionPayload = {
  pair: string;
  base_asset: string;
  base_size: number;
  avg_entry_price: number | null;
  current_price: number | null;
  value_usd: number | null;
  unrealized_pnl_usd: number | null;
  strategy_tag?: string | null;
};

export type ExposureBreakdown = {
  by_asset: Array<{ asset: string; value_usd: number | null; pct_of_equity: number | null }>;
  by_strategy: Array<{ strategy_id: string; value_usd: number | null; pct_of_equity: number | null }>;
};

export type RiskStatus = {
  kill_switch_active: boolean;
  daily_drawdown_pct: number;
  drift_flag: boolean;
  total_exposure_pct: number;
  manual_exposure_pct: number;
  per_asset_exposure_pct: Record<string, number>;
  per_strategy_exposure_pct: Record<string, number>;
};

export type RiskDecision = {
  decided_at: string;
  plan_id: string;
  strategy_id?: string | null;
  pair: string;
  action_type: string;
  blocked: boolean;
  block_reasons: string[];
  kill_switch_active: boolean;
};

export type RiskConfig = {
  max_risk_per_trade_pct: number;
  max_portfolio_risk_pct: number;
  max_open_positions: number;
  max_per_asset_pct: number;
  max_per_strategy_pct: Record<string, number>;
  max_daily_drawdown_pct: number;
  kill_switch_on_drift: boolean;
  include_manual_positions: boolean;
  volatility_lookback_bars: number;
  min_liquidity_24h_usd: number;
  dynamic_allocation_enabled: boolean;
  dynamic_allocation_lookback_hours: number;
  min_strategy_weight_pct: number;
  max_strategy_weight_pct: number;
};

export type RecentExecution = {
  plan_id: string;
  started_at: string;
  completed_at: string | null;
  success: boolean;
  orders: Array<{
    pair: string;
    side: string;
    requested_base_size: number;
    requested_price: number | null;
    status: string;
    created_at: string;
  }>;
  errors: string[];
  warnings: string[];
};

export type SystemHealth = {
  app_version?: string | null;
  execution_mode?: string | null;
  rest_api_reachable: boolean;
  websocket_connected: boolean;
  streaming_pairs: number;
  stale_pairs: number;
  subscription_errors: number;
  market_data_ok: boolean;
  market_data_status: string;
  market_data_reason?: string | null;
  market_data_stale?: boolean;
  market_data_max_staleness?: number | null;
  execution_ok: boolean;
  current_mode: string;
  ui_read_only: boolean;
  kill_switch_active?: boolean | null;
  drift_detected: boolean;
  drift_reason?: string | null;
};

export type SystemMetrics = {
  plans_generated: number;
  plans_executed: number;
  blocked_actions: number;
  execution_errors: number;
  market_data_errors: number;
  recent_errors: Array<{ at: string; message: string }>;
  last_equity_usd: number | null;
  last_realized_pnl_usd: number | null;
  last_unrealized_pnl_usd: number | null;
  open_orders_count: number;
  open_positions_count: number;
  drift_detected: boolean;
  drift_reason: string | null;
  market_data_ok: boolean;
  market_data_stale: boolean;
  market_data_reason: string | null;
  market_data_max_staleness: number | null;
};

export type ExecutionMode = 'paper' | 'live';
export type StrategyRiskProfile = 'conservative' | 'balanced' | 'aggressive';
export type RiskPresetName = 'conservative' | 'balanced' | 'aggressive' | 'degen';

export type StrategyIntentPreview = {
  pair: string;
  side: string;
  intent_type: string;
  desired_exposure_usd: number | null;
  confidence: number;
  timeframe: string;
};

export type StrategyState = {
  strategy_id: string;
  enabled: boolean;
  last_intents_at: string | null;
  last_actions_at: string | null;
  pnl_summary: { realized_pnl_usd?: number; exposure_pct?: number };
  last_intents?: StrategyIntentPreview[] | null;
  params?: { risk_profile?: StrategyRiskProfile | null };
};

export type StrategyPerformance = {
  strategy_id: string;
  realized_pnl_quote: number;
  window_start: string;
  window_end: string;
  trade_count: number;
  win_rate: number;
  max_drawdown_pct: number;
};

const API_BASE = import.meta.env.VITE_API_BASE || '/api';
const API_TOKEN = import.meta.env.VITE_API_TOKEN;

async function fetchJson<T>(path: string, options: RequestInit = {}): Promise<T | null> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;
  if (options.headers) Object.assign(headers, options.headers as Record<string, string>);

  try {
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }

    const payload = (await response.json()) as ApiEnvelope<T>;
    if (payload.error) {
      throw new Error(payload.error);
    }

    return payload.data;
  } catch (error) {
    console.warn(`Falling back to placeholders for ${path}`, error);
    return null;
  }
}

export async function fetchPortfolioSummary(): Promise<PortfolioSummary | null> {
  return fetchJson<PortfolioSummary>('/portfolio/summary');
}

export async function fetchPositions(): Promise<PositionPayload[] | null> {
  return fetchJson<PositionPayload[]>('/portfolio/positions');
}

export async function fetchExposure(): Promise<ExposureBreakdown | null> {
  return fetchJson<ExposureBreakdown>('/portfolio/exposure');
}

export async function fetchRecentExecutions(): Promise<RecentExecution[] | null> {
  return fetchJson<RecentExecution[]>('/execution/recent_executions');
}

export async function fetchSystemHealth(): Promise<SystemHealth | null> {
  return fetchJson<SystemHealth>('/system/health');
}

export async function fetchSystemMetrics(): Promise<SystemMetrics | null> {
  return fetchJson<SystemMetrics>('/system/metrics');
}

export async function fetchStrategies(): Promise<StrategyState[] | null> {
  return fetchJson<StrategyState[]>('/strategies');
}

export async function fetchStrategyPerformance(): Promise<StrategyPerformance[] | null> {
  return fetchJson<StrategyPerformance[]>('/strategies/performance');
}

export async function fetchRiskConfig(): Promise<RiskConfig | null> {
  return fetchJson<RiskConfig>('/risk/config');
}

export async function fetchRiskDecisions(limit = 50): Promise<RiskDecision[] | null> {
  return fetchJson<RiskDecision[]>(`/risk/decisions?limit=${limit}`);
}

export async function updateRiskConfig(patch: Partial<RiskConfig>): Promise<RiskConfig | null> {
  return fetchJson<RiskConfig>('/risk/config', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export async function applyRiskPreset(name: RiskPresetName): Promise<RiskConfig | null> {
  return fetchJson<RiskConfig>(`/risk/preset/${name}`, { method: 'POST' });
}

export async function setStrategyEnabled(id: string, enabled: boolean): Promise<void> {
  const result = await fetchJson<unknown>(`/strategies/${id}/enabled`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });

  if (result === null) {
    throw new Error('Unable to update strategy state');
  }
}

export async function patchStrategyConfig(id: string, patch: Record<string, unknown>): Promise<void> {
  const result = await fetchJson<unknown>(`/strategies/${id}/config`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });

  if (result === null) {
    throw new Error('Unable to update strategy configuration');
  }
}

export function getAppVersion(health: SystemHealth | null): string | null {
  return health?.app_version ?? null;
}

export function getExecutionMode(health: SystemHealth | null): ExecutionMode | null {
  const mode = health?.execution_mode ?? health?.current_mode ?? null;
  return mode === 'paper' || mode === 'live' ? mode : null;
}

export function getKillSwitchState(health: SystemHealth | null): boolean | null {
  return health?.kill_switch_active ?? null;
}

export async function getRiskStatus(): Promise<RiskStatus | null> {
  return fetchJson<RiskStatus>('/risk/status');
}

export async function setKillSwitch(active: boolean): Promise<RiskStatus | null> {
  return fetchJson<RiskStatus>('/risk/kill_switch', {
    method: 'POST',
    body: JSON.stringify({ active }),
  });
}

export async function setExecutionMode(
  mode: ExecutionMode,
): Promise<{ mode: ExecutionMode; validate_only: boolean }> {
  const result = await fetchJson<{ mode: ExecutionMode; validate_only: boolean }>('/system/mode', {
    method: 'POST',
    body: JSON.stringify({ mode }),
  });

  if (result === null) {
    throw new Error('Unable to update execution mode');
  }

  return result;
}

export async function flattenAllPositions(): Promise<void> {
  const result = await fetchJson<unknown>('/execution/flatten_all', {
    method: 'POST',
  });

  if (result === null) {
    throw new Error('Unable to flatten positions');
  }
}
