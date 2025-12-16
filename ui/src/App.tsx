import { useEffect, useMemo, useState } from 'react';
import { FooterHotkeys } from './components/FooterHotkeys';
import { KpiGrid, Kpi } from './components/KpiGrid';
import { Layout } from './components/Layout';
import { RiskPanel } from './components/RiskPanel';
import { LogEntry, LogPanel } from './components/LogPanel';
import { PositionRow, PositionsTable } from './components/PositionsTable';
import { Sidebar } from './components/Sidebar';
import { StrategiesPanel } from './components/StrategiesPanel';
import { WalletRow, WalletTable } from './components/WalletTable';
import { StartupScreen } from './components/StartupScreen';
import { SetupWizard } from './components/SetupWizard';
import { PasswordScreen } from './components/PasswordScreen';
import { LiveModeModal } from './components/LiveModeModal';
import {
  fetchExposure,
  fetchPortfolioSummary,
  fetchPositions,
  fetchRecentExecutions,
  fetchRiskDecisions,
  fetchSystemHealth,
  fetchSessionState,
  fetchProfiles,
  fetchStrategies,
  fetchStrategyPerformance,
  fetchRiskConfig,
  applyRiskPreset,
  getRiskStatus,
  fetchSetupStatus,
  ExposureBreakdown,
  ProfileSummary,
  PortfolioSummary,
  PositionPayload,
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
  SessionMode,
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
} from './services/api';
import { RISK_PRESET_META, formatPresetSummary } from './constants/riskPresets';

const DASHBOARD_REFRESH_MS = Number(import.meta.env.VITE_REFRESH_DASHBOARD_MS ?? 5000) || 5000;
const ORDERS_REFRESH_MS = Number(import.meta.env.VITE_REFRESH_ORDERS_MS ?? 5000) || 5000;

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

const isRiskProfile = (value: unknown): value is StrategyRiskProfile =>
  value === 'conservative' || value === 'balanced' || value === 'aggressive';

const RISK_PRESET_OPTIONS: RiskPresetName[] = ['conservative', 'balanced', 'aggressive', 'degen'];
const ML_STRATEGY_IDS = ['ai_predictor', 'ai_predictor_alt', 'ai_regression'] as const;
type MlStrategyId = (typeof ML_STRATEGY_IDS)[number];

const buildKpis = (summary: PortfolioSummary) => [
  {
    label: 'Equity',
    value: formatCurrency(summary.equity_usd),
    hint: summary.last_snapshot_ts ? `Last snapshot ${formatTimestamp(summary.last_snapshot_ts)}` : 'Snapshot pending',
  },
  { label: 'Cash', value: formatCurrency(summary.cash_usd), hint: 'Usable collateral' },
  { label: 'Realized PnL', value: formatCurrency(summary.realized_pnl_usd), hint: 'Total' },
  { label: 'Unrealized PnL', value: formatCurrency(summary.unrealized_pnl_usd), hint: summary.drift_flag ? 'Rebalance suggested' : 'In bounds' },
];

const transformPositions = (payload: PositionPayload[]) =>
  payload.map((position) => {
    const side: PositionRow['side'] = position.base_size < 0 ? 'short' : 'long';
    const size = `${Math.abs(position.base_size).toFixed(4)} ${position.base_asset}`;
    const entry = position.avg_entry_price ? formatCurrency(position.avg_entry_price) : '—';
    const mark = position.current_price ? formatCurrency(position.current_price) : '—';
    const pnlValue = position.unrealized_pnl_usd ?? 0;
    const pnl = pnlValue === 0 ? '$0.00' : formatCurrency(pnlValue);
    const status = position.strategy_tag || 'Tracking';

    return { pair: position.pair, side, size, entry, mark, pnl, status };
  });

