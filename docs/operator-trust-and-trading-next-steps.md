# Operator Trust And Normal Trading Next Steps

Date: 2026-06-20

## Current Truth

Krakked is no longer primarily blocked by ML, branch hygiene, or basic
deployability. The highest-value lane is now the operator path: starting,
watching, pausing, recovering, and understanding normal trading without hidden
state or misleading strategy claims.

Known current facts:

- Pinned-image Unraid deploy, upgrade, rollback, backup, and restore proof has
  passed with hard checks enabled.
- The first pinned-image paper soak completed on 2026-06-19. It proved the
  runtime/session lifecycle stayed observable overnight, but it did not prove a
  useful normal trading loop because enabled strategy contexts mostly waited for
  new closed strategy bars, and fresh evaluations still produced no actions,
  risk blocks, OMS orders, execution results, trades, or ledger entries. See
  [`soak-reports/2026-06-19-paper-soak.md`](./soak-reports/2026-06-19-paper-soak.md).
- The short pinned-image paper validation completed on 2026-06-20. It produced
  strategy intents, OMS dry-run fills, paper trades, portfolio positions, and
  useful closed-bar/no-signal diagnostics. It also exposed small operator-truth
  gaps around in-progress paper sync health, Unraid Docker health, recurring
  `ADA/USD` staleness, and reused profile naming. See
  [`soak-reports/2026-06-20-paper-validation.md`](./soak-reports/2026-06-20-paper-validation.md).
- The `v0.1.1-rc.10` forward decision soak completed on 2026-06-23. It ran on
  the intended pinned image and isolated profile, produced strategy-generated
  risk decisions, filled paper orders, persisted paper trades, and kept
  score-filtered candidate diagnostics legible. The follow-up paper
  emergency-flatten drill exposed a runtime defect: filled paper market flatten
  orders did not reduce synthetic wallet positions, so the emergency resume path
  retried until the container was stopped for containment. See
  [`soak-reports/2026-06-23-decision-soak-rc10-forward.md`](./soak-reports/2026-06-23-decision-soak-rc10-forward.md).
- The `v0.1.1-rc.11` controlled paper emergency-flatten confirmation completed
  on 2026-06-23. It seeded BTC/ETH paper positions through the deployed runtime,
  resumed with `emergency_flatten=true`, submitted priced market close orders,
  wrote paper trades, reduced the synthetic paper wallet to USD, and cleared
  emergency intent. See
  [`soak-reports/2026-06-23-rc11-paper-flatten-confirmation.md`](./soak-reports/2026-06-23-rc11-paper-flatten-confirmation.md).
- Runtime provenance is visible in health payloads, so deployment drift is no
  longer invisible.
- The EWMA market-risk signal is display-only. It is useful operator context,
  not a trading input.
- The 2026-06-16 ML volatility-forecasting rerun was strict-data ready and
  reached `ready_for_verdict`, but failed the EWMA gate. It should stay closed
  for trading and display influence.
- Standard bundled strategies remain research-stage or unproven. The UI must
  keep saying that plainly.
- `4h` and `1d` data for `BTC/USD`, `ETH/USD`, `SOL/USD`, and `ADA/USD` is
  continuous from `2025-12-01` through the current tail. `1h` still has an
  April/May 2026 gap, so default 1h strategy scoreboards remain legitimately
  data-blocked until deeper history is imported.
- Closed-bar/no-signal strategy diagnostics and explicit starter strategy
  parameters have landed, and the 2026-06-20 validation showed those diagnostics
  are useful during a real paper session.
- Live Balance, TradesHistory, Ledgers, never-synced startup, stale sync age,
  missing trade-history evidence, and material drift now degrade account truth
  and block live opening risk through the normal loop and OMS gate.
- The fake Kraken harness now proves one narrow
  AddOrder/OpenOrders/ClosedOrders/Balance/TradesHistory/Ledgers lifecycle with
  full fill, partial fill, restart reconciliation, degraded private reads, and
  the trade-ledger `refid` to TradesHistory ID assumption used by the
  account-truth gate.

Recently landed operator UX work:

