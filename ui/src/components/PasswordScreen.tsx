import { useState } from 'react';
import { performUnlock } from '../services/api';

export function PasswordScreen({ onUnlock }: { onUnlock: () => void | Promise<void> }) {
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
      // If performUnlock fails, it throws.
      // If onUnlock fails (unlikely, usually just returns void/null), we catch it here too.
      console.error(err);
      setError("Invalid password or unlock failed");
    } finally {
      // Always clear busy state.
      // If onUnlock succeeded and parent unmounts us, this setBusy call is safe/ignored.
      // If onUnlock succeeded but parent didn't unmount us (e.g. status still locked),
      // we need this to re-enable the button so user can try again.
      setBusy(false);
    }
  };

  return (
    <div className="startup">
      <div className="startup__panel">
        <div className="startup__brand">
          <h1>Welcome Back</h1>
          <p>Enter Master Password to unlock.</p>
        </div>
        <form className="form" onSubmit={handleSubmit}>
          <div className="field">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Master Password"
              autoFocus
              disabled={busy}
            />
          </div>
          <button type="submit" className="primary-button" disabled={busy}>
            {busy ? 'Unlocking...' : 'Unlock'}
          </button>
          {error && <div className="feedback feedback--error">{error}</div>}
        </form>
      </div>
    </div>
  );
}
