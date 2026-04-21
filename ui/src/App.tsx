import { useCallback, useEffect, useRef, useState } from 'react';
import { KpiGrid, Kpi } from './components/KpiGrid';
import { Layout } from './components/Layout';
import { RiskPanel } from './components/RiskPanel';
import { ReplaySummaryPanel } from './components/ReplaySummaryPanel';
import { LogEntry, LogPanel } from './components/LogPanel';
import { PositionRow, PositionsTable } from './components/PositionsTable';
import { Sidebar } from './components/Sidebar';
import { StrategiesPanel } from './components/StrategiesPanel';
import { StartupScreen } from './components/StartupScreen';
import { SetupWizard } from './components/SetupWizard';
import { PasswordScreen } from './components/PasswordScreen';
import { LiveModeModal } from './components/LiveModeModal';
import {
  fetchExposure,
  fetchPortfolioSummary,
  fetchPositions,
  fetchRecentExecutions,
  fetchLatestReplay,
  fetchRiskDecisions,
  fetchSystemHealth,
  fetchSessionState,
  fetchProfiles, ProfileSummary,
  fetchStrategies,
  fetchStrategyPerformance,
  fetchRiskConfig,
  applyRiskPreset,
  getRiskStatus,
  fetchSetupStatus,
  ExposureBreakdown,
  PortfolioSummary,
  PositionPayload,
  ReplayLatestSummary,
  RiskConfig,
  RiskStatus,
  RecentExecution,
  RiskDecision,
  RiskPresetName,
  StrategyRiskProfile,
  StrategyPerformance,
  StrategyState,
  SystemHealth,
  SessionStateResponse,
  SessionConfigRequest,
  SetupStatus,
  updateRiskConfig,
  setKillSwitch,
  patchStrategyConfig,
  setStrategyEnabled,
  setExecutionMode,
  createProfile,
  fetchSystemConfig,
  applyConfig,
  startSession,
  stopSession,
  ExecutionMode,
  flattenAllPositions,
  downloadRuntimeConfig,
  updateSessionConfig,
} from './services/api';
import { RISK_PRESET_META, formatPresetSummary } from './constants/riskPresets';
import { getRuntimeTrust, takeImportantWarnings } from './utils/operatorTrust';

const DASHBOARD_REFRESH_MS = Number(import.meta.env.VITE_REFRESH_DASHBOARD_MS ?? 5000) || 5000;
const ORDERS_REFRESH_MS = Number(import.meta.env.VITE_REFRESH_ORDERS_MS ?? 5000) || 5000;
const ACTIVE_DASHBOARD_REFRESH_MS = Math.min(DASHBOARD_REFRESH_MS, ORDERS_REFRESH_MS);
const ACTIVE_RESOURCE_TIMEOUT_MS = 3500;
const STARTER_STRATEGY_IDS = ['trend_core', 'vol_breakout', 'majors_mean_rev', 'rs_rotation'] as const;
type SystemMessage = { tone: 'info' | 'error' | 'success'; message: string };
type DashboardAlert = { id: string; tone: 'danger' | 'warning' | 'info'; title: string; message: string };

const formatCurrency = (value: number | null | undefined) => {
  const formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
  if (typeof value !== 'number' || Number.isNaN(value)) return formatter.format(0);
  return formatter.format(value);
};

const formatPercent = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '0.00%';
  return `${value.toFixed(2)}%`;
};

const formatTimestamp = (timestamp: string | null) => {
  if (!timestamp) return 'Unknown';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'Unknown';
  return parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};

