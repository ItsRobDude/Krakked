# Offline Simulation And Backtesting

Krakked now has a real offline replay seam:

```bash
poetry run krakked backtest-preflight \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z

poetry run krakked backtest \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z \
  --starting-cash-usd 10000 \
  --save-report backtest-report.json \
  --publish-latest
```

What it reuses:

- The existing strategy engine
- The existing risk engine
- The existing order router and OMS execution path
- The existing SQLite decision / execution persistence when `--db-path` is provided

What it reads:

- Stored OHLC parquet files from the configured `market_data.ohlc_store.root_dir`
- Cached pair metadata from `pair_metadata.json` / `market_data.metadata_path` when available

Current assumptions and limits:

- The replay starts from a synthetic USD-only wallet. The bankroll is explicit via `--starting-cash-usd`.
- It uses cached OHLC bars only. It does not fetch Kraken REST or WebSocket data during the run.
- By default, the replay exposes cached pre-window OHLC for indicator, regime, and risk warmup, while only generating decision cycles inside the requested `--start` / `--end` window.
- Fills are immediate and priced from the latest available candle close, then adjusted by `execution.max_slippage_bps`.
- A flat taker fee is applied per fill via `--fee-bps` and defaults to `25`.
- It does not model order book depth, spread dynamics, latency queueing, partial fills, liquidation logic, or exchange rejects beyond the repo's local guardrails.
- Missing or partial pair/timeframe series are reported by a preflight pass. Warmup coverage is reported separately from execution-window coverage. The run continues if at least one requested execution-window series has usable data unless `--strict-data` is enabled.
- This is a learning / strategy-validation seam, not a brokerage-accurate fills simulator.

Useful flags:

- `backtest-preflight`: check pair/timeframe coverage before you spend time on a full replay
- `--config <path>`: use a specific base `config.yaml`
- `--pair BTC/USD --pair ETH/USD`: clamp the replay to specific pairs
- `--timeframe 1h --timeframe 4h`: clamp the replay to specific stored timeframes
- `--fee-bps 25`: apply a flat taker fee assumption to simulated fills
- `--warmup-days <days>`: override the automatic pre-window warmup length; use `--warmup-days 0` for the old exact-window behavior
- `--strict-data`: fail if any requested execution-window or warmup pair/timeframe is missing or only partially covered
- `--save-report backtest-report.json`: save one durable JSON report with coverage, PnL, drawdown, and per-strategy totals
- `--publish-latest`: publish the validated replay summary to the canonical operator path at `<config_dir>/reports/backtests/latest.json`
- `--db-path backtest.db`: keep the SQLite decisions/orders/results for inspection after the run
- `--json`: print the replay summary as JSON

Action-quality diagnostics:

```bash
poetry run krakked strategy-action-diagnostics \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --strategy trend_core \
  --strict-data \
  --save-report action-diagnostics.json
```

This is research-only. It reruns one cache-only replay and reports action stages, normalized guardrail reason buckets, per-pair activity, filled-order tape rows, and approximate fill-level realized PnL. It does not change runtime config, strategy defaults, risk behavior, or order routing.

Unified strategy evidence scoreboard:

```bash
poetry run krakked strategy-evidence-scoreboard \
  --window-set recent_20d \
  --window-set long_4h \
  --fee-bps 30 \
  --strict-data \
  --save-dir strategy-evidence-scoreboard
```

This is research-only. It runs configured packs and individual configured
strategies, including disabled research/ML strategies, through the same cached
runtime replay path and writes one aggregate scoreboard. Use it when comparing
ML, starter strategies, cash, and equal-weight buy-and-hold under one replay
context instead of stitching separate research reports together. It does not
change runtime config, strategy defaults, risk behavior, order routing, or
paper/live execution.

Current evidence correction: the ML walk-forward result is not an
apples-to-apples project verdict by itself. Future ML work should be judged
against this unified scoreboard plus the simple hand-coded top-2
`trend_rank_proxy` soft `target_scale` overlay documented in
[`regime-diverse-evidence-plan.md`](./regime-diverse-evidence-plan.md). The next
scoreboard extension should add regime-diverse window labels and risk-adjusted
metrics before any broad strategy or ML verdict is made.

Minimal ML regime-overlay research:

```bash
poetry run krakked ml-regime-overlay-research \
  --window-set regime_diverse_4h \
  --allocation-pct 20 \
  --timeframe 4h \
  --strict-data \
  --save-dir ml-regime-overlay-research
```

This is research-only. It trains a small online classifier on prior-window
rebalance examples and asks whether ML can choose exposure scale
(`0.25`, `0.75`, or `1.0`) better than the hand-coded top-2 soft
`target_scale` baseline. Reports remain `runtime_wiring_approved=false`.

The report now also records each window's computed `market_bucket` (uptrend,
downtrend, chop_or_transition, or current_rolling) from benchmark/basket returns,
and the promotion gate includes `regime_coverage_sufficient`: a window set that
does not span uptrend, downtrend, and chop fails as `insufficient_regime_coverage`
rather than trusting the set name. The `regime_diverse_4h` set does span those
regimes on the current cached data, so always read the computed buckets rather
than assuming a set is or is not diverse from its name.

trend_core signal-quality research:

```bash
poetry run krakked trend-core-signal-quality \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --fresh-bars-only \
  --strict-data \
  --save-report trend-core-signal-quality.json
```