- The stopped-session startup screen now shows live readiness when Live is
  selected and keeps Start disabled only when backend readiness reports real
  blockers.
- The activity tab now includes a grouped decision trace from strategy/risk
  actions through OMS/order result, while preserving the raw activity log.
- The decision trace distinguishes actionable, blocked, clamped, no-op, and
  degraded evidence states. In particular, "strategy evaluated and chose no
  trade" is now `no_action`, not `pending`, and risk clamping is visible.

## Implemented Lane: Live Automation Start Path

Goal: after the operator is already logged in/unlocked, live automation should
feel like an appliance control, not a developer ritual.

Target UX:

- Startup screen shows profile, mode, loop cadence, and live readiness.
- Choosing Live shows blockers inline: credentials, live gates, market-data
  freshness, strategy caps, kill switch, backup/provenance status, and paper
  certification state.
- If blockers are clear, one button starts live automation.
- If blockers remain, the button stays disabled and names the exact blocker.
- No second login prompt and no user-typed confirmation phrase in the normal UI
  flow. Backend gates, persistence, and audit events remain.

Implementation notes:

- Keep the existing backend live gates: `execution.mode="live"`,
  `execution.validate_only=false`, and `execution.allow_live_trading=true`.
- Keep `/api/system/mode` as the protected state change, but make the UI own the
  confirmation payload instead of making the operator type ceremony text.
- Keep `/api/system/live-readiness` read-only and visible before start.
- Tests cover live-ready start, live-blocked start, no extra password prompt
  after unlock, decision trace no-op, blocked, clamped, execution-failed, and
  degraded evidence states.

Acceptance check:

1. Unlock once.
2. Select Live on the startup screen.
3. Read readiness state.
4. Click Start live automation.
5. Session becomes active, health reports live mode, and readiness/provenance
   stay visible.

Verification commands used for this local lane:

- `poetry run pytest tests/ui/test_system_routes.py -k "cockpit_snapshot"`
- `npm run test:run -- App.operator-paths.test.tsx`

## Next Lane: Decision-Loop Proof And Paper Soak

Before optimizing strategy knobs or planning live smoke, make the operator
surface boring around the account and control states that the 2026-06-20
validation surfaced. The first paper soak covered runtime lifecycle. The short
paper validation proved that the paper loop can produce strategy, OMS, trade,
and portfolio evidence. The operator-truth cleanup and seeded emergency-flatten
drill now close the main remaining operator-control proof gap. The next lane is
to run the decision-loop proof sequence in
[`decision-loop-proof-plan.md`](./decision-loop-proof-plan.md) before starting
the longer paper soak.

Any future decision soak must also complete
[`deployment-preflight-checklist.md`](./deployment-preflight-checklist.md)
before the session starts, so the report records the intended image/SHA,
runtime provenance, profile, DB path, monitor path, and replay evidence source.

Claim boundary:

- deterministic replay proves strategy/risk/OMS with simulated fills;
- deterministic fake-Kraken live-config tests prove live account-truth gates on
  strategy-generated opening risk;
- forward paper soak proves runtime/operator behavior with synthetic paper
  fills;
- paper mode does not exercise live account-truth gates or real Kraken
  reconciliation.

Completed evidence:

- A long paper session on the pinned-image Unraid install stayed active and
  observable overnight.
- Pause/resume, implemented as session stop/start, worked.
- Container restart preserved profile/config/wallet and avoided image drift, but
  did not auto-resume the active session.
- Backup/export/restore worked after using the profile-isolated DB path.
- Emergency flatten is now covered by deterministic seeded tests for the API
  route and background resume path: cancel-first, remaining open-order residue,
  degraded account-truth refusal, verified closeout, persisted execution state,
  and dust/no-retry behavior.
- A short paper validation on `v0.1.1-rc.8` produced strategy intents, OMS
  dry-run fills, paper trades, portfolio positions, and useful
  deferred/no-signal strategy summaries.
