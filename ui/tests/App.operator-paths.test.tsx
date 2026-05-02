import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type {
  CockpitSnapshot,
  ExecutionResultPayload,
  RiskConfig,
  RiskStatus,
  SessionStateResponse,
  StrategyState,
  SystemHealth,
} from '../src/services/api';

const apiMocks = vi.hoisted(() => ({
  fetchCockpitSnapshot: vi.fn(),
  fetchSystemHealth: vi.fn(),
  fetchSessionState: vi.fn(),
  fetchProfiles: vi.fn(),
  fetchStrategies: vi.fn(),
  fetchStrategyPerformance: vi.fn(),
  applyRiskPreset: vi.fn(),
  getRiskStatus: vi.fn(),
  fetchSetupStatus: vi.fn(),
  updateRiskConfig: vi.fn(),
  setKillSwitch: vi.fn(),
  patchStrategyConfig: vi.fn(),
  setStrategyEnabled: vi.fn(),
  setExecutionMode: vi.fn(),
  createProfile: vi.fn(),
  fetchSystemConfig: vi.fn(),
  applyConfig: vi.fn(),
  startSession: vi.fn(),
  stopSession: vi.fn(),
  flattenAllPositions: vi.fn(),
  downloadRuntimeConfig: vi.fn(),
  updateSessionConfig: vi.fn(),
}));

vi.mock('../src/services/api', () => apiMocks);

import App from '../src/App';

const activeSession: SessionStateResponse = {
  active: true,
  lifecycle: 'active',
  reloading: false,
  mode: 'paper',
  loop_interval_sec: 15,
  profile_name: 'Rob',
  ml_enabled: true,
  emergency_flatten: false,
};

const healthyHealth: SystemHealth = {
  app_version: '0.1.0',
  execution_mode: 'paper',
  lifecycle: 'active',
  rest_api_reachable: true,
  websocket_connected: true,
  streaming_pairs: 4,
  stale_pairs: 0,
  subscription_errors: 0,
  market_data_ok: true,
  market_data_status: 'streaming',
  market_data_reason: 'streaming',
  market_data_detail: '4 pairs streaming',
  market_data_stale: false,
  market_data_max_staleness: 1,
  execution_ok: true,
  current_mode: 'paper',
  ui_read_only: false,
  kill_switch_active: false,
  portfolio_sync_ok: true,
  portfolio_last_sync_at: '2026-05-02T03:00:00Z',
  portfolio_baseline: 'paper_wallet',
  drift_detected: false,
  drift_reason: null,
};

const healthyRisk: RiskStatus = {
  kill_switch_active: false,
  daily_drawdown_pct: 0,
  drift_flag: false,
  total_exposure_pct: 6.2,
  manual_exposure_pct: 0,
  per_asset_exposure_pct: { USD: 93.8, BTC: 3.1, ETH: 3.1 },
  per_strategy_exposure_pct: { dca_overlay: 6.2 },
};

const riskConfig: RiskConfig = {
  max_risk_per_trade_pct: 1,
  max_portfolio_risk_pct: 10,
  max_open_positions: 10,
  max_per_asset_pct: 5,
  max_per_strategy_pct: { dca_overlay: 20, trend_core: 40 },
  max_daily_drawdown_pct: 10,
  kill_switch_on_drift: true,
  include_manual_positions: true,
  volatility_lookback_bars: 20,
  min_liquidity_24h_usd: 100000,
  dynamic_allocation_enabled: false,
  dynamic_allocation_lookback_hours: 72,
  min_strategy_weight_pct: 0,
  max_strategy_weight_pct: 50,
};

const strategy = (overrides: Partial<StrategyState>): StrategyState => ({
  strategy_id: 'dca_overlay',
  label: 'DCA Overlay',
  enabled: true,
  last_intents_at: '2026-05-02T03:00:00Z',
  last_actions_at: null,
  last_evaluated_at: '2026-05-02T03:00:00Z',
  current_positions: [],
  pnl_summary: { realized_pnl_usd: 0, exposure_pct: 6.2 },
  last_intents: null,
  conflict_summary: null,
  params: { pairs: ['BTC/USD', 'ETH/USD'] },
  configured_weight: 100,
  effective_weight_pct: 50,
  ...overrides,
});

