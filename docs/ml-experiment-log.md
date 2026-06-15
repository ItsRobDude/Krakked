# ML Experiment Log

This log captures the durable conclusions from ignored `reports/ml/` evidence.
Generated walk-forward JSON reports and fold databases stay local and untracked.

## 2026-05-24: `ohlc_v3` PA Regression Baseline

Configuration:

- Strategy: `ai_regression`
- Backend: Passive-Aggressive regressor
- Feature schema: `ohlc_v3`
- Primary lane: `4h`, BTC/USD and ETH/USD
- Window: 2026-03-21 through 2026-05-24
- Costs: 10 bps fee, 20 bps slippage

Observed primary-lane summary:

- Positive-edge predictions: `126`
- Long precision: `19.05%`
- Base realized hit rate: `17.86%`
- p90/p95 lift: `0.988x`
- p95 selected average realized return: negative
- Upper-half predicted-delta realized return improved over lower half: yes
- Feature-health warnings remained present across folds

The `1h` PA comparison lane remained weak and is not a promotion candidate.

Decision:

- Do not promote a model or expose UI controls.
- Treat `ohlc_v3` as useful evidence infrastructure, not a validated trading model.
- Next experiment: `ohlc_v4`, clipping heavy-tailed normalized features before scaler/model input.

## 2026-05-24: `ohlc_v4` Clipped Feature Baseline

Configuration matched the `ohlc_v3` primary lane:

- Strategy: `ai_regression`
- Backend: Passive-Aggressive regressor
- Feature schema: `ohlc_v4`
- Primary lane: `4h`, BTC/USD and ETH/USD
- Window: 2026-03-21 through 2026-05-24
- Costs: 10 bps fee, 20 bps slippage

Observed summary:

- Positive-edge predictions: `126`
- Long precision: `19.05%`
- Base realized hit rate: `17.86%`
- p90/p95 lift: `0.988x`
- p95 selected average realized return: negative
- Upper-half predicted-delta realized return improved over lower half: yes
- Fold-level scaled feature-health warnings remained present in all folds
- Highest clipping rates were `return_zscore` at `3.57%` in fold 4 and `2.38%` in fold 3
- No feature exceeded the `5%` clipped-rate research gate

Comparison against `ohlc_v3`:

- Aggregate scoring metrics were unchanged.
- Clipping did not reduce the scaled feature-health warning footprint enough to change the model readout.
- Promotion remains blocked because p90/p95 lift stayed below `1.3x`, selected average realized return remained negative, and one fold remained non-monotonic.

Decision:

- Do not promote a model or expose UI controls.
- Keep `ohlc_v4` as robustness infrastructure because clipping is bounded and auditable, but do not treat it as a performance win.
- Next research should use the contribution and feature-health diagnostics to trim or replace weak/tail-heavy features before adding another model family.

## 2026-05-24: `ohlc_v4` Feature Ablation Summary

Tool:

- `krakked ml-feature-ablation-summary`
- Input: `reports/ml/ai-regression-4h-v6-realistic-pa-ohlc-v4.json`
- Local ignored output: `reports/ml/pa-ohlc-v4-feature-ablation-summary.md`

Readout:

- Drop candidates: `body_atr`, `pct_change`
- Review candidates with low/unstable contribution: `return_atr_3`, `volume_change`, `hour_cos`, `upper_wick_atr`
- Keep but health-risk candidates: `return_zscore`, `range_atr`, `volatility`, `return_atr_1`
- Keep candidates by the current mechanical rules: `volatility_ratio`, `hour_sin`
- Stronger contributors still need review because fold-to-fold coefficient signs are unstable: `weekday_sin`, `trend_diff`, `weekday_cos`, `volume_log_ratio`

Decision:

- Do not declare an ablation result from this summary alone.
- Use these rankings to define a small `ohlc_v5` trimmed-feature experiment rather than adding more OHLC-derived features.

## 2026-05-24: `ohlc_v4` Controlled Ablation Matrix

Tooling:

- Added `--feature-profile` to `krakked ml-walk-forward` for controlled, keyed feature-subset experiments.
- Default profile `all` preserves the existing `features_ohlc_v4` model key.
- Non-default profiles are encoded in model keys and report comparison output, for example `features_ohlc_v4_profile_drop_lower_wick_body`.

Matrix:

- Baseline: all `ohlc_v4` features.
- `drop_weakest`: removes `pct_change`, `body_atr`, `return_atr_3`, and `volatility_ratio`.
- `volume_change_only`: removes `volume_log_ratio`.
- `volume_log_ratio_only`: removes `volume_change`.
- `drop_lower_wick`: removes `lower_wick_atr`.
- `drop_lower_wick_body`: removes `lower_wick_atr` and `body_atr`.
- `drop_time`: removes `hour_sin`, `hour_cos`, `weekday_sin`, and `weekday_cos`.

