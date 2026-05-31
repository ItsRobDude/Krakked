# Regime-Diverse Evidence And ML Overlay Plan

Date: 2026-05-31

## Correction

The recent evidence work should not be read as "ML is out" or as a permanent
claim that Krakked cannot use machine learning. The corrected read is narrower:

- The `ohlc_v5` ML walk-forward pass is useful ML diagnostics, not an
  apples-to-apples project verdict.
- The unified strategy evidence scoreboard shows ML is one unproven row among
  several unproven or inactive strategy rows under the same replay context.
- The current scoreboard window mix is negative-biased: cash beats the active
  strategy rows and equal-weight buy-and-hold is deeply negative across the
  recorded windows.
- The strongest surviving positive result is the hand-coded market-state
  `target_scale` overlay on the dense top-2 `trend_rank_proxy` research target.
  That is a baseline to beat, not runtime approval.
- Rich meta-labeling over actual strategy events should wait until the event
  stream is dense enough. A veto model on sparse, weak strategy events can only
  learn to trade less.

The next ML hypothesis is therefore not "predict every winning trade from raw
OHLC." It is:

> Can a minimal ML market-state model learn a better exposure scale than the
> simple hand-coded `target_scale` rule, under the same regime-diverse evidence
> set and cost assumptions?

## Implementation Research

### 1. Define a regime-diverse evidence set

Before this pass, the code had two overlapping window-set definitions:

- `STRATEGY_ACTIVITY_WINDOW_SETS` in `src/krakked/backtest/strategy_activity.py`
- `MARKET_REGIME_EXPOSURE_WINDOW_SETS` in `src/krakked/cli.py`

This is now centralized in `src/krakked/backtest/evidence_windows.py` so runtime
strategy scoreboards, controlled exposure sweeps, and ML overlay research use
the same window catalog.

Proposed shape:

- `EvidenceWindow` defines `window_set`, `window_id`, `start`, `end`, and an
  optional label.
- Existing `recent_20d` and `long_4h` sets now live in that module.
- `regime_diverse_4h` is available as a shared candidate set. Its market bucket
  is computed from cached data at report time, not hard-coded from intuition.
- Per-window market context is computed from cached `4h` OHLC:
  `benchmark_return_pct`, `basket_return_pct`, `benchmark_max_drawdown_pct`,
  `basket_volatility_pct`, market-regime state counts, and dominant reason
  codes.
- Aggregate reports can group windows by computed regime bucket:
  `uptrend`, `downtrend`, `chop_or_transition`, and `current_rolling`.

Do not hard-promote a regime label until strict cached data passes for that
window.

### 2. Extend the unified scoreboard with risk-adjusted metrics

`build_strategy_evidence_scoreboard` already compares strategy rows, cash, and
equal-weight buy-and-hold. The next pass should extend the same report instead
of adding another standalone scoreboard.

Implemented per-row fields:

- `avg_return_over_drawdown_ready`
- `current_return_over_drawdown`
- `positive_ready_window_rate`
- `filled_window_rate`
- `avg_actions_per_ready_window`
- `avg_fills_per_ready_window`
- `turnover_proxy`: filled notional divided by starting cash when order fill
  notional is available, otherwise `null`
- `regime_breakdown`: row metrics grouped by computed regime bucket

Implemented aggregate/baseline fields:

- cash baseline remains `0.0%` return and `0.0%` drawdown
- buy-hold baseline keeps return and drawdown, and adds the same
  return/drawdown ratio
- `negative_benchmark_context=true` when buy-hold is negative across most
  windows, so future decisions do not mistake "lost less than buy-hold" for
  proven edge

The first implementation uses existing replay summary fields plus filled order
metadata already present in the offline result object. Runtime behavior is not
changed.

### 3. Promote the simple top-2 `target_scale` rule to comparison baseline

The top-2 `trend_rank_proxy` soft-scale result belongs in the evidence system as
a baseline, not as a runtime strategy.

Implemented path:

- Keep the existing controlled exposure engine in
  `src/krakked/backtest/market_regime_exposure.py`.
- Add an explicit baseline profile named `top2_soft_target_scale` that expands
  to:
  - scenario `trend_rank_proxy`
  - overlay mode `target_scale`
  - `max_target_pairs=2`
  - `neutral_allocation_multiplier=0.75`
  - `risk_off_allocation_multiplier=0.25`
  - allocation `5%` and `20%`
  - `4h`, 63-bar target lookback, 6-bar rebalance interval, 25 bps fee
- Have the aggregate report state clearly that this row is
  `controlled_exposure_proxy`, not `runtime_replay`.
- Future ML overlay research must beat this baseline on average return,
  drawdown, return/drawdown ratio, current rolling window behavior, and exposure
  adequacy before any runtime plan is written.

### 4. Add a minimal ML exposure-scale experiment

Implemented as the research-only `krakked ml-regime-overlay-research` command.
It does not start with a full strategy meta-labeler. It starts with a small,
falsifiable overlay model.

Proposed command:

```bash
poetry run krakked ml-regime-overlay-research \
  --window-set regime_diverse_4h \
  --baseline top2_soft_target_scale \
  --allocation-pct 20 \
  --timeframe 4h \
  --strict-data \
  --save-dir reports/ml-regime-overlay-research
```

Model target:

- Predict the next rebalance exposure scale for the top-2
  `trend_rank_proxy` target source.
- Supported output starts as one of `0.25`, `0.75`, or `1.0`.
- Labels should be generated from next-rebalance realized performance after
  costs, with a drawdown penalty so the model is rewarded for risk-adjusted
  exposure, not raw upside only.

Feature set:

- Benchmark momentum, drawdown, and volatility from the existing market-regime
  classifier.
