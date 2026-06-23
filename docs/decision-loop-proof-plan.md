# Decision-Loop Proof And Paper Soak Acceptance

Date: 2026-06-22

## Purpose

The June 19 paper soak proved runtime lifecycle, but the loop was alive and
silent: no strategy actions, orders, trades, or ledgers were produced. This
proof lane answers one narrower question:

> When a strategy produces an intent, does Krakked preserve a legible chain from
> signal to risk, OMS, fill, portfolio state, UI/reporting, and diagnostics?

This is not a strategy-edge proof and not a live-capital proof. `rs_rotation` is
used only because the strict 4h evidence path reliably generates decisions while
remaining research-only and unproven.

## What Each Phase Proves

| Phase | Mode | Proves | Does not prove |
| --- | --- | --- | --- |
| Strict preflight | cached/offline | The chosen 4h pair/window data is ready before replay | Strategy quality or exchange behavior |
| Deterministic replay | `simulation` | Real strategy/risk/OMS path with simulated immediate fills, persisted decisions/orders/results, portfolio snapshots, and diagnostics | Kraken Balance/TradesHistory/Ledgers reconciliation or live account-truth gates |
| Fake-Kraken live-config harness | test-only `live` config against `FakeKrakenRESTClient` | Strategy-generated opening risk reaches the live account-truth gate; healthy truth submits; degraded truth blocks; fake TradesHistory/Ledgers reconcile after fill | Real Kraken behavior, real latency, or production API-key safety |
| Forward paper soak | `paper` | Runtime lifecycle, operator surfaces, synthetic paper wallet, paper fills, activity traces, profile backup/export, and diagnostics under a real session | Live account-truth gates, real Kraken reconciliation, or live AddOrder |

Paper and replay mode must never be described as proof that live account-truth
gates ran. Those gates are live-only and are proved separately by deterministic
fake-Kraken tests.

## Profile Recipe

Use a fresh isolated profile named for the run date, for example
`decision-soak-2026-06-21`.

Profile intent:

- mode: paper;
- live submission: disabled (`allow_live_trading=false`);
- pairs: `BTC/USD`, `ETH/USD`, `SOL/USD`, `ADA/USD`;
- primary timeframe: `4h`;
- data policy: strict coverage; do not loosen to force a run;
- strategies: enable `rs_rotation` as the event generator and keep
  `trend_core` / `majors_mean_rev` enabled only when their strict data
  requirements are ready enough for useful silence diagnostics;
- `rs_rotation` params: starter 4h params, explicit in the profile, with no
  parameter tuning to manufacture trades.

Minimal `rs_rotation` slice:

```yaml
strategies:
  enabled:
    - rs_rotation
  configs:
    rs_rotation:
      name: rs_rotation
      type: relative_strength
      enabled: true
      params:
        pairs: ["BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD"]
        lookback_bars: 42
        timeframe: "4h"
        rebalance_interval_hours: 24
        top_n: 2
        total_allocation_pct: 5.0
        confidence_return_bps: 250.0
```

## Deterministic Commands

Use `backtest-preflight` and `backtest` directly for deterministic cached
replay. `replay-run` is useful later, but it refreshes OHLC over public Kraken
endpoints before running.

```bash
poetry run krakked backtest-preflight \
  --config <decision-soak-config.yaml> \
  --start 2025-12-01T00:00:00Z \
  --end <current-4h-tail> \
  --pair BTC/USD --pair ETH/USD --pair SOL/USD --pair ADA/USD \
  --timeframe 4h \
  --strict-data \
  --json
```

```bash
poetry run krakked backtest \
  --config <decision-soak-config.yaml> \
  --start 2025-12-01T00:00:00Z \
  --end <current-4h-tail> \
  --pair BTC/USD --pair ETH/USD --pair SOL/USD --pair ADA/USD \
  --timeframe 4h \
  --starting-cash-usd 10000 \
  --fee-bps 25 \
  --strict-data \
  --db-path reports/decision-soak-YYYY-MM-DD/replay.db \
  --save-report reports/decision-soak-YYYY-MM-DD/replay.json \
  --json
```

Replay is judged by the decision-loop pass rule, not by `trust_level` alone.
The shared replay `trust_level` remains strict for strategy-edge evaluation:
any blocked action downgrades it to `limited`. For this plumbing proof, low-ratio
guardrail blocks are acceptable and useful evidence when they are legible.

Decision-loop replay passes only when the saved report says:

- strict preflight coverage is complete: no missing or partial execution series;
- warmup is ready;
- `summary.total_actions > 0`;
- `summary.filled_orders > 0`;
- `summary.execution_errors == 0`;
- `summary.blocked_actions / summary.total_actions < 0.75`;
- every blocked action has a clear block reason in
  `summary.blocked_reason_counts` or the persisted decisions table.

