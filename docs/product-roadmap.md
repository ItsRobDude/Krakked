# Krakked Product Roadmap

## Product Definition

Krakked is a Docker-first, self-hosted Kraken trading product aimed at California and broader U.S. users.

The intended product is:

- Live-trading capable, with paper trading as a staging environment rather than the final destination.
- Strategy-driven, with per-strategy enable/disable controls and simple user-facing weights.
- Friendly to beginners while still exposing advanced controls for experienced operators.
- Sellable/distributable software rather than an internal-only bot.
- Extensible to optional machine-learning strategies and continuous training workflows.

## California / U.S. Assumptions

Krakked uses the `US_CA` region profile for California.

Current product assumptions:

- Spot trading remains the default and lowest-complexity product scope for v1.
- Margin and derivatives are out of scope for v1, even if Kraken supports some U.S. margin/derivatives offerings for eligible users.
- Live trading is the target end state once deployment, controls, and safety flows are production-ready.

This is a product and engineering constraint, not a claim that California law categorically forbids all non-spot activity.

## V1 Priorities

1. Docker-first deployment
   - `docker compose` should be the primary installation and runtime path.
   - Persist config, secrets, model checkpoints, logs, and SQLite state in mounted volumes.
   - Include health checks, restart policies, and a documented upgrade path.
   - Support explicit config/data directory overrides so container layouts are predictable.

2. Consolidate the `krakked` namespace
   - Keep package/module/config naming aligned on `krakked`.
   - Preserve migration notes for existing installs and operational runbooks.
   - Treat follow-up compatibility shims as deliberate, temporary decisions.

3. Strategy management UX
   - Users can enable/disable strategies live in the UI.
   - Users can assign strategy weights on a 1-100 scale.
   - The engine normalizes weights internally and exposes attribution in the UI.

4. ML controls
   - ML strategies are toggleable per strategy.
   - Training can run continuously, but model promotion must be controlled and resumable.
   - Checkpoints and training metadata must survive machine crashes or shutdowns.

5. Live-trading readiness
   - Paper mode remains the proving ground.
   - Live mode must be operationally first-class, not treated as an afterthought.
   - Emergency controls, audits, and runtime safety checks must stay intact in live mode.

## UX Recommendation

Krakked should ship with two views:

- Simple View
  - Strategy toggles
  - Weight sliders or inputs (`1-100`)
  - Equity, PnL, drawdown, open positions, recent executions
  - Paper/live mode visibility
  - Clear emergency controls

- Advanced View
  - Per-strategy attribution and correlations
  - ML training/checkpoint status
  - Runtime logs and execution details
  - More detailed risk and config controls

## Strategy Weighting Recommendation

Use a simple user-facing weighting model:

- Each enabled strategy gets a weight from `1` to `100`.
- Disabled strategies contribute zero.
- The runtime normalizes the active weights before allocation.
- The UI should explain this in plain language rather than percentages-by-default.

## ML Recommendation

Continuous learning is in scope, but it should be implemented as a crash-safe pipeline:

- Training state must checkpoint atomically.
- Interrupted training must resume cleanly after restart.
- Inference should keep using the last known-good model if training is interrupted.
- New model versions should be validated before they become active for trading.

## Distribution Recommendation

The recommended first commercial shape is licensed self-hosted software:

- Docker-based install
- User brings their own Kraken account and API keys
- Local or VPS deployment
- Paid license/subscription for the software, updates, and support

This keeps custody and exchange credentials with the customer while making the product easier to trust and distribute.

## Immediate Engineering Focus

The next major implementation track should be:

1. Harden and validate Docker deployment.
2. Design and implement strategy weighting in the runtime and UI.
3. Formalize ML checkpoint/resume behavior.
4. Finish the remaining post-rename cleanup and migration notes.