This is research-only. It evaluates cached `trend_core` long entry/increase signals against forward returns at one or more bar horizons and reports whether the raw signal clears a fee-aware quality gate. Use `--fresh-bars-only` when comparing against the runtime/replay engine behavior that evaluates each strategy timeframe only once per new closed bar; omit it only when intentionally measuring the old stale-context behavior.

Target-source research:

```bash
poetry run krakked target-source-research \
  --window-set recent_20d \
  --window-set long_4h \
  --scenario rank_top2 \
  --scenario dual_momentum_top2 \
  --scenario vol_adj_dual_momentum_top2 \
  --scenario pullback_vol_adj_top2 \
  --scenario oversold_reversion_top1 \
  --scenario hybrid_state_source \
  --allocation-pct 20 \
  --timeframe 4h \
  --rebalance-interval-bars 6 \
  --fee-bps 25 \
  --strict-data \
  --save-dir target-source-research
```

This is research-only. It evaluates explicit dynamic target-weight adapters
against cached `4h` starter-universe OHLC and writes one report per
window/scenario/allocation plus `aggregate.json`. Each run report includes
`rebalance_trace` rows with the rebalance timestamp, selected pairs, per-pair
scores/features, target weights, equity/exposure before and after rebalance,
fees, forward returns to the next rebalance, and whether the source targeted
cash. Each run also includes a `diagnostics` block that buckets likely failure
modes: wrong asset selection, late/chasing entries, slow exits, sparse exposure,
fee/churn drag, and pair-level edge hidden inside bad allocation rules.

It does not change runtime config, strategy defaults, risk behavior, order
routing, paper/live execution, or operator UI behavior. `rank_top2` is the
comparison baseline; no source is a runtime candidate unless the aggregate gate
beats that baseline on return and drawdown across the requested window sets
while preserving adequate exposure and strict data coverage.

Pair-local source research:

```bash
poetry run krakked pair-local-source-research \
  --window-set recent_20d \
  --window-set long_4h \
  --allocation-pct 20 \
  --timeframe 4h \
  --rebalance-interval-bars 6 \
  --fee-bps 25 \
  --strict-data \
  --save-dir pair-local-source-research
```

This is the current source-edge comparison gate before any runtime strategy wiring.
It evaluates each starter pair independently against cached `4h` OHLC using
pair-local setup/exit rules. The aggregate reports
`promote_pair_local_source` and `runtime_wiring_approved`; runtime wiring is not
eligible unless a pair/scenario passes both recent and long window-set gates at
the primary 20 percent allocation.

Saved report highlights:

- requested window, pairs, timeframes, and bankroll
- simple replay trust fields for a default operator view: `trust_level`, `trust_note`, and `notable_warnings`
- ending equity, return, realized/unrealized PnL, and max drawdown
- total actions, blocked actions, clamped actions, orders, fills, rejects, and execution errors
- grouped blocked-action and clamped-action reason counts for deeper debugging
- full data coverage results for every requested pair/timeframe
- per-strategy realized PnL summary
- the exact replay assumptions used, including slippage and fee settings
- small provenance fields such as app version, config path, and enabled strategies

Report-to-report comparison:

```bash
poetry run krakked compare-backtests \
  --baseline runs/baseline.json \
  --candidate runs/candidate.json
```

This compares two saved reports without rerunning simulations and prints the deltas that matter: ending equity, return, drawdown, fills, blocked actions, clamped actions, execution errors, and overlapping per-strategy realized PnL.

Suggested workflow:

1. Run `krakked backtest-preflight` for the date window you care about.
2. Backfill or collect OHLC if the preflight shows missing or partial coverage you do not want.
3. Run `krakked backtest` over the bounded window.
4. Start with the simple replay trust note and important warnings before looking at deeper details.
5. Save a JSON report when you want a durable artifact or an A/B comparison point.
6. If needed, rerun with `--db-path` and inspect the stored decisions and orders.

## Replay Smoke Scenarios

### 1. Sanity replay

Use this when you want to prove the offline seam still runs end to end and publish one operator-facing summary:

```bash
poetry run krakked backtest-preflight \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z

poetry run krakked backtest \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z \
  --starting-cash-usd 10000 \
  --publish-latest
```

Healthy enough:

- preflight reports at least one usable series
- replay completes without execution errors
- trust is not empty and the operator panel can read the published report

Weak or untrustworthy:

- preflight is fully missing or strict-data fails
- replay shows `weak_signal` because there were no actions, no orders, or no fills
- execution errors occur during the run

### 2. Cost-check replay

Use this when you want a lightweight honesty check that costs reduce reported outcome:

```bash
poetry run krakked backtest \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z \
  --starting-cash-usd 10000 \
  --fee-bps 0 \
  --save-report runs/zero-cost.json

poetry run krakked backtest \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z \
  --starting-cash-usd 10000 \
  --fee-bps 25 \
  --save-report runs/with-costs.json

poetry run krakked compare-backtests \
  --baseline runs/zero-cost.json \
  --candidate runs/with-costs.json
```

Healthy enough:

- the higher-cost candidate ends with lower or equal equity than the zero-cost baseline
- compare output shows the expected deltas for return, fills, blocked actions, and execution errors

Weak or untrustworthy:

- both runs have zero fills, so the cost model did not get exercised
- missing or partial data dominates the replay window
