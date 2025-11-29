import { FormEvent, useEffect, useMemo, useState } from 'react';
import { FooterHotkeys } from './components/FooterHotkeys';
import { KpiGrid, Kpi } from './components/KpiGrid';
import { Layout } from './components/Layout';
import { RiskPanel } from './components/RiskPanel';
import { LogEntry, LogPanel } from './components/LogPanel';
import { PositionRow, PositionsTable } from './components/PositionsTable';
import { Sidebar } from './components/Sidebar';
import { WalletRow, WalletTable } from './components/WalletTable';
import {
  fetchExposure,
  fetchPortfolioSummary,
  fetchPositions,
  fetchRecentExecutions,
  fetchSystemHealth,
  getRiskStatus,
  ExposureBreakdown,
  PortfolioSummary,
  PositionPayload,
  RiskStatus,
  RecentExecution,
  SystemHealth,
  setKillSwitch,
} from './services/api';
import { validateCredentials } from './services/credentials';

const DEFAULT_REGION = (import.meta.env.VITE_REGION as string | undefined) ?? 'US_CA';

const initialState = {
  apiKey: '',
  apiSecret: '',
  region: DEFAULT_REGION,
};

const AUTH_STORAGE_KEY = 'krakked.authenticated';

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
    const timestamp = formatTimestamp(execution.completed_at || execution.started_at);
    const message = `${execution.plan_id} ${execution.success ? 'succeeded' : 'failed'} (${execution.orders.length} orders)`;
    const level: LogEntry['level'] = execution.success ? 'info' : 'error';

    return {
      level,
      message,
      timestamp,
      source,
    };
  });

type ValidationErrors = Partial<typeof initialState>;

type SubmissionState = {
  status: 'idle' | 'loading' | 'success' | 'error';
  message?: string;
};

