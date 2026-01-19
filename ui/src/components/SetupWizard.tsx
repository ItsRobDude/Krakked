import { useState } from 'react';
import { performSetupConfig, performSetupCredentials, performUnlock } from '../services/api';

type SetupWizardProps = {
  onComplete: () => void;
};

export function SetupWizard({ onComplete }: SetupWizardProps) {
  const [region, setRegion] = useState('US');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFinish = async (e: React.FormEvent) => {
    e.preventDefault();

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      await performSetupConfig(region);
      await performSetupCredentials({ apiKey, apiSecret, password, region });
      await performUnlock(password);

      // Give the backend time to re-bootstrap after unlock.
      await new Promise((resolve) => setTimeout(resolve, 1500));
      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed');
      setBusy(false);
    }
  };

  return (
    <div className="startup">
      <div className="startup__panel">
        <div className="startup__brand">
          <div className="startup__mark" />
          <div>
            <p className="eyebrow">First-Time Setup</p>
            <h1>Welcome to Krakked</h1>
            <p className="subtitle">Let&apos;s configure your secure trading environment.</p>
          </div>
        </div>

        <form className="startup__grid" onSubmit={handleFinish}>
          {error && <div className="feedback feedback--error">{error}</div>}

          <div className="field">
            <label>Kraken Region</label>
            <select value={region} onChange={(e) => setRegion(e.target.value)} disabled={busy}>
              <option value="US">United States</option>
              <option value="EU">Europe</option>
              <option value="GB">United Kingdom</option>
              <option value="JP">Japan</option>
              <option value="CA">Canada</option>
            </select>
            <p className="field__hint">Used for regulatory compliance settings.</p>
          </div>

          <div className="field">
            <label>API Key</label>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Kraken API Key"
              required
              disabled={busy}
            />
          </div>

          <div className="field">
            <label>API Secret</label>
            <input
              type="password"
              value={apiSecret}
              onChange={(e) => setApiSecret(e.target.value)}
              placeholder="Kraken API Secret"
              required
              disabled={busy}
            />
          </div>

          <div className="field">
            <label>Create Master Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Encrypts your API keys"
              required
              disabled={busy}
            />
          </div>

          <div className="field">
            <label>Confirm Password</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              disabled={busy}
            />
          </div>

          <div className="startup__actions">
            <button type="submit" className="primary-button" disabled={busy} aria-busy={busy}>
              {busy ? 'Initializing System…' : 'Complete Setup'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
