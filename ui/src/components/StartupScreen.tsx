import { useMemo, useState } from 'react';
import type { ExecutionMode, ProfileSummary, SessionConfigRequest } from '../services/api';

export type StartupScreenProps = {
  profiles: ProfileSummary[];
  activeProfileName?: string | null;
  readOnly?: boolean;
  systemMode?: ExecutionMode | null;
  modeBusy?: boolean;
  systemMessage?: { tone: 'info' | 'success' | 'error'; message: string } | null;
  startupMlEnabled?: boolean;
  onCreateProfile: (name: string) => Promise<void>;
  onProfileChange: (name: string) => Promise<void> | void;
  onSaveConfig: () => Promise<void>;
  onMlToggle: (enabled: boolean) => Promise<void> | void;
  onStart: (params: SessionConfigRequest) => Promise<void> | void;
};

const DEFAULT_LOOP_INTERVAL = 15;

export function StartupScreen({
  profiles,
  activeProfileName,
  readOnly,
  systemMode,
  modeBusy,
  systemMessage,
  startupMlEnabled = true,
  onCreateProfile,
  onProfileChange,
  onSaveConfig,
  onMlToggle,
  onStart,
}: StartupScreenProps) {
  const [mode, setMode] = useState<SessionConfigRequest['mode']>('paper');
  const [loopInterval, setLoopInterval] = useState<number>(DEFAULT_LOOP_INTERVAL);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync internal selection with active profile prop
  // We prioritize activeProfileName (the source of truth) over local assumption
  const selectedProfile = activeProfileName || profiles[0]?.name || '';

  const selectedProfileMeta = useMemo(
    () => profiles.find((profile) => profile.name === selectedProfile),
    [profiles, selectedProfile],
  );

  const handleProfileSelect = async (name: string) => {
    if (readOnly) {
      setError('Backend is in read-only mode.');
      return;
    }
    // Don't re-trigger if same
    if (name === selectedProfile) return;

    setBusy(true);
    setError(null);
    try {
      await onProfileChange(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to switch profile');
    } finally {
      setBusy(false);
    }
  };

  const handleCreateProfile = async () => {
    if (readOnly) {
      setError('Backend is in read-only mode.');
      return;
    }

    const name = window.prompt('New profile name');
    if (!name) return;

    const trimmed = name.trim();
    if (!trimmed) return;

    setBusy(true);
    setError(null);

    try {
      await onCreateProfile(trimmed);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create profile');
    } finally {
      setBusy(false);
    }
  };

  const handleSaveConfig = async () => {
    if (readOnly) {
      setError('Backend is in read-only mode.');
      return;
    }

    if (!activeProfileName || selectedProfile !== activeProfileName) {
      setError('Select the active profile before saving configuration.');
      return;
    }

    setBusy(true);
    setError(null);

    try {
      await onSaveConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save configuration');
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async () => {
    if (readOnly) {
      setError('Backend is in read-only mode.');
      return;
    }

    if (modeBusy) {
      setError('Backend is reloading. Please try again in a moment.');
      return;
    }

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
        // ml_enabled is no longer part of session request
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to start session');
    } finally {
      setBusy(false);
    }
  };

  const handleMlToggle = async (enabled: boolean) => {
    if (readOnly) return;

    if (!activeProfileName || selectedProfile !== activeProfileName) {
      setError('Active profile required to toggle ML settings.');
      return;
    }

    setError(null);
    try {
      await onMlToggle(enabled);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update ML settings');
    }
  };

  return (
    <div className="startup">
      <div className="startup__panel">
        <div className="startup__brand">
          <div className="startup__mark" />
          <div>
            <p className="eyebrow">Krakked</p>
            <h1>Start a session</h1>
            <p className="subtitle">Pick a profile, mode, and loop cadence before trading begins.</p>
          </div>
        </div>

        {systemMessage ? (
          <div className={`feedback feedback--${systemMessage.tone}`}>{systemMessage.message}</div>
        ) : null}

        <div className="startup__grid">
          <div className="field">
            <div className="field__label-row">
              <label htmlFor="startup-profile">Profile</label>
              <button
                type="button"
                className="ghost-button"
                onClick={handleCreateProfile}
                disabled={busy || modeBusy || readOnly}
              >
                New profile
              </button>
            </div>
            <select
              id="startup-profile"
              value={selectedProfile}
              onChange={(event) => handleProfileSelect(event.target.value)}
              disabled={busy || modeBusy || readOnly}
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
            <div className="field__label-row">
              <label htmlFor="startup-mode">Mode</label>
              {systemMode ? (
                <span className={systemMode === 'live' ? 'pill pill--danger' : 'pill pill--muted'}>
                  System: {systemMode === 'live' ? 'Live' : 'Paper'}
                </span>
              ) : null}
              {modeBusy ? <span className="pill pill--info">Reloading…</span> : null}
            </div>
            <select
              id="startup-mode"
              value={mode}
              onChange={(event) => setMode(event.target.value as SessionConfigRequest['mode'])}
              disabled={busy || Boolean(modeBusy) || Boolean(readOnly)}
            >
              <option value="paper">Paper</option>
              <option value="live">Live</option>
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
        </div>

        <section className="startup__section" aria-label="Strategy setup">
          <div className="startup__section-header">
            <div>
              <h2>Strategy setup</h2>
              <p className="panel__hint">Profile-level strategy switches belong here before you start a session.</p>
            </div>
          </div>

          <div className="field field--checkbox">
            <label>
              <input
                type="checkbox"
                checked={startupMlEnabled}
                disabled={readOnly || modeBusy || !activeProfileName}
                onChange={(event) => handleMlToggle(event.target.checked)}
              />
              Enable ML strategies for this profile
            </label>
            <p className="field__hint">
              Profile-level strategy setting. It controls whether ML strategies participate for the active profile.
            </p>
            <p className="field__hint">
              Changes apply when the stopped session reloads or starts. Turning this off removes ML strategies from the
              enabled strategy set at config load time.
            </p>
            <p className="field__hint">Per-strategy Learning only controls continuous training for enabled ML strategies.</p>
            {!activeProfileName && (
              <p className="field__hint field__hint--warn">Select and activate a profile to change ML settings.</p>
            )}
          </div>
        </section>

        <div className="startup__actions">
          <button
            type="button"
            className="primary-button"
            onClick={handleStart}
            disabled={busy || modeBusy || readOnly || !selectedProfile}
            aria-busy={busy}
          >
            {busy ? 'Starting…' : 'Start session'}
          </button>

          <button
            type="button"
            className="ghost-button"
            onClick={handleSaveConfig}
            disabled={
              busy ||
              modeBusy ||
              readOnly ||
              !activeProfileName ||
              selectedProfile !== activeProfileName
            }
          >
            Save configuration
          </button>
        </div>

        {error ? <div className="feedback feedback--error">{error}</div> : null}
      </div>
    </div>
  );
}
