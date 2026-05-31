# Strategy Source Decision

Date: 2026-05-30

## Decision

- `promote_pair_local_source=false`
- `runtime_wiring_approved=false`
- Stop the current strategy-source development lane.

## Evidence

Artifacts:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\target-source-research-20260530\aggregate.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\pair-local-source-research-20260530\aggregate.json`

Rejected source families:

- `rs_rotation`
- `trend_core` as strategy-quality evidence
- global top-N momentum and target-weight proxies
- pair-local dual momentum, vol-adjusted momentum, trend pullback, oversold
  reversion, and breakout continuation

## Why

The final pair-local proof gate found no pair/scenario combination that passed
both recent and long cached `4h` out-of-sample window sets at the primary
20 percent allocation.

Notable partial positives were not enough:

- ETH/USD `pair_oversold_reversion` passed the recent-window gate only.
- SOL/USD and ETH/USD `pair_trend_pullback` had positive slices, but active
  exposure was too sparse and the source missed too much pair upside while cash.

## Operating Boundary

Do not wire any of these sources into runtime strategy, risk behavior, order
routing, paper/live execution, or operator UI defaults.

Near-term Krakked work should move back to:

- replay/data reliability
- operator visibility
- market/risk-state reporting
- paper-mode safety and observability

Reopen strategy-source work only with a genuinely new hypothesis and a written
promotion gate before implementation.
