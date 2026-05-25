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