const baseStrategies = [
  strategy({}),
  strategy({
    strategy_id: 'trend_core',
    label: 'Trend Core',
    pnl_summary: { realized_pnl_usd: 0, exposure_pct: 0 },
    params: { risk_profile: 'balanced', pairs: [] },
  }),
  strategy({
    strategy_id: 'ai_predictor',
    label: 'AI Predictor',
    enabled: false,
    last_intents_at: null,
    last_evaluated_at: null,
    pnl_summary: { realized_pnl_usd: 0, exposure_pct: 0 },
    params: { continuous_learning: true, risk_profile: 'balanced', pairs: [] },
    effective_weight_pct: null,
  }),
];

const buildCockpit = (overrides: Partial<CockpitSnapshot> = {}): CockpitSnapshot => ({
  schema_version: 'cockpit.v1',
  generated_at: '2026-05-02T03:01:00Z',
  health: healthyHealth,
  session: activeSession,
  portfolio: {
    summary: {
      equity_usd: 10015,
      cash_usd: 9397,
      realized_pnl_usd: 0,
      unrealized_pnl_usd: 15,
      drift_flag: false,
      last_snapshot_ts: '1777690860',
      portfolio_baseline: 'paper_wallet',
      exchange_reference_equity_usd: 15,
      exchange_reference_cash_usd: 0,
      exchange_reference_checked_at: '2026-05-02T03:00:00Z',
    },
    exposure: {
      by_asset: [
        { asset: 'USD', value_usd: 9397, pct_of_equity: 0.938 },
        { asset: 'BTC', value_usd: 310, pct_of_equity: 0.031 },
      ],
      by_strategy: [{ strategy_id: 'dca_overlay', value_usd: 620, pct_of_equity: 6.2 }],
    },
    positions: [
      {
        pair: 'XBTUSD',
        base_asset: 'BTC',
        base_size: 0.0039,
        avg_entry_price: 76099,
        current_price: 78371,
        value_usd: 310,
        unrealized_pnl_usd: 9,
        strategy_tag: 'dca_overlay',
        is_dust: false,
        min_order_size: 0.00005,
        rounded_close_size: 0.0039,
        dust_reason: null,
      },
    ],
  },
  risk: { status: healthyRisk, config: riskConfig },
  strategies: {
    state: baseStrategies,
    performance: [
      {
        strategy_id: 'dca_overlay',
        realized_pnl_quote: 0,
        window_start: '2026-04-29T03:00:00Z',
        window_end: '2026-05-02T03:00:00Z',
        trade_count: 6,
        win_rate: 0,
        max_drawdown_pct: 0,
      },
    ],
  },
  activity: {
    recent_executions: [
      {
        plan_id: 'plan_1',
        started_at: '2026-05-02T02:56:50Z',
        completed_at: '2026-05-02T02:56:51Z',
        success: true,
        orders: [],
        errors: [],
        warnings: [],
      },
    ],
    risk_decisions: [
      {
        decided_at: '2026-05-02T02:56:31Z',
        plan_id: 'plan_1',
        strategy_id: 'dca_overlay',
        pair: 'ETH/USD',
        action_type: 'none',
        blocked: true,
        block_reasons: ['Max per asset limit (718.36 > 500.77)'],
        kill_switch_active: false,
      },
      {
        decided_at: '2026-05-02T02:56:31Z',
        plan_id: 'plan_1',
        strategy_id: 'dca_overlay',
        pair: 'BTC/USD',
        action_type: 'none',
        blocked: true,
        block_reasons: ['Max per asset limit (718.36 > 500.77)'],
        kill_switch_active: false,
      },
    ],
  },
  replay: {
    available: false,
    generated_at: null,
    trust_level: null,
    trust_note: null,
    notable_warnings: [],
    end_equity_usd: null,
    pnl_usd: null,
    return_pct: null,
    fills: null,
    blocked_actions: null,
    execution_errors: null,
    coverage_status: null,
    usable_series_count: null,
    missing_series: [],
    partial_series: [],
    blocked_reason_counts: {},
    cost_model: null,
    replay_inputs: {},
    report_path: null,
  },
  market_data: {
    stale_pairs: [],
    session_pairs: ['BTCUSD', 'ETHUSD'],
    watchlist_pairs: ['ADAUSD', 'BTCUSD', 'ETHUSD', 'SOLUSD'],
    session_stale_pairs: [],
    watchlist_stale_pairs: [],
    global_stale_pairs: [],
    classification: 'healthy',
    session_critical: false,
    message: null,
  },
  live_readiness: {
    status: 'blocked',
    generated_at: '2026-05-02T03:01:00Z',
    blockers: [
      {
        id: 'live_gates',
        label: 'Live submission gates',
        status: 'blocked',
        message: 'Live submission gates are closed because this session is in paper mode. This is expected for normal paper trading.',
      },
    ],
    warnings: [
      {
        id: 'latest_replay',
        label: 'Latest replay',
        status: 'warning',
        message: 'No latest replay report is published.',
      },
    ],
    passed: [
      {
        id: 'market_data',
        label: 'Market data',
        status: 'passed',
        message: 'Session market data is healthy.',
      },
    ],
  },
  section_errors: {},
  ...overrides,
});

