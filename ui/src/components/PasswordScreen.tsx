import { useState } from 'react';
import { performUnlock } from '../services/api';

export function PasswordScreen({ onUnlock }: { onUnlock: () => Promise<void> }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);

    try {
      await performUnlock(password);
      await onUnlock();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid password');
      setBusy(false);
    }
  };

  return (
    <div className="startup">
      <div className="startup__panel">
        <div className="startup__brand">
          <div>
            <p className="eyebrow">Welcome Back</p>
            <h1>Unlock Krakked</h1>
            <p className="subtitle">
              Enter the same Master Password you created during setup to decrypt your
              credentials.
            </p>
          </div>
        </div>

        <form className="form" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="unlock-password">Master Password</label>
            <input
              id="unlock-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Master Password"
              autoFocus
              disabled={busy}
            />
            <p className="field__hint">
              This is the password you created to encrypt your API keys during setup.
            </p>
          </div>

          <button type="submit" className="primary-button" disabled={busy} aria-busy={busy}>
            {busy ? 'Unlock accepted. Initializing Krakked…' : 'Unlock'}
          </button>

          {error && <div className="feedback feedback--error">{error}</div>}
        </form>
      </div>
    </div>
  );
}
