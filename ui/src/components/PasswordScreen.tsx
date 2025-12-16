import { useState } from 'react';
import { performUnlock } from '../services/api';

export function PasswordScreen({ onUnlock }: { onUnlock: () => void }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);

    try {
      await performUnlock(password);
      onUnlock();
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
            <p className="subtitle">Enter your Master Password to decrypt credentials.</p>
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
          </div>

          <button type="submit" className="primary-button" disabled={busy} aria-busy={busy}>
            {busy ? 'Unlocking…' : 'Unlock'}
          </button>

          {error && <div className="feedback feedback--error">{error}</div>}
        </form>
      </div>
    </div>
  );
}
