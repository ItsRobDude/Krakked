export type ApiEnvelope<T> = {
  data: T | null;
  error: string | null;
};

export type ApiRequestOptions = RequestInit & {
  timeoutMs?: number;
};

export type PortfolioSummary = {
  equity_usd: number | null;
  cash_usd: number | null;
  realized_pnl_usd: number | null;
  unrealized_pnl_usd: number | null;
  drift_flag: boolean | null;
  last_snapshot_ts: string | null;
  portfolio_baseline?: string | null;
  exchange_reference_equity_usd?: number | null;
  exchange_reference_cash_usd?: number | null;
  exchange_reference_checked_at?: string | null;
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
  is_dust: boolean;
  min_order_size?: number | null;
  rounded_close_size?: number | null;
  dust_reason?: string | null;
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
  orders_count?: number; // Optional derived field
};

export type SystemHealth = {
  app_version?: string | null;
  execution_mode?: string | null;
  lifecycle: 'locked' | 'initializing' | 'ready' | 'starting_session' | 'active' | 'stopping_session';
  rest_api_reachable: boolean;
  websocket_connected: boolean;
  streaming_pairs: number;
  stale_pairs: number;
  subscription_errors: number;
  market_data_ok: boolean;
  market_data_status: string;
  market_data_reason?: string | null;
  market_data_detail?: string | null;
  market_data_stale?: boolean;
  market_data_max_staleness?: number | null;
  execution_ok: boolean;
  current_mode: string;
  ui_read_only: boolean;
  kill_switch_active?: boolean | null;
  portfolio_sync_ok: boolean;
  portfolio_sync_reason?: string | null;
  portfolio_last_sync_at?: string | null;
  portfolio_baseline?: string | null;
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

export type ReplayLatestSummary = {
  available: boolean;
  generated_at: string | null;
  trust_level: string | null;
  trust_note: string | null;
  notable_warnings: string[];
  end_equity_usd: number | null;
  pnl_usd: number | null;
  return_pct: number | null;
  fills: number | null;
  blocked_actions: number | null;
  execution_errors: number | null;
  coverage_status: string | null;
  usable_series_count: number | null;
  missing_series: string[];
  partial_series: string[];
  blocked_reason_counts: Record<string, number>;
  cost_model: string | null;
  replay_inputs: Record<string, unknown>;
  report_path?: string | null;
};

export type CockpitPortfolioSnapshot = {
  summary: PortfolioSummary | null;
  exposure: ExposureBreakdown | null;
  positions: PositionPayload[] | null;
};

export type CockpitRiskSnapshot = {
  status: RiskStatus | null;
  config: RiskConfig | null;
};

export type CockpitStrategiesSnapshot = {
  state: StrategyState[] | null;
  performance: StrategyPerformance[] | null;
};

export type CockpitActivitySnapshot = {
  recent_executions: RecentExecution[] | null;
  risk_decisions: RiskDecision[] | null;
};

export type CockpitMarketDataSnapshot = {
  stale_pairs: string[];
  session_pairs: string[];
  watchlist_pairs: string[];
  session_stale_pairs: string[];
  watchlist_stale_pairs: string[];
  global_stale_pairs: string[];
  classification: 'healthy' | 'session_critical' | 'watchlist_only' | 'global_only' | string;
  session_critical: boolean;
  message: string | null;
};

export type CockpitSnapshot = {
  schema_version: string;
  generated_at: string;
  health: SystemHealth | null;
  session: SessionStateResponse | null;
  portfolio: CockpitPortfolioSnapshot | null;
  risk: CockpitRiskSnapshot | null;
  strategies: CockpitStrategiesSnapshot | null;
  activity: CockpitActivitySnapshot | null;
  replay: ReplayLatestSummary | null;
  market_data: CockpitMarketDataSnapshot | null;
  section_errors: Record<string, string>;
};

export type ExecutionMode = 'paper' | 'live';
export type StrategyRiskProfile = 'conservative' | 'balanced' | 'aggressive';
export type RiskPresetName = 'conservative' | 'balanced' | 'aggressive' | 'degen';
export type SessionMode = 'paper' | 'live';

export type StrategyIntentPreview = {
  pair: string;
  side: string;
  intent_type: string;
  desired_exposure_usd: number | null;
  confidence: number;
  timeframe: string;
};

export type StrategyParams = {
  risk_profile?: StrategyRiskProfile | null;
  continuous_learning?: boolean;
  [key: string]: unknown;
};

export type StrategyState = {
  strategy_id: string;
  label: string;
  enabled: boolean;
  last_intents_at: string | null;
  last_actions_at: string | null;
  last_evaluated_at: string | null;
  pnl_summary: { realized_pnl_usd?: number; exposure_pct?: number };
  last_intents?: StrategyIntentPreview[] | null;
  conflict_summary?: Array<{
    pair: string;
    competing_strategies: string[];
    winner_strategy_id: string | null;
    winning_reason: string;
    outcome: 'winner' | 'loser' | 'netted_out';
  }> | null;
  params?: StrategyParams;
  configured_weight: number;
  effective_weight_pct?: number | null;
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

export type SessionStateResponse = {
  active: boolean;
  lifecycle: 'locked' | 'initializing' | 'ready' | 'starting_session' | 'active' | 'stopping_session';
  reloading: boolean;
  mode: SessionMode;
  loop_interval_sec: number;
  profile_name: string | null;
  ml_enabled: boolean;
  emergency_flatten: boolean;
};

export type SessionConfigRequest = {
  profile_name: string;
  mode: SessionMode;
  loop_interval_sec: number;
  // ml_enabled removed
};

export type ProfileSummary = {
  name: string;
  description: string;
};

export type SetupStatus = {
  configured: boolean;
  secrets_exist: boolean;
  unlocked: boolean;
  lifecycle: 'locked' | 'initializing' | 'ready' | 'starting_session' | 'active' | 'stopping_session';
};

export type ExecutionModeUpdate = {
  mode: ExecutionMode;
  reloading?: boolean;
  validate_only?: boolean;
  paper_tests_completed?: boolean;
};

export type ProfileCreateResponse = {
  name: string;
  path: string;
};

const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '');
const API_TOKEN = import.meta.env.VITE_API_TOKEN;

const DEFAULT_API_TIMEOUT_MS = 4000;

function mergeSignals(signal?: AbortSignal | null, timeoutMs = DEFAULT_API_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort('timeout'), timeoutMs);
  const parentSignal = signal ?? undefined;

  const abortFromParent = () => controller.abort(parentSignal?.reason);
  if (parentSignal) {
    if (parentSignal.aborted) {
      controller.abort(parentSignal.reason);
    } else {
      parentSignal.addEventListener('abort', abortFromParent, { once: true });
    }
  }

  const cleanup = () => {
    window.clearTimeout(timeoutId);
    if (parentSignal) {
      parentSignal.removeEventListener('abort', abortFromParent);
    }
  };

  return { signal: controller.signal, cleanup };
}

