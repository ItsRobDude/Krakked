# Strategy Source Decision

Date: 2026-05-30

## Decision

- `promote_pair_local_source=false`
- `runtime_wiring_approved=false`
- Pause the current strategy-source lane until there is a new written
  hypothesis.
- Treat tested bundled/source candidates as research-stage, not yet validated
  production strategy edge.

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

Near-term Krakked work should move back to:

- replay/data reliability
- operator visibility
- market/risk-state reporting
- paper-mode safety and observability

Resume strategy-source work only with a genuinely new hypothesis and a written
promotion gate before implementation.

Avoid additional bounded source gates that only re-test the same families
without changing the hypothesis. The next useful path is operator trust,
replay/data reliability, risk-state visibility, and unified strategy evidence
using the scoreboard documented in `docs/replay-experiment-log.md`.