- Basket momentum and volatility.
- Current top-2 target momentum spread and concentration.
- Prior realized regime bucket and prior overlay state.

Training/evaluation shape:

- Use `sklearn` already present in the project; no dependencies were added.
- Reuse the existing `PassiveAggressiveClassifier`, `StandardScaler`, and
  `MLOnlineModelBundle` helpers from `src/krakked/strategy/ml_models.py` so the
  research path follows the current ML checkpoint/scaler conventions.
- Train only on windows before the test window.
- Compare three rows per test window:
  `no_overlay`, `handcoded_top2_soft_target_scale`, and `ml_scale_overlay`.
- Keep the model intentionally small first: an online linear classifier over
  the three scale classes is enough for the first pass.
- The classifier seed is fixed in the research command so repeated runs are
  comparable.
- Refuse the report if strict data fails or if there are too few labeled
  rebalance examples.

Promotion gate for the ML overlay:

- Beats `top2_soft_target_scale` on average return and average max drawdown.
- Beats it on return/drawdown ratio in at least a majority of regime buckets.
- Does not collapse to cash-only behavior.
- Average exposure remains at least 35% of the hand-coded
  `top2_soft_target_scale` baseline exposure unless the report explicitly labels
  the model defensive-only.
- Current rolling window is not worse than the hand-coded baseline.
- Report states `research_only=true` and `runtime_wiring_approved=false`.

Passing this gate still does not enable runtime behavior. It only earns a
separate runtime-controls plan.

## First Run

The first strict `regime_diverse_4h` run on `2026-05-31` did not pass:

- ML-ready windows: `5 / 6`
- average ML return delta versus hand-coded top-2 soft `target_scale`: `-0.2356%`
- positive return-delta windows: `3 / 5`
- average max-drawdown delta: `+0.1867%`
- drawdown-improved windows: `4 / 5`
- average ML exposure: `11.46%` versus hand-coded baseline `12.41%`
- required minimum ML exposure for the non-cash gate: `4.34%`
- `promotion_gate.passed=false`

This validates the harness but not the model. See the regime-coverage
instrumentation follow-up below before drawing a verdict: the useful next step is
**not** per-window ML feature diagnosis on this source.

## Regime-Coverage Instrumentation And Composition Correction (2026-05-31)

The first-run write-up above (and an external review) initially read the
`regime_diverse_4h` set as "downtrend/chop only, no uptrend," and treated missing
regime diversity / prior-cycle data as the binding blocker. The ML overlay report
did not surface the per-window regime, so that read was an inference from the
`trend_rank_proxy` *strategy* return rather than the *market* return.

Follow-up instrumentation closed that gap:

- The ML overlay report now wires in `build_evidence_window_context`, so every
  window carries `market_bucket`, `evidence_bucket`, and benchmark/basket
  returns computed from cached OHLC (not the strategy's own return).
- `summarize_regime_coverage` was added and is enforced in the promotion gate as
  `regime_coverage_sufficient`. A set that does not span uptrend, downtrend, and
  chop/transition among its evaluable windows now fails explicitly as
  `insufficient_regime_coverage` instead of trusting the window-set name.

Verified `regime_diverse_4h` composition (benchmark BTC / 4-pair basket return):

| window | bucket | benchmark return | basket return | benchmark max DD |
| --- | --- | ---: | ---: | ---: |
| `20251221-20260120` | uptrend | `+4.93%` | `+4.60%` | `5.01%` |
| `20260120-20260219` | downtrend | `-27.52%` | `-32.39%` | `31.95%` |
| `20260219-20260321` | uptrend | `+5.54%` | `+5.52%` | `10.42%` |
| `20260321-20260420` | chop_or_transition | `+5.67%` | `-0.51%` | `7.97%` |
| `20260420-20260520` | chop_or_transition | `+2.66%` | `-1.24%` | `7.47%` |
| `20260510-20260530` | downtrend (current_rolling) | `-9.08%` | `-11.68%` | `11.40%` |

Corrected conclusions:

- The set is genuinely regime-diverse (2 uptrend, 1 downtrend, 2 chop, plus the
  current rolling window). `regime_coverage_sufficient=true`. The earlier
  "no uptrend / data is the blocker" framing was wrong; prior-cycle data
  acquisition is **not** a prerequisite for a meaningful overlay verdict.
- The ML overlay's `promotion_gate.passed=false` is therefore a legitimate
  negative across regimes: it failed `beats_handcoded_return` (avg `-0.2356%`)
  and `beats_handcoded_drawdown` (avg `+0.1867%`), not regime coverage.
- A separate, sharper finding: the `trend_rank_proxy` source under-captures
  upside even in up-regimes. In both uptrend windows the market rose ~5% while
  the unscaled source return was roughly flat-to-negative after fees/timing
  (e.g. `20251221-20260120`: basket `+4.60%`, source `no_overlay` `-1.60%`).
  That is a source-quality problem, not a regime-coverage problem.

Useful next steps:

- Do not iterate ML overlay features/decisions on the current `trend_rank_proxy`
  source; a defensive rule already beats it across regimes.
- The prior-cycle data spike is deprioritized (data already spans regimes). The
  Kraken REST 720-candle ceiling still applies to any future deeper-history
  work, which would need imported/aggregated external history rather than
  `refresh-ohlc`.
- The honest product direction remains risk/observability/execution-safety, with
  bundled strategy sources treated as investigation-grade.

## Explicit Non-Goals

- Do not remove ML.
- Do not promote any strategy or overlay.
- Do not change paper/live execution behavior.
- Do not add a rich strategy meta-labeler until actual strategy candidate events
  are dense enough for a meaningful model.
- Do not treat the controlled exposure proxy as proof of production strategy
  edge.
