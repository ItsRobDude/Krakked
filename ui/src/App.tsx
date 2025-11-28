import { FormEvent, useMemo, useState } from 'react';
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

type DashboardProps = {
  botStatus: string;
  balances: Array<{ asset: string; amount: string; valueUSD: string }>;
  recentActivity: Array<{ label: string; detail: string; timestamp: string }>;
  onLogout: () => void;
};

function DashboardShell({ botStatus, balances, recentActivity, onLogout }: DashboardProps) {
  return (
    <div className="dashboard">
      <header className="dashboard__header">
        <div>
          <p className="eyebrow">Kraken Bot</p>
          <h1 id="dashboard-heading">Dashboard</h1>
          <p className="subtitle">Placeholder data until backend wiring is connected.</p>
        </div>
        <button type="button" className="ghost-button" onClick={onLogout}>
          Log out
        </button>
      </header>

      <div className="dashboard__grid">
        <section className="panel">
          <div className="panel__header">
            <h2>Connection</h2>
            <span className="status-pill" data-status={botStatus.toLowerCase()}>
              {botStatus}
            </span>
          </div>
          <p className="panel__description">
            This section will be populated with live bot status once the API is connected.
          </p>
          <ul className="placeholder-list">
            <li>Next step: wire Kraken credential validation to persist session.</li>
            <li>Expose websocket health and heartbeat timestamps.</li>
            <li>Show trading mode and configured strategies.</li>
          </ul>
        </section>

        <section className="panel">
          <div className="panel__header">
            <h2>Balances</h2>
            <p className="panel__hint">Sample data props for upcoming integration</p>
          </div>
          <div className="balance-grid">
            {balances.map((balance) => (
              <div className="balance-card" key={balance.asset}>
                <p className="balance-card__label">{balance.asset}</p>
                <p className="balance-card__value">{balance.amount}</p>
                <p className="balance-card__subvalue">{balance.valueUSD} USD</p>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel__header">
            <h2>Recent Activity</h2>
            <p className="panel__hint">Replace with server-driven events</p>
          </div>
          <ul className="activity-list">
            {recentActivity.map((item) => (
              <li className="activity-list__item" key={item.timestamp + item.label}>
                <div>
                  <p className="activity-list__label">{item.label}</p>
                  <p className="activity-list__detail">{item.detail}</p>
                </div>
                <time className="activity-list__time">{item.timestamp}</time>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
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

  const placeholderBalances = [
    { asset: 'ETH', amount: '1.4200', valueUSD: '4,120.50' },
    { asset: 'BTC', amount: '0.078', valueUSD: '5,140.20' },
    { asset: 'USDT', amount: '6,500', valueUSD: '6,500.00' },
  ];

  const placeholderActivity = [
    { label: 'Strategy backfill', detail: 'Loaded 24h market history for ETH/USD.', timestamp: 'Just now' },
    { label: 'Heartbeat', detail: 'Websocket ping acknowledged.', timestamp: '2m ago' },
    { label: 'Balance sync', detail: 'Account balances refreshed.', timestamp: '15m ago' },
  ];

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
    return (
      <div className="app-shell">
        <div className="background" aria-hidden="true" />
        <main className="card card--dashboard" aria-labelledby="dashboard-heading">
          <DashboardShell
            botStatus={submission.status === 'success' ? 'Connected' : 'Reconnected'}
            balances={placeholderBalances}
            recentActivity={placeholderActivity}
            onLogout={handleLogout}
          />
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <div className="background" aria-hidden="true" />
      <main className="card" aria-labelledby="welcome-heading">
        <div className="card__body">
          <header className="card__header">
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
