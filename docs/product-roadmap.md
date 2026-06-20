# Krakked Product Roadmap

## Product Definition

Krakked is a Docker-first, self-hosted Kraken trading product aimed at California and broader U.S. users.

The intended v1 product is:

- Safe paper/execution and research infrastructure first.
- Strategy-aware, with per-strategy controls and attribution, while keeping bundled strategies clearly labeled as research-stage until evidence improves.
- Friendly to beginners while still exposing advanced controls for experienced operators.
- Sellable/distributable software rather than an internal-only bot.
- Extensible to optional machine-learning research and continuous training workflows.

## California / U.S. Assumptions

Krakked uses the `US_CA` region profile for California.

Current product assumptions:

- Spot trading remains the default and lowest-complexity product scope for v1.
- Margin and derivatives are out of scope for v1, even if Kraken supports some U.S. margin/derivatives offerings for eligible users.
- Live trading remains a plumbing and safety-readiness target, not a claim that current bundled strategies are profitable.

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
   - Strategy surfaces must label unproven bundled strategies as research-stage until a documented gate promotes them.

4. ML controls
   - ML stays in scope as research infrastructure.
   - Training can run continuously, but model promotion must be controlled and resumable.
   - Checkpoints and training metadata must survive machine crashes or shutdowns.
   - Reports must expose training target, prediction target, cost hurdle, and
     cash/buy-hold baselines before any runtime wiring discussion.
   - Cross-strategy claims should use the unified evidence scoreboard, not an
     ML-only report in isolation.
   - The 2026-06-16 volatility-forecasting slice reached a real verdict and
     closed for trading influence. The HAR-RV model failed the EWMA benchmark
     under strict data, so ML should not affect exposure or display risk until
     a genuinely new written hypothesis is proposed.

5. Live-trading readiness
   - Paper mode remains the proving ground.
   - Live mode must be operationally first-class if enabled later, not treated as an afterthought.
   - Emergency controls, audits, and runtime safety checks must stay intact in live mode.
   - Money-safety readiness is tracked in
     [`money-safety-proof-plan.md`](./money-safety-proof-plan.md); live UI polish
     and strategy evidence do not replace the safety proof gates.

## Current Repo State

The codebase is past the original architecture-building phase.

Implemented or substantially in place:

- Phases 1-7 of the original engineering roadmap
- Full internal `krakked` rename
- Docker-oriented install and runtime docs
- GitHub Actions CI plus a tag-driven release workflow
- Runtime deployment provenance in health payloads
- Passing Unraid pinned-image deploy/upgrade/rollback proof with hard
  run-once and restore checks enabled
- Strategy weighting support in the runtime
- Crash-safe ML checkpoint/resume foundations
- Backup, export, import, and upgrade-oriented operator tooling
- Operator cockpit shell that now prefers one cockpit snapshot, partial rendering, and local section degradation over global loading deadlocks
- Paper mode now uses a profile-scoped persistent synthetic wallet, with live exchange balances kept only as optional reference context
- Strategy-silence diagnostics now distinguish closed-bar deferrals, missing
  data, stale data, strategy errors, no-signal decisions, and emitted intents
  for enabled starter strategies
- Live Balance, TradesHistory, Ledgers, never-synced startup, stale sync age,
  missing trade-history evidence, and material drift now degrade account truth
  and block live opening risk through the normal loop and OMS gate
- The fake Kraken harness now proves one narrow coherent
  AddOrder/OpenOrders/ClosedOrders/Balance/TradesHistory/Ledgers lifecycle,
  including full fill, partial fill, restart reconciliation, private-read
  degradation/staleness, and the trade-ledger `refid` to TradesHistory ID
  assumption used by the account-truth gate
- Strategy-source evidence currently does not support runtime promotion for the tested bundled/source candidates
- Strict cached `4h` and `1d` OHLC now covers `BTC/USD`, `ETH/USD`,
  `SOL/USD`, and `ADA/USD` from `2025-12-01` through the current tail; `1h`
  still has a hard `2026-03-31T23:00Z -> 2026-05-17T13:00Z` gap until deeper
  Q2 history is imported.
- The EWMA market-risk signal is display-only and explicitly has no trading
  effect.

Still needing real-world validation or product work:

- Repeatable release sign-off on future pinned image tags
- More polished strategy-management and attribution UX
- Remaining cockpit polish around startup/setup fan-out and first-run lifecycle edges
- A cleaner startup/unlock/session lifecycle model so first-run and reinitialization states stay explicit and predictable
- Simple/Advanced UI presentation split
- ML operator controls beyond the checkpoint/resume foundation
- Unified strategy evidence reporting with explicit cost semantics and
  cash/buy-hold comparisons
