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
- Fills are immediate and priced from the latest available candle close, then adjusted by `execution.max_slippage_bps`.
- A flat taker fee is applied per fill via `--fee-bps` and defaults to `25`.
- It does not model order book depth, spread dynamics, latency queueing, partial fills, liquidation logic, or exchange rejects beyond the repo's local guardrails.
- Missing or partial pair/timeframe series are reported by a preflight pass. The run continues if at least one requested series has usable data unless `--strict-data` is enabled.
- This is a learning / strategy-validation seam, not a brokerage-accurate fills simulator.

Useful flags:

- `backtest-preflight`: check pair/timeframe coverage before you spend time on a full replay
- `--config <path>`: use a specific base `config.yaml`
- `--pair BTC/USD --pair ETH/USD`: clamp the replay to specific pairs
- `--timeframe 1h --timeframe 4h`: clamp the replay to specific stored timeframes
- `--fee-bps 25`: apply a flat taker fee assumption to simulated fills
- `--strict-data`: fail if any requested pair/timeframe is missing or only partially covered
- `--save-report backtest-report.json`: save one durable JSON report with coverage, PnL, drawdown, and per-strategy totals
- `--publish-latest`: publish the validated replay summary to the canonical operator path at `<config_dir>/reports/backtests/latest.json`
- `--db-path backtest.db`: keep the SQLite decisions/orders/results for inspection after the run
- `--json`: print the replay summary as JSON

Saved report highlights:

- requested window, pairs, timeframes, and bankroll
- simple replay trust fields for a default operator view: `trust_level`, `trust_note`, and `notable_warnings`
- ending equity, return, realized/unrealized PnL, and max drawdown
- total actions, blocked actions, orders, fills, rejects, and execution errors
- grouped blocked-action reason counts for deeper debugging
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

This compares two saved reports without rerunning simulations and prints the deltas that matter: ending equity, return, drawdown, fills, blocked actions, execution errors, and overlapping per-strategy realized PnL.

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
