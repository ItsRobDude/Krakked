import { useMemo, useState } from 'react';
import type { ProfileSummary, SessionConfigRequest } from '../services/api';

export type StartupScreenProps = {
  profiles: ProfileSummary[];
  onStart: (params: SessionConfigRequest) => Promise<void> | void;
};

const DEFAULT_LOOP_INTERVAL = 15;

export function StartupScreen({ profiles, onStart }: StartupScreenProps) {
  const [mode, setMode] = useState<SessionConfigRequest['mode']>('paper');
  const [loopInterval, setLoopInterval] = useState<number>(DEFAULT_LOOP_INTERVAL);
  const [mlEnabled, setMlEnabled] = useState(true);
  const [selectedProfile, setSelectedProfile] = useState<string>(() => profiles[0]?.name ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProfileMeta = useMemo(
    () => profiles.find((profile) => profile.name === selectedProfile),
    [profiles, selectedProfile],
  );

  const handleStart = async () => {
    if (!selectedProfile) {
      setError('Select a profile to start.');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      await onStart({
        profile_name: selectedProfile,
        mode,
        loop_interval_sec: loopInterval,
        ml_enabled: mlEnabled,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to start session');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="startup">
      <div className="startup__panel">
        <div className="startup__brand">
          <div className="startup__mark" />
          <div>
            <p className="eyebrow">Krakked v3</p>
            <h1>Start a session</h1>
            <p className="subtitle">Pick a profile, mode, and loop cadence before trading begins.</p>
          </div>
        </div>

        <div className="startup__grid">
          <div className="field">
            <label>Profile</label>
            <select
              value={selectedProfile}
              onChange={(event) => setSelectedProfile(event.target.value)}
            >
              {profiles.map((profile) => (
                <option key={profile.name} value={profile.name}>
                  {profile.name}
                </option>
              ))}
            </select>
            {selectedProfileMeta?.description ? (
              <p className="field__hint">{selectedProfileMeta.description}</p>
            ) : null}
          </div>

          <div className="field">
            <label>Mode</label>
            <select
              value={mode}
              onChange={(event) => setMode(event.target.value as SessionConfigRequest['mode'])}
            >
              <option value="paper">Paper / Test</option>
              <option value="live">Live</option>
              <option value="test">Sandbox</option>
            </select>
            <p className="field__hint">Trading remains paused until you hit Start.</p>
          </div>

          <div className="field">
            <label>Loop frequency (seconds)</label>
            <input
              type="number"
              min={1}
              max={300}
              step={1}
              value={loopInterval}
              onChange={(event) => setLoopInterval(Number(event.target.value))}
            />
            <p className="field__hint">How often the engine should evaluate and place orders.</p>
          </div>

          <div className="field field--checkbox">
            <label>
              <input
                type="checkbox"
                checked={mlEnabled}
                onChange={(event) => setMlEnabled(event.target.checked)}
              />
              Enable machine learning strategies
            </label>
          </div>
        </div>

        {error ? <div className="alert alert--error">{error}</div> : null}

        <div className="startup__actions">
          <button type="button" className="primary-button" onClick={handleStart} disabled={busy}>
            {busy ? 'Starting…' : 'Start bot'}
          </button>
        </div>
      </div>
    </div>
  );
}
