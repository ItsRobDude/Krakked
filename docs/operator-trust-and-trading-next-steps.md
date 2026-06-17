# Operator Trust And Normal Trading Next Steps

Date: 2026-06-16

## Current Truth

Krakked is no longer primarily blocked by ML, branch hygiene, or basic
deployability. The highest-value lane is now the operator path: starting,
watching, pausing, recovering, and understanding normal trading without hidden
state or misleading strategy claims.

Known current facts:

- Pinned-image Unraid deploy, upgrade, rollback, backup, and restore proof has
  passed with hard checks enabled.
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

Implemented in the local working tree after this note was first drafted:

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

## Next Lane: Normal Trading Reliability

Before optimizing strategy knobs, prove the normal loop is boring:

- Run a long paper session on the pinned-image Unraid install with the current
  starter profile.
- Verify cycle cadence, market-data freshness, strategy decisions, risk blocks,
  OMS records, portfolio snapshots, and UI snapshot freshness.
- Exercise pause/resume, emergency flatten, strategy toggle, weight change, and
  restart persistence.
- Export a backup after the session and restore it into scratch paths.
- Save a dated report that says whether anything was unclear to the operator.

This is the bridge between "the code works" and "I would trust this appliance to
run unattended."

For the stricter money-safety proof contract, use
[`money-safety-proof-plan.md`](./money-safety-proof-plan.md). That document is
the routing layer for live-capital readiness; this note remains the operator
workflow lane.

## Standard Strategy Optimization Boundary

Do not tune strategy parameters just because the current rows are unproven.
That would recreate the ML loop in another costume.

Useful strategy work now:

- Make every enabled strategy's pairs, timeframes, and sizing params explicit in
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

1. Re-run pinned-image proof after the local UI/API changes land on the image.
2. Normal paper-session soak on the pinned-image host.
3. Explicit starter strategy params in profile/config.
4. One strict evidence scoreboard after Q2 `1h` history exists, or a deliberate
   4h-only hypothesis if the strategy truly operates on 4h.
5. Only then consider tiny live smoke testing with conservative caps and a
   written stop condition.