const formatDateTime = (timestamp: string | null | undefined) => {
  if (!timestamp) return 'Unknown';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'Unknown';
  return parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const getConnectionStateFromHealth = (state: SystemHealth | null): 'connected' | 'degraded' => {
  if (!state) return 'degraded';
  if (state.market_data_status === 'warming_up') {
    return state.execution_ok && state.portfolio_sync_ok ? 'connected' : 'degraded';
  }
  return state.market_data_ok && state.execution_ok && state.portfolio_sync_ok ? 'connected' : 'degraded';
};

const isRiskProfile = (value: unknown): value is StrategyRiskProfile =>
  value === 'conservative' || value === 'balanced' || value === 'aggressive';

const extractMlEnabledFromConfig = (
  snapshot: Record<string, unknown> | null,
  fallback: boolean,
) => {
  const mlSection = snapshot?.ml;
  if (mlSection && typeof mlSection === 'object') {
    const enabled = (mlSection as Record<string, unknown>).enabled;
    if (typeof enabled === 'boolean') {
      return enabled;
    }
  }
  return fallback;
};

const RISK_PRESET_OPTIONS: RiskPresetName[] = ['conservative', 'balanced', 'aggressive', 'degen'];

const isExchangeBalanceBaseline = (baseline: string | null | undefined) => baseline === 'exchange_balances';

const recomputeEffectiveWeights = (nextStrategies: StrategyState[]) => {
  const totalWeight = nextStrategies
    .filter((strategy) => strategy.enabled)
    .reduce((sum, strategy) => sum + (strategy.configured_weight || 100), 0);

  return nextStrategies.map((strategy) => ({
    ...strategy,
    effective_weight_pct: strategy.enabled && totalWeight > 0
      ? ((strategy.configured_weight || 100) / totalWeight) * 100
      : null,
  }));
};

const getDrawdownState = (drawdownPct?: number) => {
  if (drawdownPct === undefined) return { label: 'No data', tone: 'neutral' as const };
  if (drawdownPct < 10) return { label: 'Leading', tone: 'success' as const };
  if (drawdownPct < 25) return { label: 'Cooling', tone: 'warning' as const };
  return { label: 'Under pressure', tone: 'danger' as const };
};

const getStrategyMomentum = (strategy: StrategyState, performance?: StrategyPerformance) => {
  if (!strategy.enabled) {
    return { label: 'Paused', tone: 'neutral' as const };
  }
  if ((strategy.conflict_summary?.some((entry) => entry.outcome === 'winner'))) {
    return { label: 'Leading', tone: 'success' as const };
  }
  if ((performance?.max_drawdown_pct ?? 0) >= 25) {
    return { label: 'Under pressure', tone: 'danger' as const };
  }
  if ((performance?.realized_pnl_quote ?? 0) < 0 || (performance?.max_drawdown_pct ?? 0) >= 10) {
    return { label: 'Cooling', tone: 'warning' as const };
  }
  return { label: 'Stable', tone: 'info' as const };
};

const buildKpis = (summary: PortfolioSummary) => {
  const exchangeBalanceBaseline = isExchangeBalanceBaseline(summary.portfolio_baseline);

  return [
    {
      label: 'Total Equity',
      value: formatCurrency(summary.equity_usd),
      tone: 'neutral' as const,
      hint: exchangeBalanceBaseline
        ? 'Reference equity from current exchange balances'
        : (summary.last_snapshot_ts ? `Last snapshot ${formatTimestamp(summary.last_snapshot_ts)}` : 'Awaiting the first snapshot'),
    },
    {
      label: 'Unrealized PnL',
      value: formatCurrency(summary.unrealized_pnl_usd),
      tone: (summary.unrealized_pnl_usd ?? 0) < 0 ? 'danger' as const : 'success' as const,
      hint: exchangeBalanceBaseline
        ? 'Valued against current market prices'
        : (summary.drift_flag ? 'Drift detected' : 'Within expected range'),
    },
    {
      label: 'Session Realized',
      value: formatCurrency(summary.realized_pnl_usd),
      tone: (summary.realized_pnl_usd ?? 0) < 0 ? 'danger' as const : 'success' as const,
      hint: exchangeBalanceBaseline ? 'Prior live trade history is not replayed into paper PnL' : 'Realized session result',
    },
    {
      label: 'Available Cash',
      value: formatCurrency(summary.cash_usd),
      tone: 'neutral' as const,
      hint: exchangeBalanceBaseline ? 'Reference cash from current exchange balances' : 'Deployable collateral',
    },
  ];
};

const transformPositions = (payload: PositionPayload[]): PositionRow[] =>
  payload.map((position) => {
    const side: PositionRow['side'] = position.base_size < 0 ? 'short' : 'long';
    const size = `${Math.abs(position.base_size).toFixed(4)} ${position.base_asset}`;
    const entry = position.avg_entry_price ? formatCurrency(position.avg_entry_price) : '—';
    const mark = position.current_price ? formatCurrency(position.current_price) : '—';
    const pnlValue = position.unrealized_pnl_usd ?? 0;
    const pnl = pnlValue === 0 ? '$0.00' : formatCurrency(pnlValue);

    let status = position.strategy_tag || 'Tracking';
    if (position.is_dust) {
      status = 'Dust';
    }

    return { pair: position.pair, side, size, entry, mark, pnl, status };
  });

const transformLogs = (executions: RecentExecution[]) =>
  executions.map((execution) => {
    const source = execution.errors[0] || execution.warnings[0] || 'Execution summary';
    const completedAt = execution.completed_at || execution.started_at;
    const timestamp = formatTimestamp(completedAt);
    const message = `${execution.plan_id} ${execution.success ? 'succeeded' : 'failed'} (${execution.orders.length} orders)`;
    const level: LogEntry['level'] = execution.success ? 'info' : 'error';

    return {
      level,
      message,
      timestamp,
      source,
      sortKey: completedAt ? new Date(completedAt).getTime() : undefined,
    };
  });

const transformRiskDecisions = (decisions: RiskDecision[]) =>
  decisions
    .filter((decision) => decision.blocked || decision.kill_switch_active)
    .map((decision) => {
      const timestamp = formatTimestamp(decision.decided_at);
      const reasons = decision.block_reasons.length
        ? decision.block_reasons.join(', ')
        : decision.kill_switch_active
          ? 'Kill switch active'
          : '';
      const message = `${decision.pair}: ${decision.blocked ? 'blocked' : 'allowed'} ${decision.action_type}${
        reasons ? ` (${reasons})` : ''
      }`;
      const level: LogEntry['level'] = decision.blocked ? 'warning' : 'info';

      return {
        level,
        message,
        timestamp,
        source: decision.strategy_id || 'Risk',
        sortKey: new Date(decision.decided_at).getTime(),
      };
    });

function DashboardShell({ onLogout }: { onLogout: () => void }) {
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [activePositions, setActivePositions] = useState<PositionRow[]>([]);
  const [dustPositions, setDustPositions] = useState<PositionRow[]>([]);
  const [exposure, setExposure] = useState<ExposureBreakdown | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connectionState, setConnectionState] = useState<'connected' | 'degraded'>('degraded');
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [latestReplay, setLatestReplay] = useState<ReplayLatestSummary | null>(null);
  const [risk, setRisk] = useState<RiskStatus | null>(null);
  const [riskBusy, setRiskBusy] = useState(false);
  const [riskFeedback, setRiskFeedback] = useState<{ tone: 'info' | 'error' | 'success'; message: string } | null>(null);
  const [riskConfig, setRiskConfig] = useState<RiskConfig | null>(null);
  const [riskConfigBusy, setRiskConfigBusy] = useState(false);
  const [riskConfigError, setRiskConfigError] = useState<string | null>(null);
  const [currentPreset, setCurrentPreset] = useState<RiskPresetName | null>(null);
  const [strategies, setStrategies] = useState<StrategyState[]>([]);
  const [strategyPerformance, setStrategyPerformance] = useState<
    Record<string, StrategyPerformance>
  >({});
  const [strategyRisk, setStrategyRisk] = useState<Record<string, StrategyRiskProfile>>({});
  const [strategyLearning, setStrategyLearning] = useState<Record<string, boolean>>({});
  const [strategyBusy, setStrategyBusy] = useState<Set<string>>(new Set());
  const [strategyFeedback, setStrategyFeedback] = useState<string | null>(null);
  const [systemMessage, setSystemMessage] = useState<SystemMessage | null>(null);
  const [refreshIssues, setRefreshIssues] = useState<Record<string, string>>({});
  const [modeBusy, setModeBusy] = useState(false);
  const [session, setSession] = useState<SessionStateResponse | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [loopIntervalDraft, setLoopIntervalDraft] = useState<number>(15);
  const [startupMlEnabled, setStartupMlEnabled] = useState(true);

  const [showLiveModal, setShowLiveModal] = useState(false);
  const [pendingLiveStart, setPendingLiveStart] = useState<SessionConfigRequest | null>(null);
  const dashboardRefreshInFlightRef = useRef(false);
  const dashboardAbortRef = useRef<AbortController | null>(null);
  const requestInFlightRef = useRef<Record<string, boolean>>({});

  const mlEnabled = session?.ml_enabled ?? false;
  const startupReloading = Boolean(
    !session?.active && (session?.reloading || session?.lifecycle === 'initializing'),
  );
  const liveStrategyGuardrails = Boolean(session?.active && session.mode === 'live');

  const updateRefreshIssue = useCallback((key: string, message: string | null) => {
    setRefreshIssues((current) => {
      const next = { ...current };
      if (message) {
        next[key] = message;
      } else {
        delete next[key];
      }
      return next;
    });
  }, []);

  const requestDashboardResource = async <T,>(
    key: string,
    controller: AbortController,
    loader: (options: { signal: AbortSignal; timeoutMs: number }) => Promise<T | null>,
  ) => {
    if (requestInFlightRef.current[key]) {
      return null;
    }

    requestInFlightRef.current[key] = true;
    try {
      return await loader({ signal: controller.signal, timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS });
    } finally {
      requestInFlightRef.current[key] = false;
    }
  };

  const loadSession = useCallback(async () => {
    const sessionState = await fetchSessionState({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS });

    if (sessionState) {
      setSession(sessionState);
      setLoopIntervalDraft(sessionState.loop_interval_sec);
    }

    const [profileSummaries, systemHealth, systemConfig] = await Promise.all([
      fetchProfiles(),
      fetchSystemHealth({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS }),
      fetchSystemConfig({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS }).catch(() => null),
    ]);

    if (systemHealth) {
      setHealth(systemHealth);
      setConnectionState(getConnectionStateFromHealth(systemHealth));
      updateRefreshIssue('session-health', null);
    } else {
      setConnectionState('degraded');
      updateRefreshIssue(
        'session-health',
        'System health is unavailable. Showing the last successful status where possible.',
      );
    }

    setProfiles(profileSummaries);
    setStartupMlEnabled(extractMlEnabledFromConfig(systemConfig, sessionState?.ml_enabled ?? true));
    setSessionLoading(false);
  }, [updateRefreshIssue]);

  useEffect(() => {
    let cancelled = false;
    loadSession().then(() => {
      if (cancelled) return;
    });
    return () => {
      cancelled = true;
    };
  }, [loadSession]);

  useEffect(() => {
    if (session?.loop_interval_sec) {
      setLoopIntervalDraft(session.loop_interval_sec);
    }
  }, [session?.loop_interval_sec]);

  useEffect(() => {
    if (!startupReloading) return;

    let cancelled = false;
    const interval = setInterval(() => {
      if (cancelled) return;
      void loadSession();
    }, 1500);

    void loadSession();

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [startupReloading, loadSession]);

  useEffect(() => {
    if (!session?.active) return;

    let cancelled = false;
    const loadActiveDashboard = async () => {
      if (dashboardRefreshInFlightRef.current) {
        return;
      }

      dashboardRefreshInFlightRef.current = true;
      const controller = new AbortController();
      dashboardAbortRef.current = controller;

      try {
        const [
          portfolioSummary,
          exposureData,
          systemHealth,
          riskStatus,
          riskConfigData,
          positionsData,
          strategiesData,
          perf,
          executions,
          decisions,
          latestReplaySummary,
        ] = await Promise.all([
          requestDashboardResource('portfolio-summary', controller, fetchPortfolioSummary),
          requestDashboardResource('portfolio-exposure', controller, fetchExposure),
          requestDashboardResource('system-health', controller, fetchSystemHealth),
          requestDashboardResource('risk-status', controller, getRiskStatus),
          requestDashboardResource('risk-config', controller, fetchRiskConfig),
          requestDashboardResource('portfolio-positions', controller, fetchPositions),
          requestDashboardResource('strategy-state', controller, fetchStrategies),
          requestDashboardResource('strategy-performance', controller, fetchStrategyPerformance),
          requestDashboardResource('recent-executions', controller, fetchRecentExecutions),
          requestDashboardResource('risk-decisions', controller, (options) => fetchRiskDecisions(50, options)),
          requestDashboardResource('latest-replay', controller, fetchLatestReplay),
        ]);

        if (cancelled || controller.signal.aborted) return;

        const dashboardFailures: string[] = [];

        if (portfolioSummary) {
          setSummary(portfolioSummary);
          setKpis(buildKpis(portfolioSummary));
        } else {
          dashboardFailures.push('portfolio summary');
        }

        if (systemHealth) {
          setHealth(systemHealth);
          setConnectionState(getConnectionStateFromHealth(systemHealth));
          updateRefreshIssue('session-health', null);
        } else {
          dashboardFailures.push('system health');
          setConnectionState('degraded');
          updateRefreshIssue(
            'session-health',
            'System health is unavailable. Showing the last successful status where possible.',
          );
        }

        if (riskStatus) {
          setRisk(riskStatus);
          updateRefreshIssue('risk-status', null);
        } else {
          dashboardFailures.push('risk status');
          updateRefreshIssue(
            'risk-status',
            'Risk status refresh failed. Dashboard risk indicators may be stale.',
          );
        }

        if (exposureData) {
          setExposure(exposureData);
          updateRefreshIssue('exposure', null);
        } else {
          dashboardFailures.push('exposure');
          updateRefreshIssue(
            'exposure',
            'Exposure refresh failed. Exposure panels may be showing the last successful data.',
          );
        }

        if (riskConfigData) {
          setRiskConfig(riskConfigData);
          updateRefreshIssue('risk-config', null);
        } else {
          updateRefreshIssue(
            'risk-config',
            'Risk configuration refresh failed. Edits may be working against stale values.',
          );
        }

        if (positionsData) {
          const active = positionsData.filter((position) => !position.is_dust);
          const dust = positionsData.filter((position) => position.is_dust);
          setActivePositions(transformPositions(active));
          setDustPositions(transformPositions(dust));
          updateRefreshIssue('positions', null);
        } else {
          updateRefreshIssue(
            'positions',
            'Positions refresh failed. Position tables may be showing the last successful snapshot.',
          );
        }

        if (latestReplaySummary) {
          setLatestReplay(latestReplaySummary);
          updateRefreshIssue('latest-replay', null);
        } else {
          updateRefreshIssue(
            'latest-replay',
            'Latest replay summary refresh failed. Showing the last published replay where possible.',
          );
        }

        if (strategiesData) {
          setStrategies(strategiesData);
          setStrategyRisk((previous) => {
            const next = { ...previous };
            strategiesData.forEach((strategy) => {
              const riskProfile = strategy.params?.risk_profile;
              if (isRiskProfile(riskProfile)) {
                next[strategy.strategy_id] = riskProfile;
              } else if (!next[strategy.strategy_id]) {
                next[strategy.strategy_id] = 'balanced';
              }
            });
            return next;
          });
          setStrategyLearning((previous) => {
            const next = { ...previous };
            strategiesData.forEach((strategy) => {
              const learning = strategy.params?.continuous_learning;
              if (typeof learning === 'boolean') {
                next[strategy.strategy_id] = learning;
              } else if (next[strategy.strategy_id] === undefined) {
                next[strategy.strategy_id] = true;
              }
            });
            return next;
          });
          updateRefreshIssue('strategies', null);
        } else {
          updateRefreshIssue(
            'strategies',
            'Strategy refresh failed. Strategy toggles and weights may be showing stale data.',
          );
        }

        if (perf) {
          const byId: Record<string, StrategyPerformance> = {};
          perf.forEach((entry) => {
            byId[entry.strategy_id] = entry;
          });
          setStrategyPerformance(byId);
          updateRefreshIssue('strategy-performance', null);
        } else {
          updateRefreshIssue(
            'strategy-performance',
            'Strategy performance refresh failed. Performance metrics may be stale.',
          );
        }

        if (executions || decisions) {
          const executionLogs = executions ? transformLogs(executions) : [];
          const decisionLogs = decisions ? transformRiskDecisions(decisions) : [];
          const merged = [...executionLogs, ...decisionLogs].sort(
            (a, b) => (b.sortKey ?? 0) - (a.sortKey ?? 0),
          );
          setLogs(merged);
          updateRefreshIssue('activity', null);
        } else {
          updateRefreshIssue(
            'activity',
            'Recent activity refresh failed. Execution and risk logs may be stale.',
          );
        }

        if (dashboardFailures.length > 0) {
          updateRefreshIssue(
            'dashboard',
            `Dashboard refresh degraded: ${dashboardFailures.join(', ')} unavailable. Showing the last successful data where possible.`,
          );
        } else {
          updateRefreshIssue('dashboard', null);
        }
      } finally {
        if (dashboardAbortRef.current === controller) {
          dashboardAbortRef.current = null;
        }
        dashboardRefreshInFlightRef.current = false;
      }
    };

    void loadActiveDashboard();
    const interval = setInterval(() => {
      void loadActiveDashboard();
    }, ACTIVE_DASHBOARD_REFRESH_MS);

    return () => {
      cancelled = true;
      dashboardAbortRef.current?.abort();
      clearInterval(interval);
    };
  }, [session?.active, updateRefreshIssue]);

  useEffect(() => {
    if (!health || !summary) return;

    const baseKpis = buildKpis(summary);
    const extra: Kpi[] = [
      {
        label: 'Market data',
        value:
          health.market_data_status === 'warming_up'
            ? 'Warming up'
            : (health.market_data_ok ? 'OK' : 'Degraded'),
        hint:
          health.market_data_status === 'warming_up'
            ? 'Awaiting fresh startup ticks'
            : (health.market_data_reason ?? ''),
      },
      {
        label: 'Execution',
        value: health.execution_mode ?? 'dry-run',
        hint: health.rest_api_reachable ? 'API reachable' : 'API degraded',
      },
    ];

    setKpis([...baseKpis, ...extra]);
  }, [health, summary]);

  useEffect(() => {
    if (health?.ui_read_only) {
      setRiskFeedback({ tone: 'info', message: 'Backend is read-only. Kill switch changes are disabled.' });
      setStrategyFeedback('Backend is read-only. Strategy controls are disabled.');
    } else {
      setRiskFeedback((current) => (current?.tone === 'info' ? null : current));
      setStrategyFeedback((current) => (current === 'Backend is read-only. Strategy controls are disabled.' ? null : current));
    }
  }, [health?.ui_read_only]);

  const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

  const resolveExecutionMode = (state: SystemHealth | null): ExecutionMode | null => {
    if (!state) return null;
    const mode = state.current_mode ?? state.execution_mode ?? null;
    return mode === 'paper' || mode === 'live' ? mode : null;
  };

  const waitForExecutionMode = async (target: ExecutionMode, timeoutMs = 15000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const latest = await fetchSystemHealth({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS });
      if (
        latest &&
        resolveExecutionMode(latest) === target &&
        (target !== 'live' || Boolean(latest.execution_ok))
      ) {
        return latest;
      }
      await sleep(500);
    }
    return null;
  };

  const waitForSessionActivation = async (timeoutMs = 20000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const latest = await fetchSessionState({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS });
      if (latest?.active) {
        return latest;
      }
      await sleep(500);
    }
    return null;
  };

  const applyStartedSession = async (next: SessionStateResponse) => {
    setSession(next);
    setLoopIntervalDraft(next.loop_interval_sec);

    const [systemHealth, riskStatus] = await Promise.all([
      fetchSystemHealth({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS }),
      getRiskStatus({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS }),
    ]);

    if (systemHealth) {
      setHealth(systemHealth);
      setConnectionState(getConnectionStateFromHealth(systemHealth));
    }

    if (riskStatus) {
      setRisk(riskStatus);
    }
  };

  const performModeSwitch = async (
    mode: ExecutionMode,
    password?: string,
    certifyPaperTestsCompleted = false,
  ) => {
    setModeBusy(true);
    try {
      const confirmation = mode === 'live' ? 'ENABLE LIVE TRADING' : undefined;
      await setExecutionMode(mode, password, confirmation, certifyPaperTestsCompleted);

      const latest = await waitForExecutionMode(mode);
      if (latest) {
        setHealth(latest);
        setConnectionState(getConnectionStateFromHealth(latest));
        updateRefreshIssue('session-health', null);
        return;
      }

      const fallback = await fetchSystemHealth({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS });
      if (fallback) {
        setHealth(fallback);
        setConnectionState(getConnectionStateFromHealth(fallback));
        updateRefreshIssue('session-health', null);
      } else {
        setConnectionState('degraded');
        updateRefreshIssue(
          'session-health',
          'Execution mode changed, but fresh health data is unavailable. Please verify backend status before continuing.',
        );
      }
    } finally {
      setModeBusy(false);
    }
  };

  const handleStartSession = async (config: SessionConfigRequest) => {
    if (health?.ui_read_only) {
      throw new Error('Backend is in read-only mode.');
    }

    if (session?.reloading) {
      throw new Error('Krakked is reloading configuration. Please wait for reload to finish.');
    }

    const currentMode = resolveExecutionMode(health) ?? 'paper';
    const liveReady = currentMode === 'live' && Boolean(health?.execution_ok);

    if (config.mode === 'live' && !liveReady) {
      setPendingLiveStart(config);
      setShowLiveModal(true);
      return;
    }

    if (config.mode === 'paper' && currentMode === 'live') {
      await performModeSwitch('paper');
    }

    const updated = await updateSessionConfig(config);
    if (!updated) {
      throw new Error('Unable to configure session.');
    }

    const next = await startSession();
    const startedSession = next?.active ? next : await waitForSessionActivation();
    if (!startedSession) {
      throw new Error('Unable to start session.');
    }

    await applyStartedSession(startedSession);
  };

  const handleConfirmLiveStart = async (
    password: string,
    certifyPaperTestsCompleted: boolean,
  ) => {
    if (!pendingLiveStart) {
      setShowLiveModal(false);
      return;
    }

    await performModeSwitch('live', password, certifyPaperTestsCompleted);
    const updated = await updateSessionConfig(pendingLiveStart);
    if (!updated) {
      throw new Error('Unable to configure session.');
    }

    const next = await startSession();
    const startedSession = next?.active ? next : await waitForSessionActivation();
    if (!startedSession) {
      throw new Error('Unable to start session.');
    }

    await applyStartedSession(startedSession);
    setPendingLiveStart(null);
    setShowLiveModal(false);
  };

  const handleCloseLiveModal = () => {
    setShowLiveModal(false);
    setPendingLiveStart(null);
  };

  const handleCreateProfile = async (name: string) => {
    if (health?.ui_read_only) {
      throw new Error('Backend is in read-only mode.');
    }

    setSystemMessage(null);
    const created = await createProfile(name);
    const refreshedProfiles = await fetchProfiles();
    setProfiles(refreshedProfiles);

    const createdProfile = refreshedProfiles.find((profile) => profile.name === created.name);
    if (!createdProfile) {
      setSystemMessage({
        tone: 'info',
        message: `Profile "${created.name}" was created, but Krakked has not refreshed the profile list yet.`,
      });
      return;
    }

    try {
      const updated = await updateSessionConfig({ profile_name: created.name });
      if (!updated) {
        throw new Error('Unable to switch to the new profile yet.');
      }

      setSession(updated);
      setLoopIntervalDraft(updated.loop_interval_sec);

      const [systemHealth, systemConfig] = await Promise.all([
        fetchSystemHealth(),
        fetchSystemConfig({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS }).catch(() => null),
      ]);

      if (systemHealth) {
        setHealth(systemHealth);
        setConnectionState(getConnectionStateFromHealth(systemHealth));
      }

      setStartupMlEnabled(extractMlEnabledFromConfig(systemConfig, updated.ml_enabled));
      setSystemMessage({ tone: 'success', message: `Profile "${created.name}" created and selected.` });
    } catch (error) {
      const [systemHealth, systemConfig] = await Promise.all([
        fetchSystemHealth(),
        fetchSystemConfig({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS }).catch(() => null),
      ]);

      if (systemHealth) {
        setHealth(systemHealth);
        setConnectionState(getConnectionStateFromHealth(systemHealth));
      }

      setStartupMlEnabled(extractMlEnabledFromConfig(systemConfig, mlEnabled));
      setSystemMessage({
        tone: 'info',
        message: `Profile "${created.name}" created, but Krakked could not switch to it yet.`,
      });

      if (error instanceof Error) {
        console.warn(error.message);
      }
    }
  };

  const handleSaveConfig = async () => {
    if (health?.ui_read_only) {
      throw new Error('Backend is in read-only mode.');
    }

    const snapshot = await fetchSystemConfig();

    const sections = ['region', 'universe', 'market_data', 'portfolio', 'execution', 'risk', 'strategies', 'ml'] as const;
    const configPayload: Record<string, unknown> = {};
    for (const section of sections) {
      if (snapshot && typeof snapshot === 'object' && section in snapshot) {
        configPayload[section] = (snapshot as Record<string, unknown>)[section];
      }
    }

    const execution = configPayload.execution;
    if (execution && typeof execution === 'object') {
      const exec = execution as Record<string, unknown>;
      delete exec.mode;
      delete exec.allow_live_trading;
      delete exec.validate_only;
      delete exec.paper_tests_completed;
    }

    await applyConfig(configPayload);
    setSystemMessage({ tone: 'info', message: 'Configuration saved. Krakked is reloading…' });
    await loadSession();
  };

  const handleStopSession = async () => {
    const next = await stopSession();
    if (next) {
      setSession(next);
      setConnectionState('degraded');
    }
  };

  const handleLoopIntervalUpdate = async () => {
    if (!session) return;

    if (health?.ui_read_only) {
      setSystemMessage({ tone: 'error', message: 'Loop frequency is locked while the backend is read-only.' });
      return;
    }

    if (session.active) {
      setSystemMessage({ tone: 'error', message: 'Stop session to change loop frequency.' });
      return;
    }

    const updated = await updateSessionConfig({ loop_interval_sec: loopIntervalDraft });
    if (updated) {
      setSession(updated);
      setLoopIntervalDraft(updated.loop_interval_sec);
    }
  };

  const handleProfileChange = async (name: string) => {
    if (health?.ui_read_only) {
      throw new Error('Backend is in read-only mode.');
    }

    setSystemMessage(null);
    const updated = await updateSessionConfig({ profile_name: name });
    if (updated) {
      setSession(updated);
      setLoopIntervalDraft(updated.loop_interval_sec);

      const [systemHealth, systemConfig, profileSummaries] = await Promise.all([
        fetchSystemHealth(),
        fetchSystemConfig({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS }).catch(() => null),
        fetchProfiles(),
      ]);

      if (systemHealth) {
        setHealth(systemHealth);
        setConnectionState(getConnectionStateFromHealth(systemHealth));
      }

      setProfiles(profileSummaries);
      setStartupMlEnabled(extractMlEnabledFromConfig(systemConfig, updated.ml_enabled));
    } else {
      throw new Error('Failed to update profile.');
    }
  };

  const handleStartupMlToggle = async (enabled: boolean) => {
    if (health?.ui_read_only) {
      throw new Error('Backend is in read-only mode.');
    }

    await applyConfig({ ml: { enabled } });
    setStartupMlEnabled(enabled);
    setSystemMessage({ tone: 'info', message: 'Strategy settings updated. Krakked is reloading…' });
    await loadSession();
  };

  const handleToggleKillSwitch = async () => {
    if (!risk) {
      setRiskFeedback({ tone: 'error', message: 'Risk status unavailable. Please wait for the next refresh.' });
      return;
    }

    if (health?.ui_read_only) {
      setRiskFeedback({ tone: 'error', message: 'Backend is in read-only mode. Risk controls are locked.' });
      return;
    }

    const nextState = !risk.kill_switch_active;
    if (!nextState) {
      const confirmed = window.confirm('Disable the kill switch and allow trading to resume?');
      if (!confirmed) return;
    }

    setRiskBusy(true);
    const previous = risk;
    const updated = await setKillSwitch(nextState);

    if (updated) {
      setRisk(updated);
      setRiskFeedback({
        tone: 'success',
        message: updated.kill_switch_active ? 'Kill switch enabled. Execution halted.' : 'Kill switch disabled. Execution may resume.',
      });
    } else {
      setRisk(previous);
      setRiskFeedback({ tone: 'error', message: 'Unable to update kill switch. Restored prior state.' });
    }

    setRiskBusy(false);
  };

  const handleFlattenAll = async () => {
    if (health?.ui_read_only) {
      setSystemMessage({ tone: 'error', message: 'Read-only mode prevents flattening positions.' });
      return;
    }

    const confirmed = window.confirm(
      'Send flatten-all orders? This will attempt to close every open position immediately.',
    );
    if (!confirmed) return;

    try {
      const result = await flattenAllPositions();

      if (result.success) {
        if (result.warnings?.length) {
          setSystemMessage({
            tone: 'info',
            message: `Flatten-all submitted with warnings: ${result.warnings[0]}`,
          });
        } else {
          setSystemMessage({ tone: 'success', message: 'Flatten-all orders submitted.' });
        }

        return;
      }

      const errorMessage = result.errors?.length ? result.errors[0] : 'Unknown error.';
      setSystemMessage({
        tone: 'error',
        message: `Flatten-all could not close all positions: ${errorMessage}`,
      });
    } catch (error) {
      console.error(error);
      setSystemMessage({ tone: 'error', message: 'Unable to flatten positions. Please retry.' });
    }
  };

  const handleDownloadRuntimeConfig = async () => {
    try {
      const blob = await downloadRuntimeConfig();
      if (!blob) {
        setSystemMessage({
          tone: 'error',
          message: 'Failed to download config. Check server logs or network.',
        });
        return;
      }

      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      const date = new Date().toISOString().slice(0, 10);

      link.href = url;
      link.download = `krakked-config-${date}.json`;

      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      setSystemMessage({ tone: 'success', message: 'Config downloaded.' });
    } catch (error) {
      setSystemMessage({
        tone: 'error',
        message: 'Failed to download config. Check network or logs.',
      });
    }
  };

  const setStrategyBusyState = (strategyId: string, busyState: boolean) => {
    setStrategyBusy((previous) => {
      const next = new Set(previous);
      if (busyState) {
        next.add(strategyId);
      } else {
        next.delete(strategyId);
      }
      return next;
    });
  };

  const confirmLiveStrategyChange = (message: string) => {
    if (!liveStrategyGuardrails) {
      return true;
    }
    return window.confirm(message);
  };

  const handleStrategyToggle = async (strategyId: string, enabled: boolean) => {
    if (health?.ui_read_only) {
      setStrategyFeedback('Backend is read-only. Strategy controls are disabled.');
      return;
    }

    const confirmed = confirmLiveStrategyChange(
      enabled
        ? `Enable ${strategyId} in the active live session? This takes effect immediately and can change which strategy wins on overlapping signals.`
        : `Disable ${strategyId} in the active live session? This removes one current signal source immediately and can change active conflict outcomes.`,
    );
    if (!confirmed) {
      return;
    }

    setStrategyFeedback(null);
    setStrategyBusyState(strategyId, true);

    const previousStrategies = strategies.map((strategy) => ({ ...strategy }));
    setStrategies((current) =>
      recomputeEffectiveWeights(
        current.map((strategy) => (strategy.strategy_id === strategyId ? { ...strategy, enabled } : strategy)),
      ),
    );

    try {
      await setStrategyEnabled(strategyId, enabled);
      setStrategyFeedback(`Strategy ${strategyId} ${enabled ? 'enabled' : 'disabled'}.`);
    } catch (error) {
      setStrategies(previousStrategies);
      setStrategyFeedback(`Unable to update ${strategyId}. Please try again.`);
    } finally {
      setStrategyBusyState(strategyId, false);
    }
  };

  const handlePerStrategyBudgetChange = async (strategyId: string, valuePct: number) => {
    if (health?.ui_read_only) {
      setRiskConfigError('Backend is read-only. Risk config changes are disabled.');
      return;
    }

    if (!riskConfig) return;

    const nextMap = {
      ...riskConfig.max_per_strategy_pct,
      [strategyId]: valuePct,
    };

    setRiskConfig({ ...riskConfig, max_per_strategy_pct: nextMap });
    setRiskConfigBusy(true);
    setRiskConfigError(null);

    const updated = await updateRiskConfig({ max_per_strategy_pct: nextMap });

    if (!updated) {
      setRiskConfigError('Unable to update risk config. Restored prior values.');
      setRiskConfig(riskConfig);
    } else {
      setRiskConfig(updated);
    }

    setRiskConfigBusy(false);
  };

  const handleRiskConfigFieldChange = async (
    field: keyof RiskConfig,
    value: number | boolean,
  ) => {
    if (health?.ui_read_only) {
      setRiskConfigError('Backend is read-only. Risk config changes are disabled.');
      return;
    }
    if (!riskConfig) return;

    const previous = riskConfig;
    const patch: Partial<RiskConfig> = { [field]: value } as Partial<RiskConfig>;

    setRiskConfig({ ...riskConfig, [field]: value });
    setRiskConfigBusy(true);
    setRiskConfigError(null);

    const updated = await updateRiskConfig(patch);

    if (!updated) {
      setRiskConfigError('Unable to update risk config. Restored prior values.');
      setRiskConfig(previous);
    } else {
      setRiskConfig(updated);
    }

    setRiskConfigBusy(false);
  };

  const handleRiskProfileChange = async (strategyId: string, profile: StrategyRiskProfile) => {
    if (health?.ui_read_only) {
      setStrategyFeedback('Backend is read-only. Strategy controls are disabled.');
      return;
    }

    setStrategyFeedback(null);
    setStrategyBusyState(strategyId, true);

    const previousProfile = strategyRisk[strategyId];
    setStrategyRisk((current) => ({ ...current, [strategyId]: profile }));

    try {
      await patchStrategyConfig(strategyId, { params: { risk_profile: profile } });
      setStrategyFeedback(`Updated ${strategyId} risk profile to ${profile}.`);
    } catch (error) {
      setStrategyRisk((current) => ({ ...current, [strategyId]: previousProfile }));
      setStrategyFeedback(`Unable to update risk profile for ${strategyId}.`);
    } finally {
      setStrategyBusyState(strategyId, false);
    }
  };

  const handleStrategyWeightChange = async (strategyId: string, weight: number) => {
    if (health?.ui_read_only) {
      setStrategyFeedback('Backend is read-only. Strategy controls are disabled.');
      return;
    }

    const previousWeight =
      strategies.find((strategy) => strategy.strategy_id === strategyId)?.configured_weight ?? 100;
    if (previousWeight === weight) {
      return;
    }

    setStrategyFeedback(null);
    setStrategyBusyState(strategyId, true);

    setStrategies((current) =>
      current.map((strategy) =>
        strategy.strategy_id === strategyId
          ? { ...strategy, configured_weight: weight }
          : strategy,
      ),
    );

    try {
      await patchStrategyConfig(strategyId, { strategy_weight: weight });
      setStrategies((current) => recomputeEffectiveWeights(
        current.map((strategy) =>
          strategy.strategy_id === strategyId ? { ...strategy, configured_weight: weight } : strategy,
        ),
      ));
      setStrategyFeedback(`Updated ${strategyId} weight to ${weight}.`);
    } catch (error) {
      setStrategies((current) =>
        current.map((strategy) =>
          strategy.strategy_id === strategyId
            ? { ...strategy, configured_weight: previousWeight }
            : strategy,
        ),
      );
      setStrategyFeedback(`Unable to update weight for ${strategyId}.`);
    } finally {
      setStrategyBusyState(strategyId, false);
    }
  };

  const handleRestoreStarterStrategies = async () => {
    if (health?.ui_read_only) {
      setStrategyFeedback('Backend is read-only. Starter recovery is disabled.');
      return;
    }

    const existingStarterIds = STARTER_STRATEGY_IDS.filter((strategyId) =>
      strategies.some((strategy) => strategy.strategy_id === strategyId),
    );
    if (existingStarterIds.length === 0) {
      setStrategyFeedback('Starter strategies are not available on this profile yet.');
      return;
    }

    const confirmed = confirmLiveStrategyChange(
      'Restore the starter strategy pack? This re-enables the default starter strategies and resets each starter weight to 100 in the active session.',
    );
    if (!confirmed) {
      return;
    }

    setStrategyFeedback(null);
    const previousStrategies = strategies.map((strategy) => ({ ...strategy }));
    existingStarterIds.forEach((strategyId) => setStrategyBusyState(strategyId, true));

    setStrategies((current) => recomputeEffectiveWeights(
      current.map((strategy) => (
        STARTER_STRATEGY_IDS.includes(strategy.strategy_id as (typeof STARTER_STRATEGY_IDS)[number])
          ? { ...strategy, enabled: true, configured_weight: 100 }
          : strategy
      )),
    ));

    try {
      for (const strategyId of existingStarterIds) {
        await setStrategyEnabled(strategyId, true);
        await patchStrategyConfig(strategyId, { strategy_weight: 100 });
      }

      const refreshed = await fetchStrategies({ timeoutMs: ACTIVE_RESOURCE_TIMEOUT_MS });
      if (refreshed) {
        setStrategies(refreshed);
      }
      setStrategyFeedback('Starter strategy pack restored and enabled.');
    } catch (error) {
      setStrategies(previousStrategies);
      setStrategyFeedback('Unable to restore the starter strategy pack.');
    } finally {
      existingStarterIds.forEach((strategyId) => setStrategyBusyState(strategyId, false));
    }
  };

  const handleLearningToggle = async (strategyId: string, enabled: boolean) => {
    if (health?.ui_read_only) {
      setStrategyFeedback('Backend is read-only. Strategy controls are disabled.');
      return;
    }

    setStrategyFeedback(null);
    setStrategyBusyState(strategyId, true);

    const previous = strategyLearning[strategyId];
    setStrategyLearning((current) => ({ ...current, [strategyId]: enabled }));
    setStrategies((current) =>
      current.map((strategy) =>
        strategy.strategy_id === strategyId
          ? { ...strategy, params: { ...strategy.params, continuous_learning: enabled } }
          : strategy,
      ),
    );

    try {
      await patchStrategyConfig(strategyId, { params: { continuous_learning: enabled } });
      setStrategyFeedback(`Updated ${strategyId} learning ${enabled ? 'on' : 'off'}.`);
    } catch (error) {
      setStrategyLearning((current) => ({ ...current, [strategyId]: previous }));
      setStrategies((current) =>
        current.map((strategy) =>
          strategy.strategy_id === strategyId
            ? { ...strategy, params: { ...strategy.params, continuous_learning: previous } }
            : strategy,
        ),
      );
      setStrategyFeedback(`Unable to update learning for ${strategyId}.`);
    } finally {
      setStrategyBusyState(strategyId, false);
    }
  };

  const handlePresetChange = async (preset: RiskPresetName) => {
    if (health?.ui_read_only) {
      setRiskConfigError('Backend is read-only. Risk config changes are disabled.');
      return;
    }

    setRiskConfigBusy(true);
    setRiskConfigError(null);
    try {
      const updated = await applyRiskPreset(preset);

      if (!updated) {
        setRiskConfigError('Unable to apply preset. Restored prior values.');
        return;
      }

      setRiskConfig(updated);
      setRiskFeedback({ tone: 'success', message: `Applied ${preset} preset.` });
      setCurrentPreset(preset);

      const [strategiesData, perf, status] = await Promise.all([
        fetchStrategies(),
        fetchStrategyPerformance(),
        getRiskStatus(),
      ]);

      if (status) setRisk(status);

      if (strategiesData) {
        setStrategies(strategiesData);
        setStrategyRisk((previous) => {
          const next = { ...previous };
          strategiesData.forEach((strategy) => {
            const riskProfile = strategy.params?.risk_profile;
            if (isRiskProfile(riskProfile)) {
              next[strategy.strategy_id] = riskProfile;
            } else if (!next[strategy.strategy_id]) {
              next[strategy.strategy_id] = 'balanced';
            }
          });
          return next;
        });
      }

      if (perf) {
        const byId: Record<string, StrategyPerformance> = {};
        perf.forEach((entry) => {
          byId[entry.strategy_id] = entry;
        });
        setStrategyPerformance(byId);
      }
    } finally {
      setRiskConfigBusy(false);
    }
  };

  if (sessionLoading) {
    return (
      <div className="app-shell">
        <div className="background" aria-hidden="true" />
        <div className="layout__status-row" style={{ gap: '0.5rem' }}>
          <span className="pill pill--muted">Loading session…</span>
        </div>
      </div>
    );
  }

  if (!session?.active) {
    return (
      <>
        <StartupScreen
          profiles={profiles}
          activeProfileName={session?.profile_name ?? null}
          readOnly={Boolean(health?.ui_read_only)}
          systemMode={resolveExecutionMode(health)}
          modeBusy={modeBusy || startupReloading}
          systemMessage={systemMessage}
          startupMlEnabled={startupMlEnabled}
          onCreateProfile={handleCreateProfile}
          onProfileChange={handleProfileChange}
          onSaveConfig={handleSaveConfig}
          onMlToggle={handleStartupMlToggle}
          onStart={handleStartSession}
        />
        <LiveModeModal isOpen={showLiveModal} onClose={handleCloseLiveModal} onConfirm={handleConfirmLiveStart} />
      </>
    );
  }

  const dashboardAlerts: DashboardAlert[] = [];
  if (health?.portfolio_sync_ok === false) {
    dashboardAlerts.push({
      id: 'portfolio-sync',
      tone: 'danger',
      title: 'Portfolio sync degraded',
      message: health.portfolio_sync_reason || 'Krakked could not complete the latest portfolio sync. Positions and balances may be stale.',
    });
  }
  if (session.mode === 'paper' && isExchangeBalanceBaseline(health?.portfolio_baseline)) {
    dashboardAlerts.push({
      id: 'paper-baseline',
      tone: 'info',
      title: 'Paper mode uses exchange reference balances',
      message: 'Krakked is validating orders against Kraken while showing current exchange balances as the paper baseline. No live orders are sent.',
    });
  }
  if (health && !health.market_data_ok) {
    dashboardAlerts.push({
      id: 'market-data',
      tone:
        health.market_data_status === 'warming_up'
          ? 'info'
          : (health.market_data_stale ? 'warning' : 'danger'),
      title:
        health.market_data_status === 'warming_up'
          ? 'Market data warming up'
          : 'Market data degraded',
      message:
        health.market_data_status === 'warming_up'
          ? 'Streaming is online, but Krakked is still waiting for fresh startup data.'
          : (health.market_data_detail || health.market_data_reason || 'Streaming or REST market data is degraded.'),
    });
  }
  if (health?.drift_detected) {
    dashboardAlerts.push({
      id: 'drift',
      tone: 'warning',
      title: 'Portfolio drift detected',
      message: health.drift_reason || 'Expected positions and tracked balances do not line up cleanly.',
    });
  }
  if (refreshIssues.strategies) {
    dashboardAlerts.push({
      id: 'strategies',
      tone: 'warning',
      title: 'Strategy state refresh failed',
      message: refreshIssues.strategies,
    });
  }
  if (health?.ui_read_only) {
    dashboardAlerts.push({
      id: 'read-only',
      tone: 'info',
      title: 'Read-only mode',
      message: 'Urgent controls stay visible, but configuration changes are blocked while the backend is read-only.',
    });
  }
  const replayWarnings = takeImportantWarnings(latestReplay?.notable_warnings, 2);
  if (latestReplay?.available && replayWarnings.length > 0) {
    dashboardAlerts.push({
      id: 'latest-replay',
      tone:
        latestReplay.trust_level === 'decision_helpful'
          ? 'info'
          : latestReplay.trust_level === 'limited'
            ? 'warning'
            : 'danger',
      title: 'Latest replay warning',
      message: replayWarnings.join(' '),
    });
  }

  const topAsset = exposure?.by_asset
    ?.filter((asset) => typeof asset.pct_of_equity === 'number')
    .sort((a, b) => (b.pct_of_equity ?? 0) - (a.pct_of_equity ?? 0))[0] ?? null;
  const topStrategyExposure = risk
    ? Object.entries(risk.per_strategy_exposure_pct)
        .sort(([, left], [, right]) => right - left)[0] ?? null
    : null;
  const strategySummary = [...strategies]
    .sort((left, right) => {
      const enabledDelta = Number(right.enabled) - Number(left.enabled);
      if (enabledDelta !== 0) return enabledDelta;
      return (right.effective_weight_pct ?? 0) - (left.effective_weight_pct ?? 0);
    })
    .slice(0, 5);
  const activeStarterCount = strategies.filter(
    (strategy) =>
      STARTER_STRATEGY_IDS.includes(strategy.strategy_id as (typeof STARTER_STRATEGY_IDS)[number]) && strategy.enabled,
  ).length;
  const noActiveStrategies = strategies.every((strategy) => !strategy.enabled);
  const runtimeTrust = getRuntimeTrust(health, connectionState);

  const systemStatusItems = [
    {
      label: 'Runtime trust',
      value: runtimeTrust.label,
      tone: runtimeTrust.sidebarTone,
      hint: runtimeTrust.hint,
    },
    {
      label: 'Mode',
      value: session.mode === 'live' ? 'Live' : 'Paper',
      tone: session.mode === 'live' ? 'warning' as const : 'ok' as const,
      hint:
        session.mode === 'paper' && isExchangeBalanceBaseline(health?.portfolio_baseline)
          ? 'Validate-only with current exchange balances as reference'
          : (session.profile_name ? `Profile ${session.profile_name}` : 'No active profile'),
    },
    {
      label: 'Trading',
      value: risk?.kill_switch_active ? 'Paused' : 'Active',
      tone: risk?.kill_switch_active ? 'danger' as const : 'ok' as const,
      hint: `Loop ${session.loop_interval_sec.toFixed(1)}s`,
    },
  ];

  const integrityItems = [
    {
      label: 'Portfolio Sync',
      value: health?.portfolio_sync_ok ? 'Healthy' : 'Degraded',
      tone: health?.portfolio_sync_ok ? 'ok' as const : 'danger' as const,
      hint: health?.portfolio_sync_ok
        ? (
          session.mode === 'paper' && isExchangeBalanceBaseline(health?.portfolio_baseline)
            ? `Exchange balances loaded ${formatDateTime(health.portfolio_last_sync_at)}`
            : `Last sync ${formatDateTime(health.portfolio_last_sync_at)}`
        )
        : (health?.portfolio_sync_reason || 'Latest sync failed'),
    },
    {
      label: 'Market Data',
      value:
        health?.market_data_status === 'warming_up'
          ? 'Warming up'
          : (health?.market_data_ok ? 'Healthy' : (health?.market_data_stale ? 'Degraded' : 'Unavailable')),
      tone:
        health?.market_data_status === 'warming_up'
          ? 'warning' as const
          : (health?.market_data_ok ? 'ok' as const : (health?.market_data_stale ? 'warning' as const : 'danger' as const)),
      hint:
        health?.market_data_status === 'warming_up'
          ? 'Streaming is online; waiting for fresh startup data'
          : (health?.market_data_detail || health?.market_data_reason || `${health?.streaming_pairs ?? 0} pairs streaming`),
    },
    {
      label: 'Drift',
      value: health?.drift_detected ? 'Detected' : 'Clear',
      tone: health?.drift_detected ? 'warning' as const : 'ok' as const,
      hint: health?.drift_reason || 'Portfolio within expected bounds',
    },
  ];

  const sidebarActions = [
    {
      label: risk?.kill_switch_active ? 'Resume Trading' : 'Pause Trading',
      disabled: riskBusy || !risk || health?.ui_read_only,
      onClick: handleToggleKillSwitch,
    },
    {
      label: 'Flatten All Positions',
      disabled: Boolean(health?.ui_read_only),
      onClick: handleFlattenAll,
    },
    {
      label: 'Stop Session',
      tone: 'danger' as const,
      disabled: Boolean(health?.ui_read_only),
      onClick: handleStopSession,
    },
  ];

  const sidebarMenu = [
    { label: 'Overview', href: '#overview' },
    { label: 'Positions', href: '#positions' },
    { label: 'Strategies', href: '#strategies' },
    { label: 'Risk', href: '#risk' },
    { label: 'Activity', href: '#activity' },
    { label: 'Settings', href: '#settings' },
  ];

  return (
    <Layout
      title={session.mode === 'live' ? 'Live Trading Overview' : 'Paper Trading Overview'}
      subtitle={
        <div className="layout__status-row">
          {session.profile_name ? <span className="pill pill--muted">Profile: {session.profile_name}</span> : null}
          <span className="pill pill--muted">Mode: {session.mode === 'live' ? 'Live' : 'Paper'}</span>
          <span className={`pill ${
            runtimeTrust.sidebarTone === 'ok'
              ? 'pill--success'
              : runtimeTrust.sidebarTone === 'danger'
                ? 'pill--danger'
                : 'pill--warning'
          }`}
          >
            Runtime trust: {runtimeTrust.label}
          </span>
          {session.mode === 'paper' && isExchangeBalanceBaseline(health?.portfolio_baseline) ? (
            <span className="pill pill--info">Portfolio: Exchange reference</span>
          ) : null}

          <span
            className={
              risk?.kill_switch_active === true
                ? 'pill pill--danger'
                : risk?.kill_switch_active === false
                  ? 'pill pill--success'
                  : 'pill pill--muted'
            }
          >
            {risk?.kill_switch_active === true
              ? 'Trading paused'
              : risk?.kill_switch_active === false
                ? 'Trading active'
                : (session.lifecycle === 'starting_session' ? 'Starting session' : 'Trading status unavailable')}
          </span>

          {currentPreset && (
            <span
              className="pill pill--muted"
              title={formatPresetSummary(currentPreset)}
            >
              Preset: {RISK_PRESET_META[currentPreset].label}
            </span>
          )}

          <span className={mlEnabled ? 'pill pill--info' : 'pill pill--muted'}>
            ML: {mlEnabled ? 'On' : 'Off'}
          </span>

          <span className="pill pill--muted">Loop: {session.loop_interval_sec.toFixed(1)}s</span>

          {health?.ui_read_only && <span className="pill pill--warning">Read-only</span>}

          {session.emergency_flatten && (
            <span className="pill pill--danger">Emergency Flattening</span>
          )}
        </div>
      }
      sidebar={
        <Sidebar
          systemStatus={systemStatusItems}
          integrity={integrityItems}
          actions={sidebarActions}
          menu={sidebarMenu}
          note="Only implemented sections are shown here. Roadmap surfaces stay out of the cockpit until they are real."
        />
      }
      actions={
        <div className="layout__action-buttons">
          <button type="button" className="ghost-button" onClick={onLogout}>
            Log out
          </button>
        </div>
      }
    >
      {systemMessage ? <div className={`feedback feedback--${systemMessage.tone}`}>{systemMessage.message}</div> : null}

      {dashboardAlerts.length > 0 ? (
        <section className="alert-strip" aria-label="System alerts">
          {dashboardAlerts.map((alert) => (
            <article key={alert.id} className={`alert-card alert-card--${alert.tone}`}>
              <p className="alert-card__title">{alert.title}</p>
              <p className="alert-card__message">{alert.message}</p>
            </article>
          ))}
        </section>
      ) : null}

      <ReplaySummaryPanel replay={latestReplay} />

      <section id="overview" className="dashboard-anchor">
        <KpiGrid items={kpis} />
      </section>

      <div className="dashboard-grid dashboard-grid--primary">
        <section id="positions" className="dashboard-anchor">
          <PositionsTable
            positions={activePositions}
            title="Positions"
            hint={
              session.mode === 'paper' && isExchangeBalanceBaseline(summary?.portfolio_baseline)
                ? 'Reference balances drive paper equity. Tracked paper positions appear here only after Krakked records them locally.'
                : (summary?.last_snapshot_ts ? `Snapshot ${formatDateTime(summary.last_snapshot_ts)}` : 'Awaiting the first portfolio snapshot')
            }
            emptyMessage={
              session.mode === 'paper' && isExchangeBalanceBaseline(summary?.portfolio_baseline)
                ? 'No tracked paper positions yet. Reference balances still contribute to equity and exposure.'
                : 'No active positions right now.'
            }
          />
          {dustPositions.length > 0 && (
            <PositionsTable
              positions={dustPositions}
              title="Dust"
              hint="Below Kraken minimum order size; these become actionable once accumulated."
            />
          )}
        </section>

        <section className="panel integrity-panel">
          <div className="panel__header">
            <div>
              <h2>Portfolio Integrity</h2>
              <p className="panel__hint">Health based on sync state, drift, exposure, and live runtime status.</p>
            </div>
          </div>
          <div className="integrity-panel__list">
            <div className="integrity-panel__item">
              <p className="integrity-panel__label">Portfolio sync</p>
              <p className={`integrity-panel__value${health?.portfolio_sync_ok ? ' text--success' : ' text--danger'}`}>
                {health?.portfolio_sync_ok ? 'Healthy' : 'Degraded'}
              </p>
              <p className="integrity-panel__hint">
                {health?.portfolio_sync_ok
                  ? (
                    session.mode === 'paper' && isExchangeBalanceBaseline(health?.portfolio_baseline)
                      ? `Current exchange balances loaded ${formatDateTime(health?.portfolio_last_sync_at)}`
                      : `Last successful sync ${formatDateTime(health?.portfolio_last_sync_at)}`
                  )
                  : (health?.portfolio_sync_reason || 'Latest sync failed')}
              </p>
            </div>
            <div className="integrity-panel__item">
              <p className="integrity-panel__label">Market data</p>
              <p
                className={`integrity-panel__value${
                  health?.market_data_status === 'warming_up'
                    ? ''
                    : (health?.market_data_ok ? ' text--success' : ' text--danger')
                }`}
              >
                {health?.market_data_status === 'warming_up'
                  ? 'Warming up'
                  : (health?.market_data_ok ? 'Healthy' : (health?.market_data_stale ? 'Degraded' : 'Unavailable'))}
              </p>
              <p className="integrity-panel__hint">
                {health?.market_data_status === 'warming_up'
                  ? 'Streaming is online; waiting for fresh startup data'
                  : (health?.market_data_detail || health?.market_data_reason || `${health?.streaming_pairs ?? 0} pairs streaming`)}
              </p>
            </div>
            <div className="integrity-panel__item">
              <p className="integrity-panel__label">Drift monitor</p>
              <p className={`integrity-panel__value${health?.drift_detected ? ' text--danger' : ' text--success'}`}>
                {health?.drift_detected ? 'Drift detected' : 'Clear'}
              </p>
              <p className="integrity-panel__hint">
                {health?.drift_reason || 'Expected positions and local balances remain aligned.'}
              </p>
            </div>
            <div className="integrity-panel__item">
              <p className="integrity-panel__label">Top asset exposure</p>
              <p className="integrity-panel__value">
                {topAsset ? `${topAsset.asset} ${formatPercent(topAsset.pct_of_equity)}` : 'No exposure'}
              </p>
              <p className="integrity-panel__hint">
                {topAsset ? formatCurrency(topAsset.value_usd) : 'No asset exposure reported yet.'}
              </p>
            </div>
          </div>
        </section>
      </div>

      <div className="dashboard-grid dashboard-grid--secondary">
        <section className="panel strategy-summary-panel">
          <div className="panel__header">
            <div>
              <h2>Strategy Summary</h2>
              <p className="panel__hint">Starter-pack posture, current leaders, and conflict winners at a glance.</p>
            </div>
          </div>
          {strategySummary.length === 0 || noActiveStrategies ? (
            <div className="panel__empty strategy-scorecard__empty">
              <p>No strategies are active for this profile right now.</p>
              <p>The recommended beginner starter pack can be restored in one step.</p>
              <button
                type="button"
                className="ghost-button"
                onClick={handleRestoreStarterStrategies}
                disabled={Boolean(health?.ui_read_only)}
                title={liveStrategyGuardrails
                  ? 'Live change: restore the default starter pack and reset starter weights to 100.'
                  : 'Re-enable the default beginner starter strategy pack.'}
              >
                Restore starter pack
              </button>
            </div>
          ) : (
            <div className="strategy-scorecard-grid">
              {strategySummary.map((strategy) => {
                const perf = strategyPerformance[strategy.strategy_id];
                const latestIntent = strategy.last_intents?.[0];
                const latestConflict = strategy.conflict_summary?.[0];
                const momentum = getStrategyMomentum(strategy, perf);
                const drawdown = getDrawdownState(perf?.max_drawdown_pct);
                const freshAt = strategy.last_actions_at || strategy.last_intents_at;
                return (
                  <article key={strategy.strategy_id} className="strategy-scorecard">
                    <div className="strategy-scorecard__header">
                      <div>
                        <p className="strategy-scorecard__title">{strategy.label}</p>
                        <p className="strategy-scorecard__subtitle">{strategy.strategy_id}</p>
                      </div>
                      <div className="strategy-scorecard__pills">
                        <span className={`pill ${strategy.enabled ? 'pill--long' : 'pill--neutral'}`}>
                          {strategy.enabled ? 'Active' : 'Paused'}
                        </span>
                        <span
                          className={`pill ${
                            momentum.tone === 'success'
                              ? 'pill--long'
                              : momentum.tone === 'warning'
                                ? 'pill--warning'
                                : momentum.tone === 'danger'
                                  ? 'pill--danger'
                                  : 'pill--info'
                          }`}
                        >
                          {momentum.label}
                        </span>
                      </div>
                    </div>
                    <div className="strategy-scorecard__metrics">
                      <div>
                        <span className="strategy-scorecard__label">Configured</span>
                        <strong>{strategy.configured_weight}</strong>
                      </div>
                      <div>
                        <span className="strategy-scorecard__label">Effective share</span>
                        <strong>{typeof strategy.effective_weight_pct === 'number' ? `${strategy.effective_weight_pct.toFixed(1)}%` : '—'}</strong>
                      </div>
                      <div>
                        <span className="strategy-scorecard__label">Recent PnL</span>
                        <strong className={(perf?.realized_pnl_quote ?? 0) < 0 ? 'text--danger' : 'text--success'}>
                          {perf ? formatCurrency(perf.realized_pnl_quote) : 'No trades'}
                        </strong>
                      </div>
                      <div>
                        <span className="strategy-scorecard__label">Freshness</span>
                        <strong>{freshAt ? formatTimestamp(freshAt) : 'No signal yet'}</strong>
                      </div>
                    </div>
                    <div className="strategy-scorecard__detail-row">
                      <span className="strategy-scorecard__detail-label">Latest</span>
                      <span>
                        {latestIntent
                          ? `${latestIntent.side} ${latestIntent.pair} (${latestIntent.timeframe})`
                          : 'No recent signal'}
                      </span>
                    </div>
                    <div className="strategy-scorecard__detail-row">
                      <span className="strategy-scorecard__detail-label">Drawdown</span>
                      <span className={`pill ${
                        drawdown.tone === 'success'
                          ? 'pill--long'
                          : drawdown.tone === 'warning'
                            ? 'pill--warning'
                            : drawdown.tone === 'danger'
                              ? 'pill--danger'
                              : 'pill--neutral'
                      }`}>
                        {drawdown.label}{perf ? ` ${perf.max_drawdown_pct.toFixed(1)}%` : ''}
                      </span>
                    </div>
                    <div className="strategy-scorecard__detail-row">
                      <span className="strategy-scorecard__detail-label">Conflict</span>
                      <span>
                        {latestConflict
                          ? `${latestConflict.pair}: ${latestConflict.winning_reason}`
                          : 'No active conflict'}
                      </span>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          {activeStarterCount < STARTER_STRATEGY_IDS.length ? (
            <div className="strategy-summary-panel__footer">
              <p className="panel__hint">
                {activeStarterCount === 0
                  ? 'The starter pack is fully paused.'
                  : `${STARTER_STRATEGY_IDS.length - activeStarterCount} starter strategies are currently disabled.`}
              </p>
              <button
                type="button"
                className="ghost-button"
                onClick={handleRestoreStarterStrategies}
                disabled={Boolean(health?.ui_read_only)}
                title={liveStrategyGuardrails
                  ? 'Live change: re-enable the starter pack and reset starter weights to 100.'
                  : 'Re-enable the default beginner starter strategy pack.'}
              >
                Re-enable starter pack
              </button>
            </div>
          ) : null}
        </section>

        <section className="panel risk-snapshot-panel">
          <div className="panel__header">
            <div>
              <h2>Risk Snapshot</h2>
              <p className="panel__hint">What matters before you decide whether to intervene.</p>
            </div>
          </div>
          <div className="snapshot-grid">
            <div className="snapshot-grid__item">
              <span className="snapshot-grid__label">Kill switch</span>
              <span className={`snapshot-grid__value${risk?.kill_switch_active ? ' text--danger' : ' text--success'}`}>
                {risk?.kill_switch_active ? 'Paused' : 'Active'}
              </span>
            </div>
            <div className="snapshot-grid__item">
              <span className="snapshot-grid__label">Total exposure</span>
              <span className="snapshot-grid__value">{risk ? `${risk.total_exposure_pct.toFixed(1)}%` : '—'}</span>
            </div>
            <div className="snapshot-grid__item">
              <span className="snapshot-grid__label">Daily drawdown</span>
              <span className={`snapshot-grid__value${(risk?.daily_drawdown_pct ?? 0) > 0 ? ' text--danger' : ''}`}>
                {risk ? `${risk.daily_drawdown_pct.toFixed(1)}%` : '—'}
              </span>
            </div>
            <div className="snapshot-grid__item">
              <span className="snapshot-grid__label">Preset</span>
              <span className="snapshot-grid__value">
                {currentPreset ? RISK_PRESET_META[currentPreset].label : 'Custom'}
              </span>
            </div>
            <div className="snapshot-grid__item snapshot-grid__item--wide">
              <span className="snapshot-grid__label">Top strategy exposure</span>
              <span className="snapshot-grid__value">
                {topStrategyExposure ? `${topStrategyExposure[0]} ${topStrategyExposure[1].toFixed(1)}%` : 'No strategy exposure'}
              </span>
            </div>
          </div>
        </section>
      </div>

      <section id="activity" className="dashboard-anchor">
        <LogPanel
          entries={logs}
          title="Activity Log"
          hint="Recent executions and meaningful risk decisions, newest first."
        />
      </section>

      <section id="strategies" className="dashboard-anchor dashboard-section">
        <div className="section-header">
          <div>
            <p className="section-header__eyebrow">Strategies</p>
            <h2 className="section-header__title">Strategy controls</h2>
          </div>
        </div>
        <StrategiesPanel
          strategies={strategies}
          performance={strategyPerformance}
          riskSelections={strategyRisk}
          learningSelections={strategyLearning}
          busy={strategyBusy}
          readOnly={Boolean(health?.ui_read_only)}
          liveMode={liveStrategyGuardrails}
          feedback={strategyFeedback}
          onToggle={handleStrategyToggle}
          onWeightChange={handleStrategyWeightChange}
          onRiskProfileChange={handleRiskProfileChange}
          onLearningToggle={handleLearningToggle}
        />
      </section>

      <section id="risk" className="dashboard-anchor dashboard-section">
        <div className="section-header">
          <div>
            <p className="section-header__eyebrow">Risk</p>
            <h2 className="section-header__title">Risk controls and budgets</h2>
          </div>
        </div>
        <RiskPanel
          status={risk}
          riskConfig={riskConfig}
          readOnly={Boolean(health?.ui_read_only)}
          busy={riskBusy}
          presetBusy={riskConfigBusy}
          presetOptions={RISK_PRESET_OPTIONS}
          onPresetChange={handlePresetChange}
          onToggle={handleToggleKillSwitch}
          currentPreset={currentPreset}
          feedback={riskFeedback}
        />

        {riskConfig ? (
          <section className="panel">
            <div className="panel__header">
              <h2>Risk settings & budgets</h2>
              {riskConfigBusy ? <span className="pill pill--info">Saving…</span> : null}
            </div>
            <p className="panel__description">
              Global risk limits and per-strategy caps. Changes apply immediately.
            </p>

            {riskConfigError ? <p className="field__error">{riskConfigError}</p> : null}

            <div className="risk-config__grid risk-config__grid--global">
            <div className="field">
              <label>Max risk per trade (%)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={riskConfig.max_risk_per_trade_pct}
                onChange={(e) => handleRiskConfigFieldChange('max_risk_per_trade_pct', Number(e.target.value))}
              />
            </div>

            <div className="field">
              <label>Max portfolio risk (%)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={riskConfig.max_portfolio_risk_pct}
                onChange={(e) => handleRiskConfigFieldChange('max_portfolio_risk_pct', Number(e.target.value))}
              />
            </div>

            <div className="field">
              <label>Max daily drawdown (%)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={riskConfig.max_daily_drawdown_pct}
                onChange={(e) => handleRiskConfigFieldChange('max_daily_drawdown_pct', Number(e.target.value))}
              />
            </div>

            <div className="field">
              <label>Max open positions</label>
              <input
                type="number"
                min={0}
                value={riskConfig.max_open_positions}
                onChange={(e) => handleRiskConfigFieldChange('max_open_positions', Number(e.target.value))}
              />
            </div>

            <div className="field">
              <label>Max per-asset exposure (%)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={riskConfig.max_per_asset_pct}
                onChange={(e) => handleRiskConfigFieldChange('max_per_asset_pct', Number(e.target.value))}
              />
            </div>

            <div className="field">
              <label>Min 24h liquidity (USD)</label>
              <input
                type="number"
                min={0}
                value={riskConfig.min_liquidity_24h_usd}
                onChange={(e) => handleRiskConfigFieldChange('min_liquidity_24h_usd', Number(e.target.value))}
              />
            </div>

            <div className="field field--checkbox">
              <label>
                <input
                  type="checkbox"
                  checked={riskConfig.kill_switch_on_drift}
                  onChange={(e) => handleRiskConfigFieldChange('kill_switch_on_drift', e.target.checked)}
                />
                Kill switch on drift
              </label>
            </div>

            <div className="field field--checkbox">
              <label>
                <input
                  type="checkbox"
                  checked={riskConfig.include_manual_positions}
                  onChange={(e) => handleRiskConfigFieldChange('include_manual_positions', e.target.checked)}
                />
                Include manual positions in risk
              </label>
            </div>

            <div className="field">
              <label>Volatility lookback (bars)</label>
              <input
                type="number"
                min={1}
                value={riskConfig.volatility_lookback_bars}
                onChange={(e) => handleRiskConfigFieldChange('volatility_lookback_bars', Number(e.target.value))}
              />
            </div>

            <div className="field field--checkbox">
              <label>
                <input
                  type="checkbox"
                  checked={riskConfig.dynamic_allocation_enabled}
                  onChange={(e) => handleRiskConfigFieldChange('dynamic_allocation_enabled', e.target.checked)}
                />
                Dynamic strategy weighting
              </label>
            </div>

            <div className="field">
              <label>Dynamic allocation lookback (hours)</label>
              <input
                type="number"
                min={1}
                value={riskConfig.dynamic_allocation_lookback_hours}
                onChange={(e) => handleRiskConfigFieldChange('dynamic_allocation_lookback_hours', Number(e.target.value))}
              />
            </div>

            <div className="field">
              <label>Min strategy weight (%)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={riskConfig.min_strategy_weight_pct}
                onChange={(e) => handleRiskConfigFieldChange('min_strategy_weight_pct', Number(e.target.value))}
              />
            </div>

            <div className="field">
              <label>Max strategy weight (%)</label>
              <input
                type="number"
                min={0}
                max={100}
                value={riskConfig.max_strategy_weight_pct}
                onChange={(e) => handleRiskConfigFieldChange('max_strategy_weight_pct', Number(e.target.value))}
              />
            </div>
          </div>

          <h3>Per-strategy caps</h3>
          <div className="risk-config__grid">
            {Object.entries(riskConfig.max_per_strategy_pct).map(([strategyId, pct]) => (
              <div key={strategyId} className="field">
                <label>{strategyId}</label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={pct}
                  onChange={(e) => handlePerStrategyBudgetChange(strategyId, Number(e.target.value))}
                />
              </div>
            ))}
          </div>
          </section>
        ) : null}
      </section>

      <section id="settings" className="dashboard-anchor dashboard-section">
        <div className="section-header">
          <div>
            <p className="section-header__eyebrow">Settings</p>
            <h2 className="section-header__title">Session maintenance</h2>
          </div>
        </div>
        <section className="panel settings-panel">
          <div className="settings-panel__grid">
            <div className="field">
              <label className="field__label-row" htmlFor="loop-interval">
                <span>Loop frequency (seconds)</span>
                <span className="pill pill--muted">Current: {loopIntervalDraft.toFixed(1)}s</span>
              </label>
              <input
                id="loop-interval"
                type="number"
                min={1}
                max={300}
                value={loopIntervalDraft}
                onChange={(event) => setLoopIntervalDraft(Number(event.target.value))}
                disabled={health?.ui_read_only}
              />
              <p className="field__hint">Use this when you want slower or tighter evaluation cadence without restarting the session.</p>
              <button
                type="button"
                className="ghost-button"
                onClick={handleLoopIntervalUpdate}
                disabled={health?.ui_read_only}
              >
                Apply frequency
              </button>
            </div>

            <div className="field">
              <label>Runtime configuration</label>
              <p className="field__hint">
                Download the current runtime configuration, including UI overrides and session-derived state.
              </p>
              <button
                type="button"
                className="ghost-button"
                onClick={handleDownloadRuntimeConfig}
              >
                Download current config
              </button>
            </div>
          </div>
        </section>
      </section>
    </Layout>
  );
}


function App() {
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [startupLoading, setStartupLoading] = useState(true);
  const [startupError, setStartupError] = useState<string | null>(null);
  const [startupStage, setStartupStage] = useState<'checking' | 'unlocking'>('checking');

  const checkStatus = async () => {
    setStartupStage('checking');
    setStartupLoading(true);
    setStartupError(null);

    try {
      const status = await fetchSetupStatus();
      setSetupStatus(status);
    } catch (error) {
      console.error('Failed to fetch setup status', error);
      setSetupStatus(null);
      setStartupError(error instanceof Error ? error.message : 'Unable to reach backend');
    } finally {
      setStartupLoading(false);
    }
  };

  const waitForUnlock = async () => {
    setStartupStage('unlocking');
    setStartupLoading(true);
    setStartupError(null);

    const deadline = Date.now() + 5 * 60 * 1000;
    let lastStatus: SetupStatus | null = null;

    try {
      while (Date.now() < deadline) {
        const status = await fetchSetupStatus();
        lastStatus = status;
        setSetupStatus(status);

        if (status.unlocked || !status.configured || !status.secrets_exist) {
          return;
        }

        await new Promise((resolve) => setTimeout(resolve, 1500));
      }

      throw new Error(
        'Unlock succeeded, but Krakked is still initializing. First-time startup can take a few minutes. Please wait and retry if the dashboard does not appear.',
      );
    } catch (error) {
      console.error('Failed while waiting for unlock', error);
      setSetupStatus(lastStatus);
      throw error;
    } finally {
      setStartupLoading(false);
    }
  };

  useEffect(() => {
    checkStatus();
  }, []);

  if (startupLoading) {
    const loadingTitle = startupStage === 'unlocking' ? 'Initializing Krakked…' : 'Connecting…';
    const loadingSubtitle =
      startupStage === 'unlocking'
        ? 'Unlock succeeded. Krakked is loading services and market data. First-time startup can take a few minutes.'
        : 'Loading system status.';

    return (
      <div className="startup">
        <div className="startup__panel">
          <div className="startup__brand">
            <div>
              <p className="eyebrow">Krakked</p>
              <h1>{loadingTitle}</h1>
              <p className="subtitle">{loadingSubtitle}</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (startupError) {
    return (
      <div className="startup">
        <div className="startup__panel">
          <div className="startup__brand">
            <div>
              <p className="eyebrow">Krakked</p>
              <h1>Unable to connect</h1>
              <p className="subtitle">{startupError}</p>
            </div>
          </div>

          <div className="startup__actions">
            <button type="button" className="primary-button" onClick={checkStatus}>
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (setupStatus && (!setupStatus.configured || !setupStatus.secrets_exist)) {
    return <SetupWizard onComplete={waitForUnlock} />;
  }

  if (setupStatus && !setupStatus.unlocked) {
    return <PasswordScreen onUnlock={waitForUnlock} />;
  }

  return <DashboardShell onLogout={() => window.location.reload()} />;
}

export default App;
