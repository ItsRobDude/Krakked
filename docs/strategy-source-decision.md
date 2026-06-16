# Strategy Source Decision

Date: 2026-05-30

## Decision

- `promote_pair_local_source=false`
- `runtime_wiring_approved=false`
- Pause the current strategy-source lane until there is a new written
  hypothesis.
- Treat tested bundled/source candidates as research-stage, not yet validated
  production strategy edge.
- This is not an ML removal decision and not a permanent claim that no source
  can work. It closes only the tested source families under the current evidence
  frame.

## Evidence

Artifacts:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\target-source-research-20260530\aggregate.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\pair-local-source-research-20260530\aggregate.json`

Tested source families that are not currently promotable:

- `rs_rotation`
- `trend_core` as strategy-quality evidence
- global top-N momentum and target-weight proxies
- pair-local dual momentum, vol-adjusted momentum, trend pullback, oversold
  reversion, and breakout continuation

## Why

The latest pair-local proof gate found no pair/scenario combination that passed
both recent and long cached `4h` out-of-sample window sets at the primary
20 percent allocation. That is a current evidence boundary, not a permanent
claim that these ideas can never work.

This result is not the same cap/allocation artifact that affected earlier
`rs_rotation` replay interpretation. The pair-local harness sized directly from
the requested allocation (`target_weight = allocation_pct / 100`) into target
notional, so the 20 percent primary allocation was actually expressible.

Notable partial positives were not enough:

- ETH/USD `pair_oversold_reversion` passed the recent-window gate only.
- SOL/USD and ETH/USD `pair_trend_pullback` had positive slices, but active
  exposure was too sparse and the source missed too much pair upside while cash.

## Operating Boundary

Do not wire any of these sources into runtime strategy, risk behavior, order
routing, paper/live execution, or operator UI defaults based on the current
evidence.

The corrected forward path is not another small source gate over the same
signals. It is a shared evidence frame with regime-diverse windows, explicit
risk-adjusted metrics, and a simple hand-coded market-state overlay baseline
that future ML exposure-scaling work must beat. See
[`regime-diverse-evidence-plan.md`](./regime-diverse-evidence-plan.md).

Near-term Krakked work should move back to:

- replay/data reliability
- operator visibility
- market/risk-state reporting
- paper-mode safety and observability
- unified strategy evidence and ML overlay research under one comparable
  regime-diverse frame

Resume strategy-source work only with a genuinely new hypothesis and a written
promotion gate before implementation.

Avoid additional bounded source gates that only re-test the same families
without changing the hypothesis. The next useful strategy-research path is
regime-diverse unified evidence and minimal ML exposure-scaling research, not a
large meta-labeling harness over sparse strategy events.

## 2026-05-31 Update: Regime-Diverse Cross-Regime Negative (lane closed)

The minimal ML exposure-scaling research from the line above was run and
instrumented (commit `efb65d0`; see
[`regime-diverse-evidence-plan.md`](./regime-diverse-evidence-plan.md)). The
result strengthens this decision from "current evidence frame" to a closed lane:

- The `regime_diverse_4h` set is genuinely regime-diverse by benchmark/basket
  return: ~2 uptrend / 1 downtrend / 2 chop windows (`regime_coverage_sufficient
  = true`). An earlier read that it was "down/chop only, no uptrend" was wrong —
  it came from reading the `trend_rank_proxy` strategy return instead of the
  market return. Acquiring prior-cycle data is therefore **not** the blocker; the
  cached data already spans regimes.
- The minimal ML exposure overlay failed to beat the simple hand-coded top-2 soft
  `target_scale` baseline on a regime-diverse set (avg return delta `-0.2356%`,
  avg max-drawdown delta `+0.1867%`). This is a legitimate cross-regime negative,
  not a downtrend-only artifact.
- Sharper diagnosis: the `trend_rank_proxy` source under-captures available
  upside even in up-regimes (e.g. `20251221-20260120` market basket `+4.60%` vs
  unscaled source `-1.60%` after fees/timing). The problem is source quality, not
  the data, the regime sample, or the ML framing.

Narrow scope of this closeout (do not overgeneralize): the **current 4h-majors
momentum / trend / mean-reversion / breakout source families failed after costs
across a regime-diverse set.** This is not a claim that no trading source can
ever work.

Forward path:

- Pivot engineering to the product (operator visibility, paper-mode safety and
  observability, deployment proof, live-readiness). Strategy sources stay
  research-stage.
- Reopen source research only as an opt-in program behind a written hypothesis
  and a written gate. The one genuinely-different candidate is wider-universe
  cross-sectional selection with different horizon/liquidity rules, measured on
  the regime-aware unified scoreboard — a future program, not another small gate.
- Do not iterate ML overlay features on the `trend_rank_proxy` source; a
  defensive rule already beats it across regimes.

## 2026-06-16 Update: Data Recheck And Vol-Forecast ML Closeout

Follow-up checks after importing multi-pair Kraken history clarified the
remaining blockers:

- The `4h`/`1d` data path for the four starter pairs is now usable and
  continuous from `2025-12-01` through the current tail.
- The default `1h` evidence path still has a real April/May 2026 gap, so strict
  scoreboards that require `1h` remain data-blocked until Q2 history is
  imported.
- A strict `4h` `rs_rotation` probe over `regime_diverse_4h` ran with `6 / 6`
  ready windows and traded in all six, but had `0 / 6` positive windows and
  stayed `unproven`.
- The HAR-RV volatility forecast lane reached `ready_for_verdict` with strict
  data and failed EWMA badly (`lane_status=close_volatility_forecast_lane`).

Updated operating boundary:

- Do not restart ML or strategy tuning loops from these results.
- Keep bundled strategies truth-labeled as research-stage/unproven.
- Treat EWMA as display-only risk context, not strategy edge.
- Shift active work to operator trust, live automation usability, normal
  session reliability, and evidence traceability.