- Live-trading readiness drills and operator runbooks after paper/execution reliability is proven
- Short paper validation of strategy diagnostics, account-truth blockers, OMS
  evidence, portfolio snapshots, and operator copy before any live-capital claim
- Live automation UX polish so a prepared operator can start live automation
  from the UI with one obvious start action after readiness is visible.
- Commercial packaging, licensing, and legal/business review

## Current Operator Reality

Krakked is now closer to an operator-facing control room than a hobby bot shell, but the current product still has some honest gaps:

- Paper mode is a local persistent synthetic wallet that can exercise the strategy, risk, OMS, and portfolio loops without transmitting live orders.
- Exchange balances are now optional reference context in paper mode, not the paper account baseline.
- In live mode, missing, failed, stale, or materially drifting account truth
  blocks new opening risk. That is not the same as full live readiness: the next
  proof step is a short paper validation pass plus seeded operator drills, not a
  live-capital claim.
- Current strategy-source evidence does not yet support runtime promotion of `rs_rotation`, `rs_rotation_v2`, `trend_core` signal-quality claims, global top-N momentum proxies, or pair-local source variants.
- ML remains in scope as infrastructure, but the current volatility-forecasting
  lane is closed for runtime influence: the 2026-06-16 strict rerun was ready
  for verdict and failed EWMA by a wide margin. Do not iterate variants on that
  same target.
- The honest current risk display is the EWMA card: calibrated enough to show
  operator context, but explicitly display-only with no trading effect.
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

Continuous learning is in scope as infrastructure, but it is not the next
operator-value lane. The current rule is:

- Training state must checkpoint atomically.
- Interrupted training must resume cleanly after restart.
- Inference should keep using the last known-good model if training is interrupted.
- New model versions must pass cross-window, cost-aware, cash/buy-hold baseline proof before any active-trading plan is written.
- The failed volatility-forecasting target should stay closed unless a new
  written hypothesis changes the target, data, or product use materially.
- ML must not affect trading, exposure, strategy selection, or risk display
  until a pre-registered gate beats the relevant simple baseline.

## Distribution Recommendation

The recommended first commercial shape is licensed self-hosted software:

- Docker-based install
- User brings their own Kraken account and API keys
- Local or VPS deployment
- Paid license/subscription for the software, updates, and support

This keeps custody and exchange credentials with the customer while making the product easier to trust and distribute.

## Next Milestones

The next milestones are product-facing rather than architecture-facing:

1. Deployment Trust
   - Maintain the passing Unraid pinned-image baseline as the release sign-off
     standard.
   - For future release candidates, validate first boot, persistence, upgrades,
     rollback, backups, restore, and runtime provenance using
     [`deployment-proof.md`](./deployment-proof.md).
   - Treat source-mode host checks as development sanity, not customer-ready
     proof.

2. Operator UX And Truth Labels
   - Improve strategy toggles, weights, and per-strategy attribution in the UI without implying proven production edge.
   - Make EWMA risk display explicitly display-only with no trading effect.
   - Make strategy evidence labels plainly show research-stage/unproven status.
   - Turn live automation into a readiness-first start path: one clear
     operator action from the startup screen after blockers are visible, with
     backend safety gates and audit logs preserved.
   - Continue cockpit snapshot V1 polish by trimming remaining startup/setup fan-out and tightening first-run lifecycle states.
   - Introduce a clearer Simple vs Advanced presentation model.
   - Surface ML status and training/checkpoint information more directly.

3. Reliability and Live-Readiness Plumbing
   - Formalize operational runbooks and pre-live checklists.
   - Tighten live-mode guidance, safety prompts, and emergency control flows.
   - Validate the stale-sync age, private-read failure, missing trade-history,
     and material-drift gates in operator-facing health/live-readiness surfaces.
   - Keep extending the fake Kraken/fault harness only around production seams
     needed to prove reconciliation, stale reads, failed reads, and restart
     recovery.
   - Use short paper validation passes to confirm diagnostics, strategy/control
     changes, pause/resume, and fresh backup behavior before any small live
     smoke.
   - Practice rollback, restore, and upgrade drills on realistic deployments.
   - Make the startup/unlock/session lifecycle trustworthy enough that operators can confidently distinguish slow warmup from a real fault.

See [`operator-trust-and-trading-next-steps.md`](./operator-trust-and-trading-next-steps.md)
for the current next-lane plan.

4. Commercial Packaging
   - Define the self-hosted licensing/update model.
   - Produce customer-facing install and onboarding material.
   - Get human legal/business review before broad sale or distribution.