`summary.trust_level == "limited"` is acceptable only when the limitation is
caused solely by low-ratio, legible guardrail blocks. It remains a stop when the
reason is incomplete coverage, zero fills, all or most actions blocked, or any
execution error.

If the replay is `weak_signal`, coverage/warmup is not ready, fills are zero,
execution errors are non-zero, or blocked actions dominate the run, do not start
the overnight paper soak. Fix data coverage, choose a supported 4h window, or
document a separate acceptance-rule change before proceeding.

## Forward Paper Soak Acceptance

Run the forward soak only after deterministic replay passes the decision-loop
pass rule above.

Required evidence:

- non-zero strategy intents/actions from the session;
- decision trace links signal reason, score/filter state, risk clamp/block
  reason, OMS order/result, paper fill, paper trade, portfolio position delta,
  and dashboard/operator diagnostics;
- no-action diagnostics for strategies that remain silent;
- stale enabled/open-position pairs block readiness, while disabled/watchlist
  pairs are warnings;
- backup/export uses the active DB path shown by the operator paths health
  surface;
- any paper emergency-flatten drill is explicitly labeled paper-only.

The soak report must include a "scope boundary" section stating that paper mode
did not exercise live account-truth gates or real Kraken reconciliation.

## 2026-06-22 rc.9 Forward Soak Finding

The corrected `v0.1.1-rc.9` forward paper soak used the intended image, commit,
profile, and isolated DB. Runtime health, portfolio sync, drift status, and
operator provenance were clean, but the forward decision chain was not observed:
no risk decisions, orders, fills, trades, or positions were produced.

The root cause was strategy-legibility, not deployment drift. `rs_rotation`
emitted two raw cold-start candidates, both with zero confidence. The strategy
engine filtered both before risk because their scores were below the score
threshold, then the 24h rebalance cadence made later closed-bar evaluations
quiet. This is a valid conservative no-trade outcome, but it exposed an
operator-truth gap: runtime surfaces need to show score-filtered candidates and
their reasons instead of collapsing them into generic no-action text.

## 2026-06-23 rc.10 Forward Soak Finding

The `v0.1.1-rc.10` forward paper soak used the intended image, commit, profile,
and isolated DB. It passed the forward paper decision-loop proof: `trend_core`
generated strategy actions, the risk engine clamped and blocked over-budget
intents with legible reasons, OMS wrote filled paper orders, paper trades and
snapshots persisted for normal limit-order fills, and the new score-filter
diagnostics made zero-confidence candidates visible before risk.

See
[`soak-reports/2026-06-23-decision-soak-rc10-forward.md`](./soak-reports/2026-06-23-decision-soak-rc10-forward.md).

The controlled paper emergency-flatten attempt after the soak failed. Filled
paper market flatten orders had no average fill price, no corresponding paper
trades were inserted, positions did not reduce, and the emergency resume path
retried until the container was stopped for containment. The follow-up rc.11
confirmation below repeated that paper-control path successfully.

## 2026-06-23 rc.11 Paper Flatten Confirmation

The `v0.1.1-rc.11` controlled paper flatten confirmation repeated the paper
runtime path on the intended image and isolated profile. It seeded BTC/ETH
paper positions through the deployed runtime, armed `emergency_flatten=true`,
recreated the container, and let the background emergency-flatten resume branch
close positions.

The confirmation passed: paper market close orders had non-null fill prices,
paper trades were inserted, synthetic BTC/ETH wallet balances went to zero, open
orders stayed empty, and emergency intent cleared.

See
[`soak-reports/2026-06-23-rc11-paper-flatten-confirmation.md`](./soak-reports/2026-06-23-rc11-paper-flatten-confirmation.md).

## Deterministic Live-Gate Harness Status

`tests/test_money_safety_order_lifecycle.py` includes a strategy-generated
`rs_rotation` fake-Kraken live-config proof. It uses real `StrategyEngine`, real
`PortfolioService`, real `ExecutionService`, and `FakeKrakenRESTClient` to prove:

- healthy account truth allows a strategy-generated opening-risk action;
- the live OMS path requests forced fresh account truth before `AddOrder`;
- a fake exchange fill reconciles through TradesHistory/Ledgers into portfolio
  state;
- a fresh Balance failure blocks the same strategy-generated opening-risk path
  before order submission.

This harness is the bridge between strategy-generated decisions and the
account-truth hardening work. It is still deterministic fake-exchange evidence,
not a live Kraken smoke.