async function fetchJson<T>(path: string, options: ApiRequestOptions = {}): Promise<T | null> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;
  if (options.headers) Object.assign(headers, options.headers as Record<string, string>);
  const { signal, cleanup } = mergeSignals(options.signal, options.timeoutMs);

  try {
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers, signal });
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
  } finally {
    cleanup();
  }
}

async function fetchJsonStrict<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;
  if (options.headers) Object.assign(headers, options.headers as Record<string, string>);
  const { signal, cleanup } = mergeSignals(options.signal, options.timeoutMs);

  try {
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers, signal });
    const payload = (await response.json()) as ApiEnvelope<T>;

    if (!response.ok) {
      throw new Error(payload.error || `Request failed: ${response.status}`);
    }

    if (payload.error) {
      throw new Error(payload.error);
    }

    if (payload.data === null) {
      throw new Error('Empty response');
    }

    return payload.data;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`Request timed out for ${path}`);
    }
    throw error;
  } finally {
    cleanup();
  }
}

export async function fetchPortfolioSummary(options: ApiRequestOptions = {}): Promise<PortfolioSummary | null> {
  return fetchJson<PortfolioSummary>('/portfolio/summary', options);
}

export async function fetchPositions(options: ApiRequestOptions = {}): Promise<PositionPayload[] | null> {
  return fetchJson<PositionPayload[]>('/portfolio/positions', options);
}

export async function fetchExposure(options: ApiRequestOptions = {}): Promise<ExposureBreakdown | null> {
  return fetchJson<ExposureBreakdown>('/portfolio/exposure', options);
}

export async function fetchRecentExecutions(options: ApiRequestOptions = {}): Promise<RecentExecution[] | null> {
  return fetchJson<RecentExecution[]>('/execution/recent_executions', options);
}

export async function fetchSystemHealth(options: ApiRequestOptions = {}): Promise<SystemHealth | null> {
  return fetchJson<SystemHealth>('/system/health', options);
}

export async function fetchSystemMetrics(): Promise<SystemMetrics | null> {
  return fetchJson<SystemMetrics>('/system/metrics');
}

export async function fetchLatestReplay(options: ApiRequestOptions = {}): Promise<ReplayLatestSummary | null> {
  return fetchJson<ReplayLatestSummary>('/system/replay/latest', options);
}

export async function fetchCockpitSnapshot(options: ApiRequestOptions = {}): Promise<CockpitSnapshot | null> {
  return fetchJson<CockpitSnapshot>('/system/cockpit', options);
}

export async function fetchSessionState(options: ApiRequestOptions = {}): Promise<SessionStateResponse | null> {
  return fetchJson<SessionStateResponse>('/system/session', options);
}

