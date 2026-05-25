# ML Cross-Window Validation Runbook

A single walk-forward window can lie. A profile that wins on one regime may
underperform on the next. Before treating any ML experiment as a durable signal,
re-run the same configuration across at least three non-overlapping windows and
compare the results with `krakked ml-report-compare`.

The current "research baseline" is `ohlc_v5` on the 4h PA lane (see
[`ml-experiment-log.md`](./ml-experiment-log.md) for the recorded evidence). It
cleared the realistic-cost gate on one window. This runbook checks whether that
result holds when the underlying market regime changes.

## When to run cross-window validation

Any of the following triggers a cross-window run before the result is treated
as durable:

- Promoting a feature schema (e.g. `ohlc_v5` → `ohlc_v6`)
- Promoting an ablation profile (e.g. `drop_lower_wick_body`)
- Switching backends (`pa` → `sgd_huber`)
- Changing the regression epsilon, scaler version, or clipping config
- Any change to promotion thresholds in `_assess_promotability`

## Recommended windows

Pick three non-overlapping ~60-day windows that cover meaningfully different
regimes. Update dates to reflect the most recent cached OHLC history on the host
running the experiment.

| label | start | end | rationale |
| --- | --- | --- | --- |
| `early_2026` | 2025-12-01 | 2026-02-01 | Late 2025 / early 2026 regime |
| `mid_2026` | 2026-01-15 | 2026-03-15 | Transition slice |
| `recent_2026` | 2026-03-21 | 2026-05-24 | Most recent (matches existing baseline) |

If a window has insufficient OHLC coverage, `ml-walk-forward` will say so via
the `coverage_status` field. Drop or replace that window rather than running on
incomplete data.

## CLI invocations

Run the same configuration against each window, varying only `--start`,
`--end`, and the report path. Adjust pairs/timeframe to whatever the experiment
is being validated for; the example below tracks the 4h PA `ohlc_v5` baseline.

```powershell
# Window 1: early 2026
krakked ml-walk-forward `
  --strategy ai_regression `
  --timeframe 4h `
  --start 2025-12-01 --end 2026-02-01 `
  --pair BTC/USD --pair ETH/USD `
  --train-bars 180 --test-bars 42 `
  --fee-bps 10 --slippage-bps 20 `
  --db-path reports/ml/cross-window/ai-regression-early_2026.db `
  --save-report reports/ml/cross-window/ai-regression-early_2026.json `
  --strict-data

# Window 2: mid 2026
krakked ml-walk-forward `
  --strategy ai_regression `
  --timeframe 4h `
  --start 2026-01-15 --end 2026-03-15 `
  --pair BTC/USD --pair ETH/USD `
  --train-bars 180 --test-bars 42 `
  --fee-bps 10 --slippage-bps 20 `
  --db-path reports/ml/cross-window/ai-regression-mid_2026.db `
  --save-report reports/ml/cross-window/ai-regression-mid_2026.json `
  --strict-data

# Window 3: recent 2026 (matches the baseline)
krakked ml-walk-forward `
  --strategy ai_regression `
  --timeframe 4h `
  --start 2026-03-21 --end 2026-05-24 `
  --pair BTC/USD --pair ETH/USD `
  --train-bars 180 --test-bars 42 `
  --fee-bps 10 --slippage-bps 20 `
  --db-path reports/ml/cross-window/ai-regression-recent_2026.db `
  --save-report reports/ml/cross-window/ai-regression-recent_2026.json `
  --strict-data
```

To compare ablation profiles across windows, repeat the above per
`--feature-profile`. Most experiments only need to vary one variable at a time
(feature profile *or* backend *or* schema), not all simultaneously.

## Compare the runs

```powershell
krakked ml-report-compare `
  --glob "reports/ml/cross-window/ai-regression-*.json" `
  --sort precision-long
```

The output table includes `tier` (the highest promotion tier the run cleared),
`precision_long`, `base_hit`, `p95_lift`, `selected_avg_ret`, `upper_half`, and
the per-fold diagnostic warnings. The `tier` column is the headline indicator:

- `blocked` — research-promising failed; experiment is not viable
- `research_promising` — research-promising cleared; risk-overlay failed
- `risk_overlay_candidate` — viable when paired with the risk overlay
- `self_standing` — model carries the strategy on its own

Read the table sorted by `precision_long` and by `p95_lift` (re-run with
`--sort p95-lift`). A genuinely durable result wins consistently on at least
one of those orderings.

## Cross-window pass criteria

Treat an experiment as cross-window validated when **all** of the following
hold across the three windows:

1. The same `promotion_tier` (or better) is reached in at least **2 of 3**
   windows
2. No window collapses below `research_promising` (i.e. no `blocked` runs)
3. `upper_half_monotonicity` is `true` or `insufficient_data` in 2 of 3
   windows — `false` in more than one window is a hard fail
4. The `diagnostic_warnings` column is empty or contains only
   `"insufficient_data"` style messages — recurring health warnings across
   windows indicate a structural issue, not a regime artifact

If only one window clears the tier, the result is window-specific noise.
Document the negative cross-window result in
[`ml-experiment-log.md`](./ml-experiment-log.md) and do not treat the
single-window evidence as a promotion candidate.

## What to record

After each cross-window pass, append to `ml-experiment-log.md`:

- Configuration (strategy, backend, schema, profile, costs)
- The three window labels and date ranges
- A small table of `tier`, `precision_long`, `p95_lift`, and
  `upper_half_monotonicity` per window
- Whether the cross-window criteria above were cleared
- The next action: promote (and to which operational scope), iterate, or
  abandon

Generated JSON reports and per-fold SQLite databases stay under
`reports/ml/cross-window/` and are gitignored. The evidence log is the durable
artifact.

## Why not just trust the strictest single-window result

Linear models on simple OHLC features overfit quickly to the regime they're
trained on. A profile that wins one window can lose the next because:

- The training window happened to capture a low-volatility regime
- The test window happened to contain rare large moves the model never saw
- ATR-normalization caps used during a quiet period don't transfer to a volatile
  one
- Time-of-day features fit weekend/weekday patterns specific to that window

Cross-window validation is the cheapest available check against this failure
mode. It catches what the existing in-window walk-forward cannot.