Primary comparison:

| profile | positive calls | long precision | base hit rate | p90 lift | p95 lift | selected avg return | readout |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `drop_lower_wick_body` | 124 | 23.39% | 17.86% | 1.153x | 1.318x | 0.0602% | strongest research candidate |
| `drop_lower_wick` | 119 | 21.85% | 17.86% | 1.153x | 1.318x | 0.0255% | useful supporting signal |
| `drop_time` | 90 | 21.11% | 17.86% | 0.659x | 0.988x | 0.1439% | better precision/return, weak tail lift |
| `volume_change_only` | 125 | 19.20% | 17.86% | 1.318x | 1.318x | 0.0894% | lift improved, precision barely moved |
| `all` | 126 | 19.05% | 17.86% | 0.988x | 0.988x | -0.0454% | clipped baseline |
| `drop_weakest` | 118 | 18.64% | 17.86% | 1.318x | 0.988x | -0.0048% | did not help |
| `volume_log_ratio_only` | 122 | 16.39% | 17.86% | 0.988x | 0.988x | -0.0322% | worse than baseline |

Fold checks:

- `drop_lower_wick_body` produced positive-edge calls in every fold: `8`, `59`, `25`, `32`.
- The highest clipping rate remained `return_zscore` at `3.57%`, below the `5%` research gate.
- Scaled feature-health warnings still appeared in every fold.
- Predicted-delta monotonicity still failed in fold 1 for the strongest profiles.

Decision:

- Do not promote a model or expose UI controls.
- Treat `drop_lower_wick_body` as the best next research seed because it improved long precision, p95 lift, and selected average return without increasing clipping risk.
- Treat `lower_wick_atr` and `body_atr` as the first removal candidates for a formal `ohlc_v5` schema.
- Do not remove all weak-summary features mechanically: `drop_weakest` was worse than targeted wick/body removal.
- Keep both volume features out of the next removal batch until the duplicate-volume behavior is better isolated; `volume_log_ratio_only` underperformed while `volume_change_only` mostly improved lift rather than precision.

## 2026-05-24: `ohlc_v5` Trimmed Feature Baseline

Change:

- Bumped the active ML feature schema from `ohlc_v4` to `ohlc_v5`.
- Removed `body_atr` and `lower_wick_atr` from the default shared ML feature vector.
- Kept clipping, scaling, diagnostics, and the Passive-Aggressive regression backend unchanged.
- Kept the `--feature-profile` experiment hook for future controlled subsets; default `all` now maps to the trimmed `ohlc_v5` feature set.

Configuration matched the `ohlc_v4` primary lane:

- Strategy: `ai_regression`
- Backend: Passive-Aggressive regressor
- Feature schema: `ohlc_v5`
- Primary lane: `4h`, BTC/USD and ETH/USD
- Window: 2026-03-21 through 2026-05-24
- Costs: 10 bps fee, 20 bps slippage

Observed summary:

- Positive-edge predictions: `124`
- Long precision: `23.39%`
- Base realized hit rate: `17.86%`
- Edge prediction accuracy: `62.50%`
- p90/p95 lift: `1.153x` / `1.318x`
- p95 selected average realized return: `0.0602%`
- Upper-half predicted-delta realized return improved over lower half: yes
- Positive-edge calls appeared in every fold: `8`, `59`, `25`, `32`
- Highest clipping rate remained `return_zscore` at `3.57%`, below the `5%` research gate

Comparison:

- `ohlc_v5` exactly matched the prior `ohlc_v4/drop_lower_wick_body` profile run.
- `ohlc_v5` improved over the clipped `ohlc_v4` baseline on long precision, p90/p95 lift, and selected average realized return.
- Promotion remains blocked because long precision is still below the current 50% promotion threshold, scaled feature-health warnings remain present in every fold, and fold 1 remains non-monotonic.

Decision:

- Do not promote a model or expose UI controls.
- Treat `ohlc_v5` as the current cleaned research baseline for 4h PA regression.
- Next useful research should address the remaining feature-health warnings and fold-1 monotonicity, rather than adding back candle-body or lower-wick features.

## 2026-05-31: `ohlc_v5` Cost-Semantics and Baseline Proof Pass

Change:

- Bumped ML walk-forward reports to version 8.
- Added explicit report semantics for model family, strategy type, training
  target, prediction target, feature schema/profile, cost multipliers,
  evaluation-hurdle source, and evaluation-hurdle values.
- Added fold and aggregate baselines for cash, pair-level buy-and-hold, and
  equal-weight buy-and-hold. These are research context only, not runtime
  approval.

