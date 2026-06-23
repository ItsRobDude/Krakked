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
  fetchLiveReadiness: vi.fn(),
  fetchSystemHealth: vi.fn(),
  fetchSessionState: vi.fn(),
  fetchProfiles: vi.fn(),
  fetchProfileNameSuggestion: vi.fn(),
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

const inactiveSession: SessionStateResponse = {
  ...activeSession,
  active: false,
  lifecycle: 'ready',
};

const liveActiveSession: SessionStateResponse = {
  ...activeSession,
  mode: 'live',
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
  portfolio_sync_in_progress: false,
  portfolio_baseline: 'paper_wallet',
  operator_paths: {
    active_profile_name: 'Rob',
    active_profile_config_path: '/krakked/config/profiles/Rob.yaml',
    portfolio_db_path: '/krakked/config/profiles/Rob/portfolio.db',
    config_dir: '/krakked/config',
    data_dir: '/krakked/data',
    path_errors: {},
  },
  drift_detected: false,
  drift_reason: null,
  drift_info: null,
};

const liveHealthyHealth: SystemHealth = {
  ...healthyHealth,
  execution_mode: 'live',
  current_mode: 'live',
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
  pnl_summary: { realized_pnl_usd: 0, exposure_pct: 6.2 },
  last_intents: null,
  conflict_summary: null,
  params: { pairs: ['BTC/USD', 'ETH/USD'] },
  configured_weight: 100,
  effective_weight_pct: 50,
  evidence_status: 'utility',
  evidence_label: 'Utility overlay',
  evidence_note: 'Operational overlay rather than a promoted alpha strategy.',
  ...overrides,
});

