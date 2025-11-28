import { FormEvent, useMemo, useState } from 'react';
import { FooterHotkeys } from './components/FooterHotkeys';
import { KpiGrid } from './components/KpiGrid';
import { Layout } from './components/Layout';
import { LogPanel } from './components/LogPanel';
import { PositionsTable } from './components/PositionsTable';
import { Sidebar } from './components/Sidebar';
import { WalletTable } from './components/WalletTable';
import { validateCredentials } from './services/credentials';

const initialState = {
  apiKey: '',
  apiSecret: '',
};

const AUTH_STORAGE_KEY = 'krakked.authenticated';

type ValidationErrors = Partial<typeof initialState>;

type SubmissionState = {
  status: 'idle' | 'loading' | 'success' | 'error';
  message?: string;
};

function DashboardShell({ onLogout }: { onLogout: () => void }) {
  const sidebarItems = [
    { label: 'Overview', description: 'KPIs & positions', active: true, badge: 'Live' },
    { label: 'Signals', description: 'Strategy stream', badge: 'Soon' },
    { label: 'Backtests', description: 'Historical runs' },
    { label: 'Settings', description: 'API keys & risk' },
  ];

  const kpis = [
    { label: '24h PnL', value: '+2.94%', change: '+0.40%', hint: 'Vs prior period' },
    { label: 'Open Exposure', value: '$14,200', change: '3 positions', hint: 'Auto-hedged' },
    { label: 'Funding Used', value: '38%', change: 'Low risk', hint: 'Configurable' },
    { label: 'Latency', value: '220ms', change: 'OK', hint: 'Order routing' },
  ];

  const positions = [
    { pair: 'ETH/USD', side: 'long', size: '1.35 ETH', entry: '$3,048.00', mark: '$3,065.44', pnl: '+$23.45', status: 'Trailing' },
    { pair: 'BTC/USD', side: 'short', size: '0.08 BTC', entry: '$65,220.00', mark: '$64,880.12', pnl: '+$27.19', status: 'Monitoring' },
    { pair: 'SOL/USD', side: 'long', size: '120 SOL', entry: '$148.10', mark: '$145.92', pnl: '-$261.60', status: 'Stop nearby' },
  ];

  const balances = [
    { asset: 'ETH', total: '1.4200', available: '0.6700', valueUsd: '$4,120.50' },
    { asset: 'BTC', total: '0.0780', available: '0.0500', valueUsd: '$5,140.20' },
    { asset: 'USDT', total: '6,500', available: '6,500', valueUsd: '$6,500.00' },
    { asset: 'SOL', total: '250', available: '110', valueUsd: '$36,480.00' },
  ];

  const logs = [
    { level: 'info', message: 'Balance sync finished. 5 assets refreshed.', timestamp: 'Just now', source: 'balances' },
    { level: 'warning', message: 'Order book latency above threshold on ETH/USD.', timestamp: '2m ago', source: 'market-data' },
    { level: 'info', message: 'Strategy backfill loaded 24h of trades.', timestamp: '15m ago', source: 'strategy' },
    { level: 'error', message: 'Websocket reconnect triggered for auth feed.', timestamp: '28m ago', source: 'connectivity' },
  ];

  const hotkeys = [
    { keys: 'R', description: 'Restart the bot service' },
    { keys: 'Shift + C', description: 'Cancel all working orders' },
    { keys: 'L', description: 'Toggle live log streaming' },
    { keys: 'G', description: 'Refresh balances and positions' },
  ];

  return (
    <Layout
      title="Trading Overview"
      subtitle="Composable blocks ready for streaming data."
      sidebar={<Sidebar items={sidebarItems} footer={{ label: 'Session', value: 'Connected' }} />}
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
          <span className="status-pill" data-status="connected">
            Connected
          </span>
        </div>
        <p className="panel__description">Panels below are wired to accept streamed props.</p>
        <ul className="placeholder-list">
          <li>Swap in websocket payloads for KPIs, balances, and logs.</li>
          <li>Use the sidebar badges to reflect live system status.</li>
          <li>Footer hotkeys can be hooked into command handlers.</li>
        </ul>
      </section>

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
    if (response.success) {
      setSubmission({ status: 'success', message: response.message || 'Connected successfully.' });
      setIsAuthenticated(true);
      localStorage.setItem(AUTH_STORAGE_KEY, 'true');
    } else {
      setSubmission({
        status: 'error',
        message: response.message || 'Unable to validate credentials. Please try again.',
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
