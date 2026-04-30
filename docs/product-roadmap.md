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

## Current Repo State

The codebase is past the original architecture-building phase.

Implemented or substantially in place:

- Phases 1-7 of the original engineering roadmap
- Full internal `krakked` rename
- Docker-oriented install and runtime docs
- GitHub Actions CI plus a tag-driven release workflow
- Strategy weighting support in the runtime
- Crash-safe ML checkpoint/resume foundations
- Backup, export, import, and upgrade-oriented operator tooling
- Operator cockpit shell that now prefers one cockpit snapshot, partial rendering, and local section degradation over global loading deadlocks
- Paper mode now uses a profile-scoped persistent synthetic wallet, with live exchange balances kept only as optional reference context

Still needing real-world validation or product work:

- Docker smoke testing on an actual deployment host
- More polished strategy-management and attribution UX
- Remaining cockpit polish around startup/setup fan-out and first-run lifecycle edges
- A cleaner startup/unlock/session lifecycle model so first-run and reinitialization states stay explicit and predictable
- Simple/Advanced UI presentation split
- ML operator controls beyond the checkpoint/resume foundation
- Live-trading readiness drills and operator runbooks
- Commercial packaging, licensing, and legal/business review

## Current Operator Reality

Krakked is now closer to an operator-facing control room than a hobby bot shell, but the current product still has some honest gaps:

- Paper mode is a local persistent synthetic wallet that can exercise the strategy, risk, OMS, and portfolio loops without transmitting live orders.
- Exchange balances are now optional reference context in paper mode, not the paper account baseline.
- The active dashboard now has cockpit snapshot V1 for coherent active-session refreshes, operator-safe section degradation, and visible snapshot freshness. Remaining cockpit work is mostly around startup/setup fan-out and clearer first-run lifecycle states.
- Startup, unlock, and session-start flows have improved significantly, but they still need a tighter lifecycle model before the product feels fully polished for a first-time operator.

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

## Next Milestones

The next milestones are product-facing rather than architecture-facing:

1. Deployment Proof
   - Run Krakked end-to-end on a real Docker host.
   - Validate first boot, persistence, upgrades, backups, and restore.
   - Confirm the published-image path works cleanly for a non-developer operator.

2. Operator UX
   - Improve strategy toggles, weights, and per-strategy attribution in the UI.
   - Continue cockpit snapshot V1 polish by trimming remaining startup/setup fan-out and tightening first-run lifecycle states.
   - Introduce a clearer Simple vs Advanced presentation model.
   - Surface ML status and training/checkpoint information more directly.

3. Live Trading Readiness
   - Formalize operational runbooks and pre-live checklists.
   - Tighten live-mode guidance, safety prompts, and emergency control flows.
   - Practice rollback, restore, and upgrade drills on realistic deployments.
   - Make the startup/unlock/session lifecycle trustworthy enough that operators can confidently distinguish slow warmup from a real fault.

4. Commercial Packaging
   - Define the self-hosted licensing/update model.
   - Produce customer-facing install and onboarding material.
   - Get human legal/business review before broad sale or distribution.