Configuration:

- Strategy: `ai_regression`
- Backend: Passive-Aggressive regressor
- Feature schema: `ohlc_v5`
- Timeframe: `4h`
- Pairs: BTC/USD and ETH/USD
- Train/test bars: `180` / `42`
- Costs: 10 bps fee, 20 bps slippage
- Strict cached data only after explicit `refresh-ohlc` maintenance for BTC/USD
  and ETH/USD `4h`

Artifacts:

- `reports/ml/ml-baseline-proof-20260531/ai-regression-mid_2026.json`
- `reports/ml/ml-baseline-proof-20260531/ai-regression-recent_2026.json`

Strict-data note:

- Exact `early_2026` (`2025-12-01 -> 2026-01-31`) could not be rerun with
  strict data. After the allowed targeted refresh, Kraken OHLC returned the
  latest 721 `4h` bars, leaving the local BTC/ETH `4h` cache starting around
  `2025-12-20`. The exact early window therefore remained partial and is a
  strict-data miss for the promotion gate.

Observed strict-window summary:

| window | tier | predictions | positive calls | long precision | base hit | p95 lift | p95 selected avg return | upper half | equal-weight buy-hold avg |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `mid_2026` | `blocked` | 336 | 162 | 24.07% | 24.70% | 1.191x | 0.0945% | false | -0.5451% |
| `recent_2026` | `research_promising` | 252 | 88 | 14.77% | 15.87% | 0.000x | -0.2936% | false | -1.3141% |

Decision:

- Keep ML away from runtime strategy, risk overlay, and operator controls until a
  shared evidence gate clears.
- Keep ML in Krakked as active research infrastructure and checkpoint/report
  plumbing.
- The result does not clear the current ML promotion gate: one required strict window
  missed data coverage, no window reached `risk_overlay_candidate`, monotonicity
  did not hold in both runnable windows, and recent p95 selected return was negative.
- ML remains in scope; it is currently unpromoted research infrastructure.
  Further ML work should either start from a genuinely new written hypothesis or
  improve data retention enough to make the intended strict windows reproducible.