const transformBalances = (exposure: ExposureBreakdown) =>
  exposure.by_asset.map((asset) => ({
    asset: asset.asset,
    total: formatPercent(asset.pct_of_equity || 0),
    available: '—',
    valueUsd: formatCurrency(asset.value_usd || 0),
  }));

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
  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [balances, setBalances] = useState<WalletRow[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connectionState, setConnectionState] = useState<'connected' | 'degraded'>('degraded');
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
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
  const [systemMessage, setSystemMessage] = useState<{ tone: 'info' | 'error' | 'success'; message: string } | null>(null);
  const [modeBusy, setModeBusy] = useState(false);
  const [session, setSession] = useState<SessionStateResponse | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [loopIntervalDraft, setLoopIntervalDraft] = useState<number>(15);

  const [showLiveModal, setShowLiveModal] = useState(false);
  const [pendingLiveStart, setPendingLiveStart] = useState<SessionConfigRequest | null>(null);

  const mlStrategies = useMemo(
    () => strategies.filter((strategy) => ML_STRATEGY_IDS.includes(strategy.strategy_id as MlStrategyId)),
    [strategies],
  );

  const mlEnabled = useMemo(
    () => mlStrategies.length > 0 && mlStrategies.every((strategy) => strategy.enabled),
    [mlStrategies],
  );

  const sidebarItems = [
    { label: 'Overview', description: 'KPIs & positions', active: true, badge: 'Live' },
    { label: 'Signals', description: 'Strategy stream', badge: 'Soon' },
    { label: 'Backtests', description: 'Historical runs' },
    { label: 'Settings', description: 'API keys & risk' },
  ];

  const hotkeys = [
    { keys: 'R', description: 'Restart the bot service' },
    { keys: 'Shift + C', description: 'Cancel all working orders' },
    { keys: 'L', description: 'Toggle live log streaming' },
    { keys: 'G', description: 'Refresh balances and positions' },
  ];

  useEffect(() => {
    let cancelled = false;

    const loadSession = async () => {
      const [sessionState, profileSummaries, systemHealth] = await Promise.all([
        fetchSessionState(),
        fetchProfiles(),
        fetchSystemHealth(),
      ]);

      if (cancelled) return;

      if (sessionState) {
        setSession(sessionState);
        setLoopIntervalDraft(sessionState.loop_interval_sec);
      }

      if (systemHealth) {
        setHealth(systemHealth);
        const healthy = systemHealth.market_data_ok && systemHealth.execution_ok;
        setConnectionState(healthy ? 'connected' : 'degraded');
      }

      setProfiles(profileSummaries);
      setSessionLoading(false);
    };

    loadSession();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (session?.loop_interval_sec) {
      setLoopIntervalDraft(session.loop_interval_sec);
    }
  }, [session?.loop_interval_sec]);

  useEffect(() => {
    if (!session?.active) return;

    let cancelled = false;

    const loadDashboard = async () => {
      const [portfolioSummary, exposure, systemHealth, riskStatus] = await Promise.all([
        fetchPortfolioSummary(),
        fetchExposure(),
        fetchSystemHealth(),
        getRiskStatus(),
      ]);
      if (cancelled) return;

      if (portfolioSummary) {
        setSummary(portfolioSummary);
        setKpis(buildKpis(portfolioSummary));
      }

      if (systemHealth) {
        setHealth(systemHealth);
        const healthy = systemHealth.market_data_ok && systemHealth.execution_ok;
        setConnectionState(healthy ? 'connected' : 'degraded');
      }

      if (riskStatus) {
        setRisk(riskStatus);
      }

      if (exposure) {
        setBalances(transformBalances(exposure));
      }
    };

    loadDashboard();
    const interval = setInterval(loadDashboard, DASHBOARD_REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [session?.active]);

  useEffect(() => {
    if (!session || strategies.length === 0) return;
    if (mlStrategies.length === 0) return;

    handleMlToggle(session.ml_enabled);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.ml_enabled, strategies.length, mlStrategies.length]);

  useEffect(() => {
    if (!health || !summary) return;

    const baseKpis = buildKpis(summary);
    const extra: Kpi[] = [
      {
        label: 'Market data',
        value: health.market_data_ok ? 'OK' : 'Degraded',
        hint: health.market_data_reason ?? '',
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
    if (!session?.active) return;

    let cancelled = false;

    const loadRiskConfig = async () => {
      const config = await fetchRiskConfig();
      if (cancelled) return;
      if (config) setRiskConfig(config);
    };

    loadRiskConfig();

    return () => {
      cancelled = true;
    };
  }, [session?.active]);

  useEffect(() => {
    if (!session?.active) return;

    let cancelled = false;

    const loadPositions = async () => {
      const data = await fetchPositions();
      if (cancelled) return;
      if (data) setPositions(transformPositions(data));
    };

    loadPositions();
    const interval = setInterval(loadPositions, DASHBOARD_REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [session?.active]);

  useEffect(() => {
    if (!session?.active) return;

    let cancelled = false;

    const loadStrategies = async () => {
      const [data, perf] = await Promise.all([
        fetchStrategies(),
        fetchStrategyPerformance(),
      ]);
      if (cancelled) return;

      if (data) {
        setStrategies(data);
        setStrategyRisk((previous) => {
          const next = { ...previous };
          data.forEach((strategy) => {
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
          data.forEach((strategy) => {
            const learning = strategy.params?.continuous_learning;
            if (typeof learning === 'boolean') {
              next[strategy.strategy_id] = learning;
            } else if (next[strategy.strategy_id] === undefined) {
              next[strategy.strategy_id] = true;
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
    };

    loadStrategies();
    const interval = setInterval(loadStrategies, DASHBOARD_REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [session?.active]);

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
      const latest = await fetchSystemHealth();
      if (latest && resolveExecutionMode(latest) === target) {
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
      fetchSystemHealth(),
      getRiskStatus(),
    ]);

    if (systemHealth) {
      setHealth(systemHealth);
      const healthy = systemHealth.market_data_ok && systemHealth.execution_ok;
      setConnectionState(healthy ? 'connected' : 'degraded');
    }

    if (riskStatus) {
      setRisk(riskStatus);
    }
  };

  const performModeSwitch = async (mode: ExecutionMode, password?: string) => {
    setModeBusy(true);
    try {
      const confirmation = mode === 'live' ? 'ENABLE LIVE TRADING' : undefined;
      await setExecutionMode(mode, password, confirmation);

      const latest = await waitForExecutionMode(mode);
      if (latest) {
        setHealth(latest);
        const healthy = latest.market_data_ok && latest.execution_ok;
        setConnectionState(healthy ? 'connected' : 'degraded');
        return;
      }

      const fallback = await fetchSystemHealth();
      if (fallback) {
        setHealth(fallback);
        const healthy = fallback.market_data_ok && fallback.execution_ok;
        setConnectionState(healthy ? 'connected' : 'degraded');
      }
    } finally {
      setModeBusy(false);
    }
  };

  const handleStartSession = async (config: SessionConfigRequest) => {
    if (health?.ui_read_only) {
      throw new Error('Backend is in read-only mode.');
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

    const next = await startSession(config);
    if (!next) {
      throw new Error('Unable to start session.');
    }

    await applyStartedSession(next);
  };

  const handleConfirmLiveStart = async (password: string) => {
    if (!pendingLiveStart) {
      setShowLiveModal(false);
      return;
    }

    await performModeSwitch('live', password);

    const next = await startSession(pendingLiveStart);
    if (!next) {
      throw new Error('Unable to start session.');
    }

    await applyStartedSession(next);
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

    const created = await createProfile(name);
    setSystemMessage({ tone: 'info', message: `Created profile "${created.name}". Reloading…` });

    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const refreshed = await fetchProfiles();
      if (refreshed.some((profile) => profile.name === created.name)) {
        setProfiles(refreshed);
        setSystemMessage({ tone: 'success', message: `Profile "${created.name}" created.` });
        return created.name;
      }
      await sleep(500);
    }

    const fallback = await fetchProfiles();
    setProfiles(fallback);
    setSystemMessage({ tone: 'success', message: `Profile "${created.name}" created.` });
    return created.name;
  };

  const handleSaveConfig = async () => {
    if (health?.ui_read_only) {
      throw new Error('Backend is in read-only mode.');
    }

    const snapshot = await fetchSystemConfig();

    const sections = ['region', 'universe', 'market_data', 'portfolio', 'execution', 'risk', 'strategies'] as const;
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
    setSystemMessage({ tone: 'success', message: 'Configuration saved. Reloading…' });
    await sleep(1500);

    const [systemHealth, profileSummaries] = await Promise.all([
      fetchSystemHealth(),
      fetchProfiles(),
    ]);

    if (systemHealth) {
      setHealth(systemHealth);
      const healthy = systemHealth.market_data_ok && systemHealth.execution_ok;
      setConnectionState(healthy ? 'connected' : 'degraded');
    }
    setProfiles(profileSummaries);
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

    const updated = await startSession({
      profile_name: session.profile_name ?? 'default',
      mode: session.mode as SessionMode,
      loop_interval_sec: loopIntervalDraft,
      ml_enabled: session.ml_enabled,
    });

    if (updated) {
      setSession(updated);
      setLoopIntervalDraft(updated.loop_interval_sec);
    }
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

  const handleStrategyToggle = async (strategyId: string, enabled: boolean) => {
    if (health?.ui_read_only) {
      setStrategyFeedback('Backend is read-only. Strategy controls are disabled.');
      return;
    }

    setStrategyFeedback(null);
    setStrategyBusyState(strategyId, true);

    const previousStrategies = strategies.map((strategy) => ({ ...strategy }));
    setStrategies((current) =>
      current.map((strategy) => (strategy.strategy_id === strategyId ? { ...strategy, enabled } : strategy)),
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

  const handleMlToggle = async (enabled: boolean) => {
    if (health?.ui_read_only) {
      setStrategyFeedback('Backend is read-only. Strategy controls are disabled.');
      return;
    }

    const mlIds = strategies
      .filter((strategy) => ML_STRATEGY_IDS.includes(strategy.strategy_id as MlStrategyId))
      .map((strategy) => strategy.strategy_id);

    if (mlIds.length === 0) {
      setStrategyFeedback('No ML strategies are configured.');
      return;
    }

    setStrategyFeedback(null);

    const previousStrategies = strategies.map((strategy) => ({ ...strategy }));
    mlIds.forEach((id) => setStrategyBusyState(id, true));

    setStrategies((current) =>
      current.map((strategy) =>
        mlIds.includes(strategy.strategy_id) ? { ...strategy, enabled } : strategy,
      ),
    );

    try {
      await Promise.all(mlIds.map((id) => setStrategyEnabled(id, enabled)));
      setStrategyFeedback(`Machine learning strategies ${enabled ? 'enabled' : 'disabled'}.`);
    } catch (error) {
      setStrategies(previousStrategies);
      setStrategyFeedback('Unable to update ML strategies. Restored previous state.');
    } finally {
      mlIds.forEach((id) => setStrategyBusyState(id, false));
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

  useEffect(() => {
    if (!session?.active) return;

    let cancelled = false;

    const loadExecutions = async () => {
      const [executions, decisions] = await Promise.all([
        fetchRecentExecutions(),
        fetchRiskDecisions(50),
      ]);
      if (cancelled) return;

      const executionLogs = executions ? transformLogs(executions) : [];
      const decisionLogs = decisions ? transformRiskDecisions(decisions) : [];
      const merged = [...executionLogs, ...decisionLogs].sort(
        (a, b) => (b.sortKey ?? 0) - (a.sortKey ?? 0),
      );

      setLogs(merged);
    };

    loadExecutions();
    const interval = setInterval(loadExecutions, ORDERS_REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [session?.active]);

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
          modeBusy={modeBusy}
          systemMessage={systemMessage}
          onCreateProfile={handleCreateProfile}
          onSaveConfig={handleSaveConfig}
          onStart={handleStartSession}
        />
        <LiveModeModal isOpen={showLiveModal} onClose={handleCloseLiveModal} onConfirm={handleConfirmLiveStart} />
      </>
    );
  }

  return (
    <Layout
      title="Trading Overview"
      subtitle={
        <div className="layout__status-row">
          <span className="pill pill--muted">
            Mode:{' '}
            {session?.mode === 'live' ? 'Live' : 'Paper'}
          </span>

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
              ? 'Bot stopped'
              : risk?.kill_switch_active === false
                ? 'Bot running'
                : 'Bot status pending'}
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
        </div>
      }
      sidebar={
        <Sidebar
          items={sidebarItems}
          footer={{ label: 'Session', value: connectionState === 'connected' ? 'Connected' : 'Degraded' }}
        />
      }
      actions={
        <div className="layout__action-buttons">
          <button
            type="button"
            className="primary-button"
            disabled={riskBusy || !risk || health?.ui_read_only}
            aria-busy={riskBusy}
            onClick={handleToggleKillSwitch}
          >
            {riskBusy
              ? 'Updating…'
              : risk?.kill_switch_active
                ? 'Start bot'
                : 'Stop bot'}
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={handleFlattenAll}
            disabled={health?.ui_read_only}
          >
            Flatten all positions
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={handleStopSession}
            disabled={health?.ui_read_only}
          >
            Stop session
          </button>
          <button type="button" className="ghost-button" onClick={onLogout}>
            Log out
          </button>
        </div>
      }
      footer={<FooterHotkeys hotkeys={hotkeys} />}
    >
      <section className="panel">
        <div className="panel__header">
          <h2>Connection</h2>
          <span className="status-pill" data-status={connectionState}>
            {connectionState === 'connected' ? 'Connected' : 'Degraded'}
          </span>
        </div>
        <p className="panel__description">
          Data refreshes automatically every {Math.round(DASHBOARD_REFRESH_MS / 1000)}s. Controls respect read-only mode and execution mode reported by the backend.
        </p>
        <div className="field" style={{ maxWidth: '280px' }}>
          <label className="field__label-row">
            <span>Execution mode</span>
            <span
              className={
                resolveExecutionMode(health) === 'live' ? 'pill pill--danger' : 'pill pill--muted'
              }
            >
              {resolveExecutionMode(health) === 'live' ? 'Live' : 'Paper'}
            </span>
          </label>
          <p className="field__hint">Stop the session to change execution mode from the Startup screen.</p>
        </div>
        <div className="field" style={{ maxWidth: '280px' }}>
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
          <button
            type="button"
            className="ghost-button"
            onClick={handleLoopIntervalUpdate}
            disabled={health?.ui_read_only}
          >
            Apply frequency
          </button>
        </div>
        {systemMessage ? <div className={`feedback feedback--${systemMessage.tone}`}>{systemMessage.message}</div> : null}
        <ul className="placeholder-list">
          <li>KPIs and balances poll the portfolio endpoints.</li>
          <li>Recent executions stream into the log panel.</li>
          <li>Sidebar session badge reflects the latest fetch outcome.</li>
          {health ? (
            <li>
              Mode: <strong>{health.current_mode}</strong> · {health.ui_read_only ? 'Read-only' : 'Mutable'}
            </li>
          ) : null}
        </ul>
      </section>

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

      <section className="panel">
        <div className="panel__header">
          <h2>Settings</h2>
        </div>
        <p className="panel__description">
          Download the current runtime configuration as JSON (including UI overrides).
        </p>
        <button
          type="button"
          className="ghost-button"
          onClick={handleDownloadRuntimeConfig}
        >
          Download current config
        </button>
      </section>

      <StrategiesPanel
        strategies={strategies}
        performance={strategyPerformance}
        riskSelections={strategyRisk}
        learningSelections={strategyLearning}
        busy={strategyBusy}
        readOnly={Boolean(health?.ui_read_only)}
        feedback={strategyFeedback}
        onToggle={handleStrategyToggle}
        onRiskProfileChange={handleRiskProfileChange}
        onLearningToggle={handleLearningToggle}
        mlEnabled={mlEnabled}
        onMlToggle={handleMlToggle}
      />

      <KpiGrid items={kpis} />

      <div className="dashboard__columns">
        <div className="dashboard__column dashboard__column--wide">
          <PositionsTable positions={positions} />
          <LogPanel entries={logs} />
        </div>
        <div className="dashboard__column">
          <WalletTable balances={balances} />
        </div>
      </div>
    </Layout>
  );
}


function App() {
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [startupLoading, setStartupLoading] = useState(true);
  const [startupError, setStartupError] = useState<string | null>(null);

  const checkStatus = async () => {
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

  useEffect(() => {
    checkStatus();
  }, []);

  if (startupLoading) {
    return (
      <div className="startup">
        <div className="startup__panel">
          <div className="startup__brand">
            <div>
              <p className="eyebrow">Krakked UI</p>
              <h1>Connecting…</h1>
              <p className="subtitle">Loading system status.</p>
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
              <p className="eyebrow">Krakked UI</p>
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
    return <SetupWizard onComplete={checkStatus} />;
  }

  if (setupStatus && !setupStatus.unlocked) {
    return <PasswordScreen onUnlock={checkStatus} />;
  }

  return <DashboardShell onLogout={() => window.location.reload()} />;
}

export default App;
