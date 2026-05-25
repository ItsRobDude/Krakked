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