- Cross-strategy judgment should use the unified runtime scoreboard in
  [`replay-experiment-log.md`](./replay-experiment-log.md#2026-05-31-unified-strategy-evidence-scoreboard);
  the ML walk-forward report alone is not an apples-to-apples strategy verdict.

## 2026-05-31: Evidence Framing Correction

Correction:

- Do not describe the ML lane as closed or removed. The `ohlc_v5` proof pass
  failed its current promotion gate, but it was a model diagnostic, not a final
  project verdict.
- The unified runtime scoreboard showed ML as one unproven strategy row among
  several unproven or inactive rows. That is materially different from saying
  ML is uniquely invalid.
- The current scoreboard window mix is negative-biased: cash beat the active
  strategy rows and equal-weight buy-and-hold was deeply negative. Future
  evidence should include explicit regime-diverse windows before making broad
  strategy or ML claims.
- The next ML hypothesis should be risk/exposure scaling first. Rich
  meta-labeling on sparse strategy events is deferred until the event stream is
  dense enough to support it.

Next implementation research lives in
[`regime-diverse-evidence-plan.md`](./regime-diverse-evidence-plan.md). The
baseline ML must beat is the simple hand-coded top-2 `trend_rank_proxy`
`target_scale` overlay, not cash alone and not an ML-only walk-forward table.

Implemented follow-up:

- Added shared evidence-window plumbing and a `regime_diverse_4h` window set.
- Extended the unified strategy scoreboard with risk-adjusted metrics, computed
  regime context, negative benchmark context, and the controlled top-2 soft
  `target_scale` baseline.
- Added `krakked ml-regime-overlay-research` as the first minimal ML
  exposure-scale command. It is research-only and leaves
  `runtime_wiring_approved=false`.
- The research classifier uses a fixed seed so repeated strict runs are
  comparable.

Initial strict run:

```bash
poetry run krakked ml-regime-overlay-research \
  --window-set regime_diverse_4h \
  --allocation-pct 20 \
  --timeframe 4h \
  --strict-data \
  --save-dir reports/ml-regime-overlay-regime-diverse-20260531 \
  --json
```

Artifact:

- `reports/ml-regime-overlay-regime-diverse-20260531/aggregate.json`

Result:

- strict data passed for all requested windows
- ML-ready windows: `5 / 6`
- average ML return delta versus hand-coded top-2 soft `target_scale`: `-0.2356%`
- positive return-delta windows: `3 / 5`
- average max-drawdown delta: `+0.1867%`
- drawdown-improved windows: `4 / 5`
- average ML exposure: `11.46%` versus hand-coded baseline `12.41%`
- required minimum ML exposure for the non-cash gate: `4.34%`
- `promotion_gate.passed=false`

Decision:

- Keep the command as useful research infrastructure.
- Do not promote ML overlay behavior or write a runtime-controls plan from this
  first strict regime-diverse result.
- Correction (same day): regime-coverage instrumentation was added to the ML
  overlay report (`market_bucket`/benchmark returns per window plus a
  `regime_coverage_sufficient` gate). It showed `regime_diverse_4h` actually
  spans ~2 uptrend / 1 downtrend / 2 chop windows by benchmark return, so the
  earlier "no uptrend / data is the blocker" read was wrong. The overlay's gate
  failure is a legitimate negative across regimes (failed return and drawdown vs
  the hand-coded baseline), and the `trend_rank_proxy` source under-captures
  upside even in up-windows. Do **not** diagnose ML scale features on this
  source; see [`regime-diverse-evidence-plan.md`](./regime-diverse-evidence-plan.md)
  for the verified composition and corrected next steps.

## 2026-06-14: Volatility Forecast Verdict Readiness

The `krakked ml-risk-signal-research` command evaluates forecast skill only:
HAR-RV-style next-window volatility forecasts versus previous-horizon,
rolling-volatility, and RiskMetrics EWMA baselines. It does not simulate
exposure rules or trading P&L.

Read `summary.forecast_verdict_readiness` before reading `summary.lane_status`.
Runs with zero model observations, missing/partial benchmark OHLC, insufficient
training history, too few evaluable regime buckets, or overlapping non-current
scored evaluation windows are not fair model verdicts. They should report
`insufficient_data`, `insufficient_training`, `insufficient_regime_coverage`, or
`insufficient_independence`, and `kill_criterion.triggered=false`.

The readiness payload includes `window_independence` diagnostics. Overlapping
current-rolling examples are reported, but only excessive overlap between
non-current scored windows blocks a verdict; otherwise a rolling current window
would make every freshness check fail.

Only `close_volatility_forecast_lane` on a readiness-passing report means the
volatility-forecasting lane failed its pre-registered exposure-research bar.

## 2026-06-14: Volatility Forecast Strict Verdict After History Import

Imported Kraken historical OHLCVT `XBTUSD_240.csv` rows from Q4 2025 and Q1
2026 quarterly archives into the local BTC/USD `4h` cache, covering
`2025-12-01T00:00:00Z` through `2026-02-19T20:00:00Z`. The local BTC/USD `4h`
cache then spanned `2025-12-01T00:00:00Z` through `2026-06-14T20:00:00Z` with
`1,176` unique bars.

Command:

```bash
poetry run krakked ml-risk-signal-research \
  --strict-data \
  --save-dir reports/ml-risk-signal-regime-diverse-strict-after-import
```

Artifact:

- `reports/ml-risk-signal-regime-diverse-strict-after-import/aggregate.json`

Result:

- `forecast_verdict_readiness.status=ready_for_verdict`
- `strict_data_ready=true`
- `window_independence.status=ready`
- model-ready windows: `5 / 6`
- evaluation observations: `605`
- overall mean model versus EWMA QLIKE improvement: `-9981.5281%`
- downtrend bucket model versus EWMA QLIKE improvement: `-41195.9083%`
- uptrend bucket model versus EWMA QLIKE improvement: `+0.7662%`
- current rolling model versus EWMA QLIKE improvement: `-10.0323%`
- `lane_status=close_volatility_forecast_lane`
- `kill_criterion.triggered=true`

Readout:

- The large overall negative number is outlier-dominated by the January
  downtrend/crash window. It should not be quoted without the bucket breakdown.
- The HAR-RV model roughly tied EWMA in the uptrend bucket, slightly lagged EWMA
  in the current rolling bucket, and catastrophically under-forecast the
  downtrend bucket.
- That downtrend model was trained only on the prior calm/uptrend history
  available under the chronological harness, so this is a valid failure of the
  pre-registered slice, not proof that every possible regime-aware volatility
  model is impossible.
- EWMA was the useful finding: it remained adaptive and much more stable than
  the HAR-RV model on this target.

Decision:

- Close this HAR-RV volatility-forecasting lane for exposure-influencing
  research under the pre-registered bar.
- Do not iterate model variants on the same next-window volatility target as a
  path to trading influence unless a future lane is explicitly pre-registered
  as a different adaptive/regime-aware hypothesis with deeper history.
- Consider a separate display-only EWMA volatility/risk signal lane before any
  new ML volatility work. It would be product context only and would not affect
  trading without its own approval gate.
- Keep the command and importer as research infrastructure. Display-only,
  anomaly-detection, or drawdown-probability lanes would need separate
  pre-registered targets and bars.
