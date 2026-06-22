# 2026-06-22 Decision-Loop Replay Gate

## Summary

The deterministic replay gate passes for decision-loop proof purposes. The
full-tail run produced a large, legible strategy/risk/OMS/fill/trade/snapshot
stream with complete strict coverage and no execution errors. Its shared replay
`trust_level` is `limited` only because the risk engine blocked a small number
of over-budget `rs_rotation` actions.

This is not a strategy-edge proof, not an alpha claim, and not a live-capital
proof. Replay runs in simulation mode and does not exercise real Kraken
TradesHistory/Ledgers reconciliation or live account-truth gates.

## Run Inputs

- Profile/config artifact: `reports/decision-soak-2026-06-22/decision-soak-2026-06-22.config.yaml`
- Primary strategy: `rs_rotation`
- Pairs: `BTC/USD`, `ETH/USD`, `SOL/USD`, `ADA/USD`
- Timeframe: `4h`
- Strict data: enabled
- Starting cash: `$10,000`
- Fee model: `25 bps`
- Refreshed common 4h tail: `2026-06-21T20:00:00Z`

The original `2025-12-01T00:00:00Z` start failed strict preflight because the
local cache began at that timestamp and had no pre-window warmup bars. The
supported full-tail replay therefore used `2025-12-08T00:00:00Z` as the start.

## Full-Tail Replay Evidence

- Window: `2025-12-08T00:00:00Z` -> `2026-06-21T20:00:00Z`
- Strict preflight: ready
- Warmup: ready
- `summary.trust_level`: `limited`
- `summary.trust_note`: `Limited signal: some strategy actions were blocked by guardrails.`
- Actions: `192`
- Orders/fills: `153 / 153`
- Execution errors: `0`
- Blocked actions: `6`
- Blocked ratio: `3.125%`
- Clamped actions: `54`
- Realized PnL: `-$264.6151`
- Ending equity: `$9,743.4861`

All blocked actions were explicit `rs_rotation` strategy-budget cap blocks, for
example `Strategy rs_rotation budget exceeded (751.64 > 498.39)`. Persisted
decision rows recorded those blocks as `action_type='none'`, target notional
`0`, and a legible `block_reason`.

SQLite evidence counts:

- `execution_plans`: `1176`
- `execution_results`: `1176`
- `decisions`: `192`
- `execution_orders`: `153`
- `execution_order_events`: `153`
- `trades`: `153`
- `snapshots`: `181`
- `ledger_entries`: `0` (expected for simulation replay)

Decision-loop verdict: **pass**. The replay has complete coverage, non-zero
actions, non-zero fills, no execution errors, and low-ratio legible risk blocks.
The `limited` trust label is acceptable here because it is caused solely by
guardrail activity, not incomplete coverage, zero fills, dominant blocking, or
execution failure.

Strategy-edge verdict: **not proved**. `rs_rotation` remains research-only and
negative in this replay. The purpose of this gate is plumbing and operator
truth, not alpha.

## Supporting Zero-Block Candidate

A documented zero-block replay window also passes the shared
`decision_helpful` gate:

- Window: `2026-03-21T00:00:00Z` -> `2026-04-20T00:00:00Z`
- Window sets: `long_4h`, `regime_diverse_4h`
- `summary.trust_level`: `decision_helpful`
- Actions: `33`
- Orders/fills: `26 / 26`
- Blocked actions: `0`
- Execution errors: `0`
- Realized PnL: `-$50.9055`

This is useful supporting evidence, but it should not replace the full-tail
decision-loop gate. The full-tail replay is more representative because it
exercises the risk clamp/block path and still reports it honestly.

## Scope Boundary

- Replay proves the strategy/risk/OMS/simulated-fill/persistence/reporting path.
- Replay does not prove live account-truth gates or real Kraken reconciliation.
- The deterministic fake-Kraken live-config test remains the bridge proof for
  live account-truth gates on strategy-generated opening risk.
- The next forward paper soak should be described as runtime/operator evidence
  with synthetic paper fills, not as a live-capital proof.