function DashboardShell({ onLogout }: { onLogout: () => void }) {
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [balances, setBalances] = useState<WalletRow[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connectionState, setConnectionState] = useState<'connected' | 'degraded'>('degraded');
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [risk, setRisk] = useState<RiskStatus | null>(null);
  const [riskBusy, setRiskBusy] = useState(false);
  const [riskFeedback, setRiskFeedback] = useState<{ tone: 'info' | 'error' | 'success'; message: string } | null>(null);

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

    const loadDashboard = async () => {
      const [summary, exposure, systemHealth, riskStatus] = await Promise.all([
        fetchPortfolioSummary(),
        fetchExposure(),
        fetchSystemHealth(),
        getRiskStatus(),
      ]);
      if (cancelled) return;

      if (summary) {
        setKpis(buildKpis(summary));
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
  }, []);

  useEffect(() => {
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
  }, []);

  useEffect(() => {
    if (health?.ui_read_only) {
      setRiskFeedback({ tone: 'info', message: 'Backend is read-only. Kill switch changes are disabled.' });
    } else {
      setRiskFeedback((current) => (current?.tone === 'info' ? null : current));
    }
  }, [health?.ui_read_only]);

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

  useEffect(() => {
    let cancelled = false;

    const loadExecutions = async () => {
      const data = await fetchRecentExecutions();
      if (cancelled) return;
      if (data) setLogs(transformLogs(data));
    };

    loadExecutions();
    const interval = setInterval(loadExecutions, ORDERS_REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <Layout
      title="Trading Overview"
      subtitle="Live data with automatic refresh."
      sidebar={<Sidebar items={sidebarItems} footer={{ label: 'Session', value: connectionState === 'connected' ? 'Connected' : 'Degraded' }} />}
      actions={
        <button type="button" className="ghost-button" onClick={onLogout}>
          Log out
        </button>
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

      <RiskPanel status={risk} readOnly={Boolean(health?.ui_read_only)} busy={riskBusy} onToggle={handleToggleKillSwitch} feedback={riskFeedback} />

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
  const [form, setForm] = useState(initialState);
  const [showSecret, setShowSecret] = useState(false);
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [submission, setSubmission] = useState<SubmissionState>({ status: 'idle' });
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    if (typeof window === 'undefined') return false;
    return localStorage.getItem(AUTH_STORAGE_KEY) === 'true';
  });

  const isDisabled = useMemo(
    () =>
      submission.status === 'loading' ||
      form.apiKey.trim().length === 0 ||
      form.apiSecret.trim().length === 0,
    [form.apiKey, form.apiSecret, submission.status],
  );

  const handleChange = (field: keyof typeof initialState) => (event: React.ChangeEvent<HTMLInputElement>) => {
    setForm((previous) => ({ ...previous, [field]: event.target.value }));
    setErrors((previous) => ({ ...previous, [field]: undefined }));
  };

  const handleLogout = () => {
    localStorage.removeItem(AUTH_STORAGE_KEY);
    setIsAuthenticated(false);
    setForm(initialState);
    setSubmission({ status: 'idle' });
    setErrors({});
    setShowSecret(false);
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const newErrors: ValidationErrors = {};

    if (!form.apiKey.trim()) newErrors.apiKey = 'API Key is required';
    if (!form.apiSecret.trim()) newErrors.apiSecret = 'API Secret is required';

    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors);
      return;
    }

    setSubmission({ status: 'loading', message: 'Validating credentials…' });

    const response = await validateCredentials(form);
    const valid = response.data?.valid ?? false;

    if (valid) {
      setSubmission({ status: 'success', message: 'Credentials validated successfully.' });
      setIsAuthenticated(true);
      localStorage.setItem(AUTH_STORAGE_KEY, 'true');
    } else {
      setSubmission({
        status: 'error',
        message: response.error || 'Unable to validate credentials. Please try again.',
      });
    }
  };

  if (isAuthenticated) {
    return <DashboardShell onLogout={handleLogout} />;
  }

  return (
    <div className="app-shell">
      <div className="background" aria-hidden="true" />
      <main className="auth" aria-labelledby="welcome-heading">
        <div className="auth__inner">
          <header className="auth__header">
            <p className="eyebrow">Authentication</p>
            <h1 id="welcome-heading">Welcome to Krakked</h1>
            <p className="subtitle">Enter your Kraken API credentials to connect.</p>
          </header>

          <form className="form" onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label htmlFor="apiKey">API Key</label>
              <input
                id="apiKey"
                name="apiKey"
                type="text"
                autoComplete="off"
                value={form.apiKey}
                onChange={handleChange('apiKey')}
                aria-invalid={Boolean(errors.apiKey)}
              />
              {errors.apiKey ? <p className="field__error">{errors.apiKey}</p> : null}
            </div>

            <div className="field">
              <div className="field__label-row">
                <label htmlFor="apiSecret">API Secret</label>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setShowSecret((value) => !value)}
                  aria-pressed={showSecret}
                  aria-controls="apiSecret"
                >
                  {showSecret ? 'Hide' : 'Show'}
                </button>
              </div>
              <input
                id="apiSecret"
                name="apiSecret"
                type={showSecret ? 'text' : 'password'}
                autoComplete="off"
                value={form.apiSecret}
                onChange={handleChange('apiSecret')}
                aria-invalid={Boolean(errors.apiSecret)}
              />
              {errors.apiSecret ? <p className="field__error">{errors.apiSecret}</p> : null}
            </div>

            <button className="primary-button" type="submit" disabled={isDisabled} aria-busy={submission.status === 'loading'}>
              {submission.status === 'loading' ? 'Connecting…' : 'Connect'}
            </button>
            <a className="secondary-link" href="https://www.kraken.com/u/security/api" target="_blank" rel="noreferrer">
              Find your API Keys
            </a>

            {submission.status !== 'idle' ? (
              <div
                className={`feedback feedback--${submission.status}`}
                role={submission.status === 'error' ? 'alert' : 'status'}
                aria-live="polite"
              >
                {submission.message}
              </div>
            ) : null}
          </form>
        </div>
      </main>
    </div>
  );
}

export default App;