export async function startSession(): Promise<SessionStateResponse | null> {
  return fetchJsonStrict<SessionStateResponse>('/system/session/start', {
    method: 'POST',
  });
}

export async function updateSessionConfig(
  patch: Partial<SessionConfigRequest>,
): Promise<SessionStateResponse | null> {
  return fetchJsonStrict<SessionStateResponse>('/system/session/config', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export async function stopSession(): Promise<SessionStateResponse | null> {
  return fetchJsonStrict<SessionStateResponse>('/system/session/stop', {
    method: 'POST',
  });
}

export async function fetchProfiles(): Promise<ProfileSummary[]> {
  const profiles = await fetchJson<ProfileSummary[]>('/system/profiles');
  return profiles ?? [];
}

export async function fetchStrategies(options: ApiRequestOptions = {}): Promise<StrategyState[] | null> {
  return fetchJson<StrategyState[]>('/strategies', options);
}

export async function fetchStrategyPerformance(options: ApiRequestOptions = {}): Promise<StrategyPerformance[] | null> {
  return fetchJson<StrategyPerformance[]>('/strategies/performance', options);
}

export async function fetchRiskConfig(options: ApiRequestOptions = {}): Promise<RiskConfig | null> {
  return fetchJson<RiskConfig>('/risk/config', options);
}

export async function fetchRiskDecisions(limit = 50, options: ApiRequestOptions = {}): Promise<RiskDecision[] | null> {
  return fetchJson<RiskDecision[]>(`/risk/decisions?limit=${limit}`, options);
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

export async function getRiskStatus(options: ApiRequestOptions = {}): Promise<RiskStatus | null> {
  return fetchJson<RiskStatus>('/risk/status', options);
}

export async function setKillSwitch(active: boolean): Promise<RiskStatus | null> {
  return fetchJson<RiskStatus>('/risk/kill_switch', {
    method: 'POST',
    body: JSON.stringify({ active }),
  });
}

export async function setExecutionMode(
  mode: ExecutionMode,
  password?: string,
  confirmation?: string,
  certifyPaperTestsCompleted = false,
): Promise<ExecutionModeUpdate> {
  return fetchJsonStrict<ExecutionModeUpdate>('/system/mode', {
    method: 'POST',
    body: JSON.stringify({
      mode,
      password,
      confirmation,
      certify_paper_tests_completed: certifyPaperTestsCompleted,
    }),
  });
}

export type ExecutionResultPayload = {
  success: boolean;
  errors: string[];
  warnings: string[];
  orders: unknown[];
};

export async function flattenAllPositions(): Promise<ExecutionResultPayload> {
  const result = await fetchJson<ExecutionResultPayload>('/execution/flatten_all', {
    method: 'POST',
    body: JSON.stringify({ confirmation: 'FLATTEN ALL' }),
  });

  if (result === null) {
    throw new Error('Unable to flatten positions');
  }

  return result;
}

export async function downloadRuntimeConfig(): Promise<Blob | null> {
  const headers: Record<string, string> = {};
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;

  const response = await fetch(`${API_BASE}/config/runtime`, {
    method: 'GET',
    headers,
  });

  if (!response.ok) {
    return null;
  }

  return response.blob();
}

// --- Setup & unlock endpoints ---

export async function fetchSetupStatus(): Promise<SetupStatus> {
  return fetchJsonStrict<SetupStatus>('/system/setup/status');
}

export async function performSetupConfig(region_code: string): Promise<void> {
  await fetchJsonStrict<unknown>('/system/setup/config', {
    method: 'POST',
    body: JSON.stringify({ region_code, universe_pairs: [] }),
  });
}

export async function performSetupCredentials(
  apiKey: string,
  apiSecret: string,
  password: string,
  region: string,
): Promise<void> {
  await fetchJsonStrict<unknown>('/system/setup/credentials', {
    method: 'POST',
    body: JSON.stringify({ apiKey, apiSecret, password, region }),
  });
}

export async function performUnlock(password: string): Promise<void> {
  await fetchJsonStrict<unknown>('/system/setup/unlock', {
    method: 'POST',
    body: JSON.stringify({ password, remember: true }),
  });
}

// --- Profile management ---

export async function createProfile(name: string, description = ''): Promise<ProfileCreateResponse> {
  return fetchJsonStrict<ProfileCreateResponse>('/system/profiles', {
    method: 'POST',
    body: JSON.stringify({ name, description, default_mode: 'paper', base_config: {} }),
  });
}

// --- Config persistence ---

export async function fetchSystemConfig(options: ApiRequestOptions = {}): Promise<Record<string, unknown>> {
  return fetchJsonStrict<Record<string, unknown>>('/system/config', options);
}

export async function applyConfig(config: Record<string, unknown>, dry_run = false): Promise<void> {
  await fetchJsonStrict<unknown>('/config/apply', {
    method: 'POST',
    body: JSON.stringify({ config, dry_run }),
  });
}