- The corrected `v0.1.1-rc.9` decision soak proved deployment/runtime
  provenance, but did not produce a forward decision chain. `rs_rotation`
  emitted two zero-confidence candidates that were score-filtered before risk,
  then its 24h rebalance cadence made later bars quiet. That run motivated the
  `v0.1.1-rc.10` score-filter legibility cleanup.
- The corrected `v0.1.1-rc.10` forward decision soak proved the paper
  decision-loop runtime path on the intended image: `trend_core` generated
  actions, risk clamped/blocked over-budget intents, OMS wrote filled paper
  orders, and paper trades/snapshots persisted for normal limit-order fills.
  `rs_rotation` and later `trend_core` score-filtered candidates with explicit
  score, threshold, and reason fields.
- The corrected `v0.1.1-rc.11` controlled paper flatten confirmation proved the
  background paper emergency-flatten runtime path after the rc10 failure:
  market close orders were priced, paper trades were inserted, synthetic BTC/ETH
  balances went to zero, and emergency intent cleared.

Remaining proof targets:

- Separate clamped and blocked reason fields in persisted/API risk decisions so
  a clamped action is not shown as if it were blocked.
- Define validate-only live drill criteria before any tiny live smoke.
- Keep stale pairs used by enabled strategies or open positions as
  session-critical blockers. Disabled/watchlist/global stale pairs, including
  recurring `ADA/USD` noise, should remain warnings.
- Use the dated paper-validation profile suggestion for the next run so
  evidence is easier to compare.
- Use the active DB path shown in the operator paths health surface for
  `db-backup` and `export-install --db-path`.

This is the bridge between "the deterministic gates exist" and "an operator can
tell what the appliance is doing, why it is safe, and why it is or is not
trading."

For the stricter money-safety proof contract, use
[`money-safety-proof-plan.md`](./money-safety-proof-plan.md). That document is
the routing layer for live-capital readiness; this note remains the operator
workflow lane.

## Standard Strategy Optimization Boundary

Do not tune strategy parameters just because the current rows are unproven.
That would recreate the ML loop in another costume.

Useful strategy work now:

- Keep every enabled strategy's pairs, timeframes, and sizing params explicit in
  the active profile so scoreboards do not depend on constructor defaults.
- Keep strict preflight as the source of truth. If data is partial, fix data or
  choose a supported window; do not loosen the gate.
- Improve "why did/didn't it trade?" reporting: signal reason, score/filter
  reason, risk clamp/block reason, and final order result in one chain.
- Verify sizing and caps before signal tuning. A strategy that constantly
  requests exposure above caps is a sizing problem before it is an alpha problem.
- Run one saved scoreboard per written hypothesis. No parameter wandering on
  recent windows.

Current strategy stance:

- `trend_core`: enabled starter, research-stage/unproven.
- `majors_mean_rev`: enabled starter, inactive/weak in replay evidence.
- `rs_rotation`: disabled by default, available for research; latest strict 4h
  probe traded in all 6 regime windows but was negative in all 6.
- `vol_breakout`: disabled by default because its natural 15m lane lacks enough
  durable cache coverage and prior probes were sizing/cap sensitive.
- ML strategies: research-only unless a future pre-registered gate passes.

## Data Lane

For strategy evidence:

- Import Q2 2026 Kraken OHLCVT history when available, especially `1h` for
  `BTC/USD`, `ETH/USD`, `SOL/USD`, and `ADA/USD`.
- Until then, use 4h/1d-supported strict windows for cross-regime strategy
  probes.
- Record continuity checks, not just first/last timestamps, because range checks
  can hide middle gaps.

## Recommended Order

1. Separate clamp and block diagnostics in decision persistence/API/UI so risk
   evidence remains operator-truthful.
2. Define validate-only live drill and tiny-live-smoke criteria before any live
   smoke attempt.
3. Clean up the remaining operator affordances from the soak: pause/resume
   wording, restart no-auto-resume wording, and profile-aware backup/export.
4. Re-run a short paper validation only if the cleanup changes health,
   readiness, control surfaces, or emergency flatten.
5. Only after account-truth gates and operator drills are boring should tiny
   live smoke testing be considered, with conservative caps and a written stop
   condition.
