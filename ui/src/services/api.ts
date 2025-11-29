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
  rest_api_reachable: boolean;
  websocket_connected: boolean;
  streaming_pairs: number;
  stale_pairs: number;
  subscription_errors: number;
  market_data_ok: boolean;
  execution_ok: boolean;
  current_mode: string;
  ui_read_only: boolean;
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

export async function getRiskStatus(): Promise<RiskStatus | null> {
  return fetchJson<RiskStatus>('/risk/status');
}

export async function setKillSwitch(active: boolean): Promise<RiskStatus | null> {
  return fetchJson<RiskStatus>('/risk/kill_switch', {
    method: 'POST',
    body: JSON.stringify({ active }),
  });
}