const renderActiveCockpit = async (snapshot = buildCockpit()) => {
  apiMocks.fetchSetupStatus.mockResolvedValue({
    configured: true,
    secrets_exist: true,
    unlocked: true,
    lifecycle: 'active',
  });
  apiMocks.fetchSessionState.mockResolvedValue(activeSession);
  apiMocks.fetchCockpitSnapshot.mockResolvedValue(snapshot);
  apiMocks.flattenAllPositions.mockResolvedValue({
    success: true,
    errors: [],
    warnings: [],
    orders: [],
  } satisfies ExecutionResultPayload);

  const result = render(<App />);

  await screen.findByRole('heading', { name: 'Paper Trading Overview' });
  await waitFor(() => expect(apiMocks.fetchCockpitSnapshot).toHaveBeenCalled());
  await screen.findByText('Runtime trust: Healthy');

  return result;
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.fetchProfiles.mockResolvedValue([]);
  apiMocks.fetchSystemHealth.mockResolvedValue(healthyHealth);
  apiMocks.fetchSystemConfig.mockResolvedValue(null);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('cockpit operator paths', () => {
  test('requires confirmation before calling flatten all', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    await renderActiveCockpit();
    await user.click(await screen.findByTestId('danger-flatten-all'));

    expect(window.confirm).toHaveBeenCalledWith(
      'Flatten all paper positions? This will attempt to close every open paper position immediately.',
    );
    expect(apiMocks.flattenAllPositions).not.toHaveBeenCalled();
  });

  test('calls flatten all after confirmation', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    await renderActiveCockpit();
    await user.click(await screen.findByTestId('danger-flatten-all'));

    expect(apiMocks.flattenAllPositions).toHaveBeenCalledTimes(1);
    await screen.findByText('Flatten-all orders submitted.');
  });

  test('switches cockpit tabs without hitting danger actions', async () => {
    const user = userEvent.setup();

    await renderActiveCockpit();
    await user.click(await screen.findByTestId('cockpit-tab-positions'));

    expect(screen.getByRole('table', { name: 'Positions' })).toBeInTheDocument();
    expect(screen.getByText('XBTUSD')).toBeInTheDocument();
    expect(apiMocks.flattenAllPositions).not.toHaveBeenCalled();
  });

  test('shows strategy evaluation heartbeat and disabled strategy truth', async () => {
    await renderActiveCockpit();

    expect(screen.getAllByText('Last evaluated').length).toBeGreaterThan(0);
    expect(screen.getAllByText('No action chosen').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('Not running').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Not yet').length).toBeGreaterThan(0);
  });

  test('keeps runtime trust healthy for watchlist-only stale market data', async () => {
    await renderActiveCockpit(buildCockpit({
      health: {
        ...healthyHealth,
        market_data_ok: false,
        market_data_status: 'degraded',
        market_data_reason: 'data_stale',
        market_data_detail: 'ADA/USD',
        market_data_stale: true,
        stale_pairs: 1,
        streaming_pairs: 3,
      },
      market_data: {
        stale_pairs: ['ADA/USD'],
        session_pairs: ['BTCUSD', 'ETHUSD'],
        watchlist_pairs: ['ADAUSD', 'BTCUSD', 'ETHUSD', 'SOLUSD'],
        session_stale_pairs: [],
        watchlist_stale_pairs: ['ADA/USD'],
        global_stale_pairs: [],
        classification: 'watchlist_only',
        session_critical: false,
        message: 'Watchlist data stale: ADA/USD.',
      },
    }));

    expect(screen.getByText('Runtime trust: Healthy')).toBeInTheDocument();
    expect(screen.getByText('Non-active market data stale')).toBeInTheDocument();
    expect(screen.getAllByText('Watchlist data stale: ADA/USD.').length).toBeGreaterThan(0);

    const integrity = screen.getByRole('region', { name: 'Integrity' });
    expect(within(integrity).getByText('Market Data')).toBeInTheDocument();
    expect(within(integrity).getByText('Degraded')).toBeInTheDocument();
  });

  test('groups zero-order risk-blocked activity under the execution plan', async () => {
    const user = userEvent.setup();

    await renderActiveCockpit();
    await user.click(await screen.findByTestId('cockpit-tab-activity'));

    expect(
      screen.getByText('plan_1: no orders placed, blocked by risk limits'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('ETH/USD: Max per asset limit (718.36 > 500.77)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('BTC/USD: Max per asset limit (718.36 > 500.77)'),
    ).toBeInTheDocument();
  });

  test('renders live readiness from the cockpit snapshot without mutation actions', async () => {
    const user = userEvent.setup();

    await renderActiveCockpit();
    await user.click(await screen.findByTestId('cockpit-tab-risk'));

    const panel = screen.getByRole('region', { name: 'Live Readiness' });
    expect(within(panel).getByText('Blocked')).toBeInTheDocument();
    expect(within(panel).getByText('Live submission gates')).toBeInTheDocument();
    expect(within(panel).getByText(/expected for normal paper trading/i)).toBeInTheDocument();
    expect(within(panel).getByText('Latest replay')).toBeInTheDocument();

    expect(apiMocks.setExecutionMode).not.toHaveBeenCalled();
    expect(apiMocks.startSession).not.toHaveBeenCalled();
  });

  test('shows warning and ready live-readiness states distinctly', async () => {
    const user = userEvent.setup();

    const { unmount } = await renderActiveCockpit(buildCockpit({
      live_readiness: {
        status: 'warning',
        generated_at: '2026-05-02T03:01:00Z',
        blockers: [],
        warnings: [
          {
            id: 'watchlist_data',
            label: 'Market data',
            status: 'warning',
            message: 'Watchlist data stale: ADA/USD.',
          },
        ],
        passed: [
          {
            id: 'live_gates',
            label: 'Live submission gates',
            status: 'passed',
            message: 'Live submission gates are open in configuration.',
          },
        ],
      },
    }));
    await user.click(await screen.findByTestId('cockpit-tab-risk'));

    expect(screen.getByRole('region', { name: 'Live Readiness' })).toHaveTextContent('Needs review');
    expect(screen.getByText('Watchlist data stale: ADA/USD.')).toBeInTheDocument();

    unmount();
    vi.clearAllMocks();
    apiMocks.fetchProfiles.mockResolvedValue([]);
    apiMocks.fetchSystemHealth.mockResolvedValue(healthyHealth);
    apiMocks.fetchSystemConfig.mockResolvedValue(null);

    await renderActiveCockpit(buildCockpit({
      live_readiness: {
        status: 'ready',
        generated_at: '2026-05-02T03:01:00Z',
        blockers: [],
        warnings: [],
        passed: [
          {
            id: 'live_gates',
            label: 'Live submission gates',
            status: 'passed',
            message: 'Live submission gates are open in configuration.',
          },
        ],
      },
    }));
    await user.click(await screen.findByTestId('cockpit-tab-risk'));

    expect(screen.getByRole('region', { name: 'Live Readiness' })).toHaveTextContent('Ready');
    expect(screen.getByText('No blockers reported.')).toBeInTheDocument();
    expect(screen.getByText('No warnings reported.')).toBeInTheDocument();
  });
});