const baseStrategies = [
  strategy({}),
  strategy({
    strategy_id: 'trend_core',
    label: 'Trend Core',
    pnl_summary: { realized_pnl_usd: 0, exposure_pct: 0 },
    params: { risk_profile: 'balanced', pairs: [] },
    evidence_status: 'research_stage',
    evidence_label: 'Research stage',
    evidence_note: 'Replay evidence has not promoted this strategy beyond research-stage operation.',
    last_evaluation_summary: {
      status: 'no_signal',
      message: 'BTC/USD regime timeframe is not in an uptrend',
      deferred_no_new_bar_contexts: 0,
      no_data_contexts: 0,
      reasons: [{ reason: 'daily_regime_not_uptrend', pair: 'BTC/USD' }],
    },
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
    evidence_status: 'research_stage',
    evidence_label: 'Research only',
    evidence_note: 'ML strategy lanes are research-only until a pre-registered evidence gate passes.',
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
    decision_traces: [
      {
        plan_id: 'plan_1',
        generated_at: '2026-05-02T02:56:31Z',
        completed_at: '2026-05-02T02:56:51Z',
        status: 'risk_blocked',
        summary: 'Risk blocked all 2 actionable action(s); no orders sent.',
        strategy_ids: ['dca_overlay'],
        pairs: ['ETH/USD', 'BTC/USD'],
        action_count: 2,
        actionable_action_count: 2,
        allowed_action_count: 0,
        blocked_action_count: 2,
        no_op_action_count: 0,
        clamped_action_count: 0,
        order_count: 0,
        filled_order_count: 0,
        risk_reasons: ['Max per asset limit (718.36 > 500.77)'],
        clamp_reasons: [],
        no_op_reasons: [],
        execution_errors: [],
        execution_warnings: [],
        details: [
          'Signal/risk actions: 2 actionable, 0 allowed, 2 blocked, 0 no-op.',
          'Risk reason: Max per asset limit (718.36 > 500.77)',
        ],
        trace_quality: 'complete',
        degraded_reason: null,
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
  risk_signal: {
    available: true,
    status: 'ready',
    source: 'riskmetrics_ewma',
    benchmark_pair: 'BTC/USD',
    timeframe: '4h',
    generated_at: '2026-05-02T03:01:00Z',
    latest_bar_time: '2026-05-02T00:00:00Z',
    latest_bar_age_seconds: 10800,
    bars_used: 120,
    lookback_bars: 720,
    min_bars: 84,
    horizon_bars: 6,
    ewma_lambda: 0.94,
    ewma_per_bar_variance: 0.0001,
    ewma_per_bar_volatility_pct: 1,
    ewma_horizon_variance: 0.0006,
    ewma_horizon_volatility_pct: 2.45,
    volatility_percentile: 65,
    risk_level: 'normal',
    thresholds: {
      elevated_percentile: 75,
      stressed_percentile: 90,
      elevated_horizon_volatility_pct: 3,
      stressed_horizon_volatility_pct: 5,
    },
    display_only: true,
    trading_effect: false,
    runtime_wiring_approved: false,
    notes: ['Display-only context; does not alter strategy selection, sizing, or order flow.'],
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

const renderActiveCockpit = async (
  snapshot = buildCockpit(),
  session: SessionStateResponse = activeSession,
  expectedRuntimeTrustText: string | null = 'Runtime trust: Healthy',
) => {
  apiMocks.fetchSetupStatus.mockResolvedValue({
    configured: true,
    secrets_exist: true,
    unlocked: true,
    lifecycle: 'active',
  });
  apiMocks.fetchSessionState.mockResolvedValue(session);
  apiMocks.fetchCockpitSnapshot.mockResolvedValue(snapshot);
  apiMocks.flattenAllPositions.mockResolvedValue({
    success: true,
    errors: [],
    warnings: [],
    orders: [],
  } satisfies ExecutionResultPayload);

  const result = render(<App />);

  await screen.findByRole('heading', {
    name: session.mode === 'live' ? 'Live Trading Overview' : 'Paper Trading Overview',
  });
  await waitFor(() => expect(apiMocks.fetchCockpitSnapshot).toHaveBeenCalled());
  if (expectedRuntimeTrustText) {
    await screen.findByText(expectedRuntimeTrustText);
  }

  return result;
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.fetchProfiles.mockResolvedValue([]);
  apiMocks.fetchSystemHealth.mockResolvedValue(healthyHealth);
  apiMocks.fetchSystemConfig.mockResolvedValue(null);
  apiMocks.fetchLiveReadiness.mockResolvedValue(buildCockpit().live_readiness);
  apiMocks.fetchProfileNameSuggestion.mockResolvedValue({
    purpose: 'paper-validation',
    name: 'paper-validation-2026-06-21',
  });
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
    expect(screen.getByText('BTC/USD regime timeframe is not in an uptrend')).toBeInTheDocument();
    expect(screen.getAllByText('No action chosen').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('Not running').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Not yet').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Research stage').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Can paper-trade; unproven').length).toBeGreaterThan(0);
    expect(screen.getAllByText('No trading effect while paused').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/not a profitability endorsement/i).length).toBeGreaterThan(0);
  });

  test('shows score-filtered strategy candidates distinctly from no-action text', async () => {
    await renderActiveCockpit(buildCockpit({
      strategies: {
        state: [
          strategy({
            strategy_id: 'rs_rotation',
            label: 'Relative Strength Rotation',
            evidence_status: 'research_stage',
            evidence_label: 'Research stage',
            evidence_note: 'Configured but disabled by default after replay evidence failed promotion.',
            params: {
              pairs: ['BTC/USD', 'ETH/USD', 'SOL/USD', 'ADA/USD'],
              timeframe: '4h',
            },
            last_evaluation_summary: {
              status: 'intents_score_filtered',
              message: '2 candidates filtered before risk checks',
              filtered_by_score: 2,
              actions_after_scoring: 0,
              score_threshold: 0.05,
              intents_emitted: 2,
            },
            last_intents: [
              {
                pair: 'SOL/USD',
                side: 'long',
                intent_type: 'enter',
                desired_exposure_usd: 250,
                confidence: 0,
                timeframe: '4h',
                score: 0,
                score_threshold: 0.05,
                weight_factor: 1,
                filter_stage: 'score_gate',
                filter_reason: 'below_score_threshold',
                relative_return: -0.01,
              },
            ],
          }),
        ],
        performance: [],
      },
      activity: {
        recent_executions: [],
        risk_decisions: [],
        decision_traces: [],
      },
    }));

    expect(screen.getAllByText('Score-filtered SOL/USD: 0.000 < 0.050').length).toBeGreaterThan(0);
    expect(screen.getByText('2 candidates filtered before risk checks')).toBeInTheDocument();
    expect(screen.queryByText('No action chosen')).not.toBeInTheDocument();
  });

  test('shows display-only EWMA market risk context', async () => {
    await renderActiveCockpit();

    const panel = screen.getByRole('region', { name: 'BTC Risk Signal' });
    expect(within(panel).getByText('Normal')).toBeInTheDocument();
    expect(within(panel).getByText('Display only')).toBeInTheDocument();
    expect(within(panel).getByText('No trading effect')).toBeInTheDocument();
    expect(within(panel).getByText('Not wired to trading')).toBeInTheDocument();
    expect(within(panel).getByText(/does not change strategy selection, sizing, risk limits, or order flow/i)).toBeInTheDocument();
    expect(within(panel).getByText('2.45%')).toBeInTheDocument();
    expect(within(panel).getByText('65.00%')).toBeInTheDocument();
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

    const trace = screen.getByRole('region', { name: 'Decision Trace' });
    expect(within(trace).getByText('Risk blocked')).toBeInTheDocument();
    expect(within(trace).getByText('Risk blocked all 2 actionable action(s); no orders sent.')).toBeInTheDocument();
    expect(within(trace).getByText('0 allowed / 2 blocked / 0 no-op')).toBeInTheDocument();
    expect(within(trace).getByText('0 sent / 0 filled')).toBeInTheDocument();

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

  test('renders no-op, clamped, and limited trace states', async () => {
    const user = userEvent.setup();
    const base = buildCockpit();

    await renderActiveCockpit(buildCockpit({
      activity: {
        ...base.activity!,
        decision_traces: [
          {
            plan_id: 'plan_noop',
            generated_at: '2026-05-02T02:56:31Z',
            completed_at: '2026-05-02T02:56:51Z',
            status: 'no_action',
            summary: 'Strategies evaluated 1 no-op action(s); no orders sent.',
            strategy_ids: ['trend_core'],
            pairs: ['BTC/USD'],
            action_count: 1,
            actionable_action_count: 0,
            allowed_action_count: 0,
            blocked_action_count: 0,
            no_op_action_count: 1,
            clamped_action_count: 0,
            order_count: 0,
            filled_order_count: 0,
            risk_reasons: [],
            clamp_reasons: [],
            no_op_reasons: ['No action because conflict netted out'],
            execution_errors: [],
            execution_warnings: [],
            details: [
              'Signal/risk actions: 0 actionable, 0 allowed, 0 blocked, 1 no-op.',
              'No action: No action because conflict netted out',
            ],
            trace_quality: 'complete',
            degraded_reason: null,
          },
          {
            plan_id: 'plan_clamped',
            generated_at: '2026-05-02T02:57:31Z',
            completed_at: '2026-05-02T02:57:51Z',
            status: 'orders_sent',
            summary: '1/1 actionable action(s) cleared risk; 1 action(s) clamped by risk; 1 order(s) sent, 1 filled.',
            strategy_ids: ['trend_core'],
            pairs: ['ETH/USD'],
            action_count: 1,
            actionable_action_count: 1,
            allowed_action_count: 1,
            blocked_action_count: 0,
            no_op_action_count: 0,
            clamped_action_count: 1,
            order_count: 1,
            filled_order_count: 1,
            risk_reasons: ['Max per asset limit'],
            clamp_reasons: ['Max per asset limit'],
            no_op_reasons: [],
            execution_errors: [],
            execution_warnings: [],
            details: [
              'Signal/risk actions: 1 actionable, 1 allowed, 0 blocked, 0 no-op.',
              'Risk clamped 1 action(s).',
              'Risk clamped: Max per asset limit',
            ],
            trace_quality: 'decisions_only',
            degraded_reason: 'Execution plan record unavailable; trace is reconstructed from persisted risk decisions.',
          },
        ],
      },
    }));
    await user.click(await screen.findByTestId('cockpit-tab-activity'));

    const trace = screen.getByRole('region', { name: 'Decision Trace' });
    expect(within(trace).getByText('No action')).toBeInTheDocument();
    expect(within(trace).getByText('0 allowed / 0 blocked / 1 no-op')).toBeInTheDocument();
    expect(within(trace).getByText('1 allowed / 0 blocked / 0 no-op / 1 clamped')).toBeInTheDocument();
    expect(within(trace).getByText('Limited trace')).toBeInTheDocument();
    expect(within(trace).getByText('Risk clamped: Max per asset limit')).toBeInTheDocument();
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

  test('starts live automation without a second password prompt', async () => {
    const user = userEvent.setup();

    apiMocks.fetchSetupStatus.mockResolvedValue({
      configured: true,
      secrets_exist: true,
      unlocked: true,
      lifecycle: 'ready',
    });
    apiMocks.fetchSessionState.mockResolvedValue(inactiveSession);
    apiMocks.fetchProfiles.mockResolvedValue([{ name: 'Rob', description: 'Primary paper profile' }]);
    apiMocks.fetchSystemHealth
      .mockResolvedValueOnce(healthyHealth)
      .mockResolvedValue(liveHealthyHealth);
    apiMocks.fetchSystemConfig.mockResolvedValue({ ml: { enabled: false } });
    apiMocks.fetchLiveReadiness.mockResolvedValue({
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
    });
    apiMocks.setExecutionMode.mockResolvedValue({
      mode: 'live',
      validate_only: false,
      paper_tests_completed: true,
      reloading: true,
    });
    apiMocks.updateSessionConfig.mockResolvedValue(liveActiveSession);
    apiMocks.startSession.mockResolvedValue(liveActiveSession);
    apiMocks.getRiskStatus.mockResolvedValue(healthyRisk);

    render(<App />);

    await screen.findByRole('heading', { name: 'Start a session' });
    await user.selectOptions(screen.getByLabelText('Mode'), 'live');
    await user.click(screen.getByRole('button', { name: 'Start live automation' }));

    await waitFor(() => {
      expect(apiMocks.setExecutionMode).toHaveBeenCalledWith(
        'live',
        'ENABLE LIVE TRADING',
        true,
      );
    });
    expect(screen.queryByLabelText(/master password/i)).not.toBeInTheDocument();
    expect(apiMocks.startSession).toHaveBeenCalledTimes(1);
  });

  test('shows live readiness blockers on the startup screen', async () => {
    const user = userEvent.setup();

    apiMocks.fetchSetupStatus.mockResolvedValue({
      configured: true,
      secrets_exist: true,
      unlocked: true,
      lifecycle: 'ready',
    });
    apiMocks.fetchSessionState.mockResolvedValue(inactiveSession);
    apiMocks.fetchProfiles.mockResolvedValue([{ name: 'Rob', description: 'Primary profile' }]);
    apiMocks.fetchSystemHealth.mockResolvedValue(healthyHealth);
    apiMocks.fetchSystemConfig.mockResolvedValue({ ml: { enabled: false } });
    apiMocks.fetchLiveReadiness.mockResolvedValue(buildCockpit().live_readiness);

    render(<App />);

    await screen.findByRole('heading', { name: 'Start a session' });
    await user.selectOptions(screen.getByLabelText('Mode'), 'live');

    expect(screen.getByText('Live readiness')).toBeInTheDocument();
    await screen.findByText('Blocked');
    expect(screen.getByText(/live submission gates are closed/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start live automation' })).toBeDisabled();
    expect(apiMocks.startSession).not.toHaveBeenCalled();
  });

  test('keeps live start disabled while readiness is unavailable', async () => {
    const user = userEvent.setup();

    apiMocks.fetchSetupStatus.mockResolvedValue({
      configured: true,
      secrets_exist: true,
      unlocked: true,
      lifecycle: 'ready',
    });
    apiMocks.fetchSessionState.mockResolvedValue(inactiveSession);
    apiMocks.fetchProfiles.mockResolvedValue([{ name: 'Rob', description: 'Primary profile' }]);
    apiMocks.fetchSystemHealth.mockResolvedValue(healthyHealth);
    apiMocks.fetchSystemConfig.mockResolvedValue({ ml: { enabled: false } });
    apiMocks.fetchLiveReadiness.mockResolvedValue(null);

    render(<App />);

    await screen.findByRole('heading', { name: 'Start a session' });
    await user.selectOptions(screen.getByLabelText('Mode'), 'live');

    expect(screen.getByText('Live readiness')).toBeInTheDocument();
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(
      screen.getByText('Live readiness must load before the UI can start live automation.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start live automation' })).toBeDisabled();
    expect(apiMocks.setExecutionMode).not.toHaveBeenCalled();
    expect(apiMocks.startSession).not.toHaveBeenCalled();
  });

  test('prefills new paper-validation profile name from suggestion endpoint', async () => {
    const user = userEvent.setup();

    apiMocks.fetchSetupStatus.mockResolvedValue({
      configured: true,
      secrets_exist: true,
      unlocked: true,
      lifecycle: 'ready',
    });
    apiMocks.fetchSessionState.mockResolvedValue(inactiveSession);
    apiMocks.fetchProfiles.mockResolvedValue([{ name: 'Rob', description: 'Primary profile' }]);
    apiMocks.fetchSystemHealth.mockResolvedValue(healthyHealth);
    apiMocks.fetchSystemConfig.mockResolvedValue({ ml: { enabled: false } });

    render(<App />);

    await screen.findByRole('heading', { name: 'Start a session' });
    await user.click(screen.getByRole('button', { name: 'New profile' }));

    const input = await screen.findByLabelText('New profile name');
    await waitFor(() => {
      expect(input).toHaveValue('paper-validation-2026-06-21');
    });
    expect(apiMocks.fetchProfileNameSuggestion).toHaveBeenCalledWith('paper-validation');
  });

  test('shows active portfolio db path in portfolio integrity advanced view', async () => {
    const user = userEvent.setup();

    await renderActiveCockpit();

    await user.click(await screen.findByTestId('cockpit-tab-positions'));
    await user.click(screen.getByRole('button', { name: 'Advanced' }));

    expect(screen.getByText('Portfolio DB')).toBeInTheDocument();
    expect(
      screen.getByText('/krakked/config/profiles/Rob/portfolio.db'),
    ).toBeInTheDocument();
    expect(screen.getByText('Profile Rob')).toBeInTheDocument();
  });

  test('shows operator paths in live mode advanced view', async () => {
    const user = userEvent.setup();

    await renderActiveCockpit(
      buildCockpit({
        health: liveHealthyHealth,
        session: liveActiveSession,
      }),
      liveActiveSession,
    );

    await user.click(await screen.findByTestId('cockpit-tab-positions'));
    await user.click(screen.getByRole('button', { name: 'Advanced' }));

    expect(screen.getByText('Portfolio DB')).toBeInTheDocument();
    expect(
      screen.getByText('/krakked/config/profiles/Rob/portfolio.db'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Paper account source')).not.toBeInTheDocument();
  });

  test('renders unknown drift without claiming runtime trust is healthy', async () => {
    await renderActiveCockpit(
      buildCockpit({
        health: {
          ...healthyHealth,
          drift_detected: false,
          drift_reason: null,
          drift_info: {
            status: 'unknown',
            source: 'cached',
            reason: 'cached_drift_status_unavailable',
          },
        },
      }),
      activeSession,
      'Runtime trust: Needs review',
    );

    expect(screen.getByText('Runtime trust: Needs review')).toBeInTheDocument();
    expect(screen.queryByText('Runtime trust: Healthy')).not.toBeInTheDocument();
    expect(screen.getByText('Drift status unknown')).toBeInTheDocument();

    const integrity = screen.getByRole('region', { name: 'Integrity' });
    expect(within(integrity).getByText('Unknown')).toBeInTheDocument();
    expect(within(integrity).queryByText('Clear')).not.toBeInTheDocument();
  });

  test('shows unavailable operator path rows explicitly', async () => {
    const user = userEvent.setup();

    await renderActiveCockpit(buildCockpit({
      health: {
        ...healthyHealth,
        operator_paths: {
          active_profile_name: 'Rob',
          active_profile_config_path: null,
          portfolio_db_path: null,
          config_dir: '/krakked/config',
          data_dir: '/krakked/data',
          path_errors: {
            portfolio_db_path: 'Unable to resolve portfolio DB path.',
          },
        },
      },
    }));

    await user.click(await screen.findByTestId('cockpit-tab-positions'));
    await user.click(screen.getByRole('button', { name: 'Advanced' }));

    expect(screen.getByText('Portfolio DB')).toBeInTheDocument();
    expect(screen.getByText('Profile config')).toBeInTheDocument();
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Unable to resolve portfolio DB path.')).toBeInTheDocument();
    expect(screen.getByText('/krakked/config')).toBeInTheDocument();
    expect(screen.getByText('/krakked/data')).toBeInTheDocument();
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
