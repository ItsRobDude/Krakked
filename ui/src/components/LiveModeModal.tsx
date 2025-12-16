import { useState } from 'react';

type LiveModeModalProps = {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (password: string) => Promise<void>;
};

export function LiveModeModal({ isOpen, onClose, onConfirm }: LiveModeModalProps) {
  const [password, setPassword] = useState('');
  const [confirmRisk, setConfirmRisk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!confirmRisk) {
      setError('You must acknowledge the risk checkbox.');
      return;
    }

    if (!password) {
      setError('Master password is required.');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      await onConfirm(password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true">
      <div className="modal-content panel">
        <div className="panel__header">
          <h2>⚠ Enable Live Trading</h2>
        </div>
        <p className="panel__description">
          You are about to switch to <strong>Live Money</strong> execution. The bot will have authority to place real
          orders on your Kraken account.
        </p>

        <form onSubmit={handleSubmit} className="form">
          <div className="field">
            <label htmlFor="master-password">Master Password</label>
            <input
              id="master-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter master password..."
              disabled={busy}
              autoFocus
            />
          </div>

          <div className="field field--checkbox">
            <label style={{ color: '#fecaca' }}>
              <input
                type="checkbox"
                checked={confirmRisk}
                onChange={(e) => setConfirmRisk(e.target.checked)}
                disabled={busy}
              />
              I understand the risks. ENABLE LIVE TRADING.
            </label>
          </div>

          {error && <div className="feedback feedback--error">{error}</div>}

          <div className="modal-actions">
            <button type="button" className="ghost-button" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="primary-button" disabled={busy || !confirmRisk}>
              {busy ? 'Verifying…' : 'Activate Live Mode'}
            </button>
          </div>
        </form>
      </div>

      <style>{`
        .modal-overlay {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.7);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
          padding: 1.5rem;
        }
        .modal-content {
          width: 100%;
          max-width: 520px;
        }
        .modal-actions {
          display: flex;
          justify-content: flex-end;
          gap: 1rem;
          margin-top: 1.5rem;
        }
      `}</style>
    </div>
  );
}
