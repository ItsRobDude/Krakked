# Replay Experiment Log

This log captures durable conclusions from local offline replay evidence. Saved
JSON reports and ad-hoc probe outputs remain local unless they become part of a
reviewed report fixture.

## 2026-05-25: Starter Strategy Replay Truth Pass

Context:

- Baseline replay window: `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`
- Cost model: `50 bps` slippage plus `25 bps` taker fee
- Starter pair/timeframe coverage was complete for the default replay lane.
- The report now distinguishes configured params from constructor defaults and
  engine evaluation timeframes before drawing strategy conclusions.

### `rs_rotation` Cost Sensitivity

`rs_rotation` is not promotion-ready under the current replay cost model. This is
an offline replay finding, not a live trading conclusion.

| Requested window | Current-cost return | Current-cost `rs_rotation` realized PnL | Zero-cost return | Zero-cost `rs_rotation` realized PnL |
| --- | ---: | ---: | ---: | ---: |
| `2026-03-21T00:00:00+00:00 -> 2026-04-10T00:00:00+00:00` | `-0.6398%` | `-$62.5823` | `-0.2644%` | `-$29.0903` |
| `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00` | `-0.2199%` | `-$8.9661` | `0.0802%` | `$17.7816` |
| `2026-04-30T00:00:00+00:00 -> 2026-05-20T00:00:00+00:00` | `-0.4704%` | `-$13.2996` | `-0.2962%` | `$1.9889` |
| `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00` | `-0.4940%` | `-$27.8754` | `-0.3883%` | `-$18.7442` |

Readout:

- Current-cost replay lost across all four covered windows.
- Zero-cost replay was mixed, which points to cost sensitivity rather than a
  proven directional inversion.
- Keep `rs_rotation` in investigation status until a later slice explains or
  improves its edge after realistic costs.

### `vol_breakout` Threshold Probe

Probe configuration:

- Window: `2026-05-18T12:00:00+00:00 -> 2026-05-25T00:00:00+00:00`
- Starter pairs: `BTC/USD`, `ETH/USD`, `SOL/USD`, `ADA/USD`
- Timeframes swept independently with explicit replay `timeframes=[...]`
- `lookback_bars=20`, `breakout_multiple=1.5`
- `min_compression_bps` swept across `10`, `25`, `50`, `100`, `150`, `250`, `500`

| Timeframe | `min_compression_bps` | Preflight | Intents | Actions | Filled orders |
| --- | ---: | --- | ---: | ---: | ---: |
| `15m` | `10` | `ready` | `0` | `0` | `0` |
| `15m` | `25` | `ready` | `0` | `0` | `0` |
| `15m` | `50` | `ready` | `1` | `1` | `1` |
| `15m` | `100` | `ready` | `11` | `11` | `4` |
| `15m` | `150` | `ready` | `22` | `22` | `4` |
| `15m` | `250` | `ready` | `35` | `35` | `5` |
| `15m` | `500` | `ready` | `40` | `40` | `5` |
| `1h` | `10` | `ready` | `0` | `0` | `0` |
| `1h` | `25` | `ready` | `0` | `0` | `0` |
| `1h` | `50` | `ready` | `0` | `0` | `0` |
| `1h` | `100` | `ready` | `0` | `0` | `0` |
| `1h` | `150` | `ready` | `0` | `0` | `0` |
| `1h` | `250` | `ready` | `0` | `0` | `0` |
| `1h` | `500` | `ready` | `3` | `3` | `2` |

Readout:

- The strategy can fire on cached data, so the silence is not a replay wiring
  failure.
- The default `10 bps` compression threshold was too selective for this probe
  window.
- Do not change thresholds from this single short-window probe; use it only to
  justify a later dedicated threshold-validation slice.

## 2026-05-25: Explicit Local Config And `vol_breakout` Deferral

Context:

- Local replay config was repaired to use explicit params for `trend_core`,
  `majors_mean_rev`, and `rs_rotation`.
- `vol_breakout` keeps explicit params in the local config but remains disabled
  because its `15m` cache does not cover the current rolling replay window.
- Requested rolling replay window:
  `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`

15m backfill attempt:

- A repo-local `backfill_ohlc` pass was run for `BTC/USD`, `ETH/USD`,
  `SOL/USD`, and `ADA/USD` with `timeframe=15m` and `since=2026-05-04T23:45:00+00:00`.
- Kraken returned `721` bars per pair and the local cache now runs
  `2026-05-18T11:30:00+00:00 -> 2026-05-26T00:30:00+00:00`.
- Enabling `vol_breakout` for the rolling window still produces
  `partial_window` coverage for `BTC/USD@15m`, `ETH/USD@15m`, `SOL/USD@15m`,
  and `ADA/USD@15m`.

Published explicit 3-strategy baseline:

- Window: `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`
- Preflight: `ready`, `missing=0`, `partial=0`, `strategy_coverage_gaps=0`
- Trust: `limited`
- Actions: `7` total, `5` blocked, `2` filled, `0` execution errors
- Equity: `$10000.00 -> $9861.44`, return `-1.3856%`
- Interpretation: this is the current truthful explicit-config baseline for
  the covered starter strategies, not a promotion signal.

Ready short-window `vol_breakout` probe:

- Window: `2026-05-19T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`
- Active strategies in probe: `trend_core`, `vol_breakout`,
  `majors_mean_rev`, `rs_rotation`
- Preflight: `ready`, `missing=0`, `partial=0`, `strategy_coverage_gaps=0`
- Configured `vol_breakout` threshold `min_compression_bps=10.0` emitted
  `0` intents, `0` actions, and `0` fills.

Same-window `vol_breakout` threshold sweep:

| `min_compression_bps` | Trust | Intents | Actions | Blocked | Filled orders | Realized PnL |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| `10` | `weak_signal` | `0` | `0` | `0` | `0` | `$0.0000` |
| `25` | `weak_signal` | `0` | `0` | `0` | `0` | `$0.0000` |
| `50` | `decision_helpful` | `1` | `1` | `0` | `1` | `-$1.2563` |
| `100` | `limited` | `11` | `11` | `7` | `4` | `-$1.3944` |
| `150` | `limited` | `22` | `22` | `18` | `4` | `-$1.3944` |
| `250` | `limited` | `35` | `35` | `30` | `5` | `-$1.4404` |
| `500` | `limited` | `52` | `52` | `39` | `5` | `-$1.4404` |

Readout:

- Do not re-enable `vol_breakout` in the current rolling baseline until the
  `15m` cache covers `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`
  or the rolling replay window is shortened intentionally.
- The configured `10 bps` threshold remains silent even when the short-window
  data is ready.
- Threshold changes should remain a separate validation slice; this pass only
  proves the deferred strategy is data-limited on the rolling window and
  threshold-sensitive on a ready short window.

## 2026-05-25: `vol_breakout` Ready-Window Threshold Sweep

Context:

- Purpose: evaluate `vol_breakout` on multiple short windows where the current
  `15m` cache is ready, without changing thresholds or re-enabling it in the
  published rolling baseline.
- Active strategy in the sweep: `vol_breakout` only.
- Replay timeframes: `15m`, `1h`.
- Starter pairs: `BTC/USD`, `ETH/USD`, `SOL/USD`, `ADA/USD`.
- Local artifact:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\vol-breakout-threshold-sweep-20260525.json`

Ready windows:

- `2026-05-19T00:00:00+00:00 -> 2026-05-22T00:00:00+00:00`
- `2026-05-20T00:00:00+00:00 -> 2026-05-23T00:00:00+00:00`
- `2026-05-21T00:00:00+00:00 -> 2026-05-24T00:00:00+00:00`
- `2026-05-22T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`

Aggregate readout:

| `min_compression_bps` | Windows with intents | Intents | Actions | Blocked | Fills | Realized PnL | Return range |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `10` | `0/4` | `0` | `0` | `0` | `0` | `$0.0000` | `0.0000%..0.0000%` |
| `25` | `0/4` | `0` | `0` | `0` | `0` | `$0.0000` | `0.0000%..0.0000%` |
| `50` | `1/4` | `1` | `1` | `0` | `1` | `-$1.2563` | `-0.0244%..0.0000%` |
| `100` | `4/4` | `20` | `20` | `11` | `9` | `-$7.6721` | `-0.1529%..0.2742%` |
| `150` | `4/4` | `42` | `42` | `29` | `12` | `-$11.4408` | `-0.5870%..0.2742%` |
| `250` | `4/4` | `67` | `67` | `52` | `14` | `-$12.7418` | `-0.5870%..0.3897%` |
| `500` | `4/4` | `102` | `102` | `71` | `14` | `-$12.7418` | `-0.5870%..0.3897%` |

Readout:

- The configured `10 bps` threshold is silent across all four ready windows.
- `25 bps` is also silent across all four ready windows.
- `50 bps` is barely active: one intent and one fill across four windows.
- `100 bps` and above consistently exercise the strategy, but most actions are
  blocked by risk and total realized PnL remains negative under the current
  replay cost model.
- Keep `vol_breakout` disabled in the published rolling baseline. The next
  useful slice is not a threshold change; it is explaining why higher
  thresholds generate many risk-blocked actions and whether the strategy's
  desired exposure should be constrained before any threshold decision.

## 2026-05-25: `vol_breakout` Risk-Block Investigation

Context:

- Follow-up to the ready-window threshold sweep above.
- Local artifact:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\vol-breakout-action-details-20260525.json`
- Scope: inspection only. No threshold, sizing, risk-limit, or enablement
  changes were made.

Findings:

- `vol_breakout` emits entry intents with `desired_exposure_usd=None` and
  `confidence=0.8`, so the risk engine auto-sizes using ATR and
  `max_risk_per_trade_pct`.
- The auto-sized projected notionals are far above the current 5% strategy cap.
  Example first entries were clamped from projected strategy exposure such as
  `$14297.02`, `$20317.97`, `$30487.41`, and `$31631.31` down to about `$500`.
- After the first clamped open, later entries are mostly blocked by
  `Strategy vol_breakout budget exceeded`, `Max per asset limit`, and sometimes
  `Max portfolio exposure limit`.
- The action trace shows a pair-identity mismatch: simulated trades are ingested
  into portfolio positions using canonical pairs like `XBTUSD`, while
  `vol_breakout` and risk actions use configured display pairs like `BTC/USD`.
  Subsequent same-pair actions can therefore show `current_base_size=0.0` even
  after an earlier fill, while total portfolio exposure still reflects the
  canonical stored position.
- Because `vol_breakout` checks ownership with raw `pos.pair == pair`, the same
  mismatch can also prevent it from recognizing held positions for `increase`
  vs `enter` decisions and for exit generation.

Aggregate blocked-reason prefixes from the action-details probe:

| `min_compression_bps` | `Strategy vol_breakout budget exceeded` | `Max per asset limit` | `Max portfolio exposure limit` |
| ---: | ---: | ---: | ---: |
| `100` | `11` | `11` | `4` |
| `150` | `29` | `29` | `14` |
| `250` | `52` | `52` | `26` |
| `500` | `71` | `71` | `35` |

Readout:

- Do not tune `min_compression_bps` yet. Higher thresholds merely expose a
  sizing and pair-normalization problem sooner.
- The next fix should normalize pair matching for existing positions before
  strategies and risk compare positions to intents. This should cover
  `vol_breakout` first, but the same raw pair-map pattern appears in other
  strategies and in the risk engine.
- After the normalization fix, rerun the same ready-window sweep before making
  any threshold or enablement decision.

## 2026-05-25: Post Pair-Normalized Position Probe

Context:

- Follow-up to the risk-block investigation above after normalizing internal
  strategy/risk position comparisons through market-data pair keys.
- Local artifacts:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\vol-breakout-threshold-sweep-post-pair-key-20260525.json`
  and
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\vol-breakout-action-details-post-pair-key-20260525.json`
- Scope: probe only. No threshold, sizing, risk-limit, enablement, or published
  latest-report changes were made.

Ready windows rerun:

- `2026-05-19T00:00:00+00:00 -> 2026-05-22T00:00:00+00:00`
- `2026-05-20T00:00:00+00:00 -> 2026-05-23T00:00:00+00:00`
- `2026-05-21T00:00:00+00:00 -> 2026-05-24T00:00:00+00:00`
- `2026-05-22T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`

Aggregate readout versus the prior action-details probe:

| `min_compression_bps` | Old actions / blocked / fills | New actions / blocked / fills | New nonzero `current_base_size` actions |
| ---: | --- | --- | ---: |
| `10` | `0 / 0 / 0` | `0 / 0 / 0` | `0` |
| `25` | `0 / 0 / 0` | `0 / 0 / 0` | `0` |
| `50` | `1 / 0 / 1` | `2 / 0 / 2` | `1` |
| `100` | `20 / 11 / 9` | `190 / 169 / 18` | `177` |
| `150` | `42 / 29 / 12` | `1436 / 1415 / 20` | `1416` |
| `250` | `67 / 52 / 14` | `2240 / 2213 / 26` | `2217` |
| `500` | `102 / 71 / 14` | `3004 / 2984 / 19` | `2980` |

Action mix after the fix:

| `min_compression_bps` | `open` | `close` | `none` |
| ---: | ---: | ---: | ---: |
| `50` | `1` | `1` | `0` |
| `100` | `12` | `174` | `4` |
| `150` | `17` | `1415` | `4` |
| `250` | `21` | `2216` | `3` |
| `500` | `17` | `2979` | `8` |

Readout:

- Pair-normalized matching is now visible in the replay: post-fix action traces
  include nonzero `current_base_size` for held positions instead of repeatedly
  treating canonical `XBTUSD` positions as unrelated to `BTC/USD` intents.
- The fix exposes a separate risk-handling issue: once positions are recognized,
  `vol_breakout` emits many `close` actions when breakout conditions fail, but
  most close actions are blocked by strategy, asset, and portfolio exposure
  limits.
- The default `10 bps` and `25 bps` thresholds remain silent on these ready
  windows. Higher thresholds are still not a promotion signal; they mostly
  expose de-risking and sizing/guardrail behavior under the current cost model.
- Next focused fix: make risk limits allow genuine close/reduce de-risking
  actions for existing positions instead of blocking them because the portfolio
  is already over an exposure cap.

## 2026-05-25: Post-Risk-Fix Strategy Revalidation

Context:

- Purpose: revalidate strategy conclusions after four replay/risk correctness
  fixes: pair-normalized position matching, de-risking actions, same-cycle risk
  cap aggregation, and cap-aware volatility sizing.
- Local artifacts:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\rs-rotation-post-risk-fix-revalidation-20260525.json`,
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\explicit-3strategy-post-risk-fix-rolling-20260525.json`,
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\all-four-post-risk-fix-ready-window-20260525.json`, and
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\post-risk-fix-latest-unchanged-verification-20260525.json`.
- `latest.json` was not published or modified:
  `2026-05-26T00:43:28.057826+00:00` before and after the runs.
- Pre-risk-fix strategy verdicts in this log should be treated as superseded or
  cautionary when they depend on strategy/risk execution behavior.

### `rs_rotation` Revalidation

Cost scenarios:

- Current cost: `25 bps` taker fee and `50 bps` slippage.
- Zero cost: `0 bps` fee and `0 bps` slippage.
- Sensitivity: `25 bps` taker fee and `20 bps` slippage.

| Requested window | Scenario | Return | `rs_rotation` realized PnL | Actions / blocked / fills | Wins / losses |
| --- | --- | ---: | ---: | ---: | ---: |
| `2026-03-21T00:00:00+00:00 -> 2026-04-10T00:00:00+00:00` | current cost | `0.2917%` | `$0.8388` | `14 / 7 / 4` | `2 / 2` |
| `2026-03-21T00:00:00+00:00 -> 2026-04-10T00:00:00+00:00` | zero cost | `0.3333%` | `$2.6283` | `14 / 7 / 4` | `2 / 0` |
| `2026-03-21T00:00:00+00:00 -> 2026-04-10T00:00:00+00:00` | `25 bps` fee, `20 bps` slippage | `0.3083%` | `$1.0846` | `14 / 7 / 4` | `2 / 2` |
| `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00` | current cost | `-0.4853%` | `-$38.3828` | `24 / 8 / 14` | `3 / 11` |
| `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00` | zero cost | `-0.2663%` | `-$18.3198` | `24 / 8 / 14` | `3 / 4` |
| `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00` | `25 bps` fee, `20 bps` slippage | `-0.3977%` | `-$30.7310` | `24 / 8 / 14` | `3 / 11` |
| `2026-04-30T00:00:00+00:00 -> 2026-05-20T00:00:00+00:00` | current cost | `-0.1156%` | `-$11.5640` | `18 / 5 / 5` | `1 / 4` |
| `2026-04-30T00:00:00+00:00 -> 2026-05-20T00:00:00+00:00` | zero cost | `-0.0394%` | `-$3.9426` | `18 / 5 / 5` | `1 / 2` |
| `2026-04-30T00:00:00+00:00 -> 2026-05-20T00:00:00+00:00` | `25 bps` fee, `20 bps` slippage | `-0.0851%` | `-$8.5146` | `18 / 5 / 5` | `1 / 4` |
| `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00` | current cost | `-0.4547%` | `-$45.2331` | `8 / 3 / 4` | `0 / 4` |
| `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00` | zero cost | `-0.3754%` | `-$37.5708` | `8 / 3 / 4` | `0 / 1` |
| `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00` | `25 bps` fee, `20 bps` slippage | `-0.4228%` | `-$42.2040` | `8 / 3 / 4` | `0 / 4` |

Aggregate readout:

| Scenario | Ready windows | Actions | Blocked | Fills | Execution errors | Aggregate return PnL | Aggregate realized PnL | Wins / losses |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Current cost | `4/4` | `64` | `23` | `27` | `0` | `-$76.3959` | `-$94.3412` | `6 / 21` |
| Zero cost | `4/4` | `64` | `23` | `27` | `0` | `-$34.7914` | `-$57.2049` | `6 / 7` |
| `25 bps` fee, `20 bps` slippage | `4/4` | `64` | `23` | `27` | `0` | `-$59.7408` | `-$80.3650` | `6 / 21` |

Readout:

- `rs_rotation` improved versus the earlier pre-risk-fix conclusion in one
  older window, but still loses in aggregate under all tested cost scenarios.
- Zero-cost revalidation is still negative in aggregate, so the updated evidence
  is no longer just a cost-sensitivity story.
- Keep `rs_rotation` investigation-only. Do not promote or retire it from this
  sample; the next useful slice is to explain why April and May windows dominate
  losses before changing logic or score gates.

### Explicit 3-Strategy Rolling Baseline Rerun

Window:
`2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`

- Enabled strategies: `trend_core`, `majors_mean_rev`, `rs_rotation`.
- Preflight: `ready`; missing series `0`; partial series `0`;
  strategy coverage gaps `0`.
- Trust: `limited`.
- Actions: `8`; blocked `3`; filled orders `5`; execution errors `0`.
- Equity: `$10000.00 -> $9948.12`; return `-0.5188%`.
- `rs_rotation` realized PnL: `-$51.5818`; `5` trades, `0` winners,
  `5` losers; `21` low-score entries filtered.

Delta versus the current published `latest.json`:

| Field | Published latest | Post-risk-fix rerun | Delta |
| --- | ---: | ---: | ---: |
| Actions | `7` | `8` | `+1` |
| Blocked actions | `5` | `3` | `-2` |
| Filled orders | `2` | `5` | `+3` |
| Execution errors | `0` | `0` | `0` |
| Return PnL | `-$138.5588` | `-$51.8786` | `+$86.6803` |
| Realized PnL | `-$2.5125` | `-$51.5818` | `-$49.0692` |

Readout:

- The risk fixes materially changed the rolling baseline. Fewer actions are
  blocked and more orders fill, but the result is still not a promotion signal.
- `trend_core` and `majors_mean_rev` remain evaluated but silent in this window.
- Do not publish this rerun as latest without a separate replay-baseline
  decision.

### All-Four Short-Window Rerun

Window:
`2026-05-18T12:00:00+00:00 -> 2026-05-25T00:00:00+00:00`

- Enabled strategies: `trend_core`, `vol_breakout`, `majors_mean_rev`,
  `rs_rotation`.
- Preflight: `limited`.
- Partial series: `ADA/USD@1d`, `BTC/USD@1d`, `ETH/USD@1d`, `SOL/USD@1d`.
- Strategy coverage gaps: `0`.
- Trust: `weak_signal`.
- Actions: `0`; filled orders `0`; execution errors `0`.

Readout:

- The short window has ready `15m` coverage for `vol_breakout`, but it is not a
  fully ready all-four replay because `trend_core` asks for `1d` context and the
  short window is under-warmed for that lane.
- With the current explicit `vol_breakout` threshold `min_compression_bps=10.0`,
  `vol_breakout` still emits `0` intents even when `15m` data is ready.
- This run is useful as a coverage/truth check, not as a strategy-quality
  comparison.

## 2026-05-26: `rs_rotation` Attribution Pass

Context:

- Purpose: explain the post-risk-fix `rs_rotation` losses before changing any
  strategy logic, thresholds, defaults, risk limits, local config, or published
  replay reports.
- Local artifact:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\rs-rotation-attribution-post-risk-fix-20260526.json`.
- Windows revalidated:
  `2026-03-21T00:00:00+00:00 -> 2026-04-10T00:00:00+00:00`,
  `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00`,
  `2026-04-30T00:00:00+00:00 -> 2026-05-20T00:00:00+00:00`, and
  `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`.
- `latest.json` was not published or modified by this attribution pass.

Aggregate attribution:

| Scenario | Actions / blocked / fills | Return PnL | Realized PnL | Closed gross PnL | Closed net PnL | Closed fees | Closed slippage estimate | Closed W / L | Open unrealized |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Current cost | `64 / 23 / 27` | `-$76.3959` | `-$94.3412` | `-$80.4321` | `-$92.0713` | `$11.6392` | `$23.2998` | `6 / 7` | `$17.9225` |
| Zero cost | `64 / 23 / 27` | `-$34.7914` | `-$57.2049` | `-$57.1782` | `-$57.1782` | `$0.0000` | `$0.0000` | `6 / 7` | `$22.3867` |
| `25 bps` fee, `20 bps` slippage | `64 / 23 / 27` | `-$59.7408` | `-$80.3650` | `-$66.4592` | `-$78.0994` | `$11.6402` | `$9.3085` | `6 / 7` | `$20.6001` |

Current-cost pair attribution:

| Pair | Closed segments | Closed W / L | Closed gross PnL | Closed net PnL | Closed fees | Closed slippage estimate | Open unrealized |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ADA/USD` | `3` | `1 / 2` | `-$28.3176` | `-$33.2128` | `$4.8952` | `$9.7884` | `$0.0000` |
| `BTC/USD` | `4` | `2 / 2` | `-$3.6391` | `-$5.0616` | `$1.4225` | `$2.8449` | `-$10.1486` |
| `ETH/USD` | `4` | `3 / 1` | `-$5.2855` | `-$8.0479` | `$2.7624` | `$5.5244` | `$28.3271` |
| `SOL/USD` | `2` | `0 / 2` | `-$43.1898` | `-$45.7489` | `$2.5591` | `$5.1420` | `-$0.2560` |

Readout:

- Costs make the result worse, but they are not the root cause. Closed gross PnL
  is already negative under current cost (`-$80.4321`) and zero-cost closed PnL
  remains negative (`-$57.1782`).
- Losses are concentrated in `SOL/USD` and `ADA/USD`. The largest losing
  segment was `SOL/USD` entered at `2026-05-12T00:00:00+00:00` and exited at
  `2026-05-16T00:00:00+00:00`, with `-$45.0804` net current-cost PnL.
- Accepted entries were not low-confidence leftovers. The closed-segment median
  entry score was `1.0`, and the median entry relative return was `607.5063 bps`.
  The strategy filtered `54` low-score intents before risk.
- This is not short-hold churn. Closed-segment median hold was `96 hours`
  (`24` bars on the configured `4h` timeframe).
- There is a config/risk intent mismatch: local `rs_rotation` params ask for
  `total_allocation_pct=20.0` across `top_n=2`, while
  `risk.max_per_strategy_pct["rs_rotation"]` is `5.0`. The replay therefore
  cannot express the configured two-asset, 20% allocation intent; it becomes a
  cap-constrained variant with frequent strategy-budget blocks.

Conclusion:

- Keep `rs_rotation` investigation-only. The attribution does not point to a
  clean code bug or a threshold tweak.
- The next focused slice should be an in-memory allocation/cap alignment probe:
  compare the current `20%` target under the `5%` cap against a cap-aligned
  `5%` target, and optionally a cap-relaxed `20%` diagnostic run. Do not change
  local config or risk defaults until that probe shows whether the strategy is
  failing because of the signal itself or because the configured allocation
  intent is impossible under the current risk envelope.

### `rs_rotation` Cap-Mismatch Warning Decision

The `rs_rotation` allocation warning is intentional and informational, not a
replay/reporting regression. Local config asks for `total_allocation_pct=20.0`
across `top_n=2`, while the active risk envelope caps `rs_rotation` at `5%`,
the portfolio at `10%`, and each asset at `5%`. The warning should remain
operator-visible because it explains why replay actions can be cap-constrained
instead of expressing the configured strategy target.

Follow-up allocation probes did not turn this into a cap-loosening task:
cap-aligned, envelope-aligned, and cap-relaxed variants still failed to produce
a promotion-quality result. The current operator conclusion is therefore:
record the mismatch, keep `rs_rotation` investigation-only, and do not loosen
risk caps or change `rs_rotation` allocation defaults from this evidence alone.

### 2026-05-30 Starter Cap Alignment

After the OHLC freshness path was made explicit and the same rolling replay
remained behaviorally unchanged, the remaining warning was no longer a replay
trust issue. The starter config was aligned by reducing
`rs_rotation.total_allocation_pct` to `5.0`, matching the conservative
`risk.max_per_strategy_pct["rs_rotation"]` default instead of loosening risk
caps. This removes the default replay warning while keeping `rs_rotation`
investigation-only under current evidence.

### 2026-05-30 `rs_rotation` Default Disable And V2 Direction

After the cap alignment, a read-only strategy-quality pass isolated
`rs_rotation` and rechecked five covered 20-day replay windows:

| Scenario | Avg return | Realized PnL | Actions / fills | Positive windows |
| --- | ---: | ---: | ---: | ---: |
| Active `5%`, current costs | `-0.2081%` | `-$116.7125` | `63 / 50` | `0 / 5` |
| Active `5%`, zero costs | `-0.0984%` | `-$65.0642` | `63 / 50` | `1 / 5` |
| Cap-relaxed `20%`, current costs | `-0.8038%` | `-$451.4611` | `60 / 53` | `0 / 5` |

Signal diagnostics on the default `lookback_bars=42`, `top_n=2`,
24-hour-forward horizon showed the selected pairs were only slightly less bad
than the full starter universe, not tradable after costs:

- Mean selected forward return: `-0.3045%`
- Mean universe forward return: `-0.3617%`
- Mean selected spread over universe: `+0.0572%`
- Positive selected cycle rate: `41.54%`
- Mean rank correlation: `0.0123`
- Top trailing pair was the next forward-best pair in only `24.62%` of cycles.

Conclusion:

- Disable `rs_rotation` from the default starter enabled list.
- Keep its config block and conservative risk cap available for manual
  research, but leave the config entry disabled by default.
- Do not loosen caps or increase allocation. Larger allocation only scaled the
  same weak signal.
- A real v2 should not be a parameter tweak to v1. It should combine an
  absolute time-series momentum gate, volatility-normalized cross-sectional
  scoring, explicit cash/flat behavior in broad selloffs, turnover controls,
  and out-of-sample promotion gates before becoming an operator default.

### 2026-05-30 `rs_rotation_v2` Research Probe

A replay-only `rs-rotation-v2-research` CLI command now exists so the next
signal can be tested without registering a new live/paper strategy. It reads
cached OHLC only, uses the configured `rs_rotation` pairs/timeframe/lookback by
default, and writes a structured JSON report when `--save-report` is supplied.

The probe deliberately changes the signal shape instead of tweaking v1:

- absolute trailing momentum must clear estimated round-trip costs plus an
  explicit edge buffer;
- cross-sectional rank is volatility-normalized rather than raw trailing return;
- BTC and broad-basket regime gates keep the strategy in cash during weak
  markets unless explicitly disabled for diagnostics;
- current holdings are retained unless a replacement clears the configured
  score-gap hurdle;
- reports include active/cash cycle counts, turnover, fees, slippage estimate,
  forward-selection diagnostics, an equal-weight reference, and research gates.

Example:

```bash
poetry run krakked rs-rotation-v2-research \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --json \
  --save-report rs-rotation-v2-research.json
```

This is still research-only. A `research_pass` report is evidence for deeper
out-of-sample work, not approval to enable a runtime strategy by default.

Initial rolling-window check:

- Window: `2026-05-10T00:00:00+00:00 -> 2026-05-30T00:00:00+00:00`.
- Report:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\rs-rotation-v2-research-20260530.json`.
- Coverage: `ready`, `4` usable `4h` series, no missing or partial series.
- Status: `research_fail`.
- Result: stayed in cash, `0` trades, `0` active cycles, ending equity
  `$10,000.00`.
- Gate failures: `positive_return_after_costs`, `enough_active_cycles`.
- Readout: this is the intended behavior for the default v2 filter in a broad
  weak window. It avoided the v1 failure mode of forcing relative winners when
  absolute and regime evidence was still negative.

Diagnostics:

- Disabling both regime gates and removing only the extra edge buffer still
  produced `0` trades because no pair cleared the `150 bps` fee/slippage hurdle.
- With zero fees, zero slippage, zero edge buffer, and regime gates disabled,
  the ranking/sizing path executed but still failed: `3` trades, `2` active
  cycles, return `-0.0407%`, and selected forward return underperformed the
  universe on the only evaluable active cycle.

### 2026-05-30 `rs_rotation_v2` Multi-Window Sweep

Artifacts:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\rs-rotation-v2-sweep-20260530\aggregate.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\rs-rotation-v2-sweep-20260530\parameter-grid.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\rs-rotation-v2-sweep-20260530\simple-baselines.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\rs-rotation-v2-sweep-20260530\btc-absolute-momentum-grid.json`

Windows:

- `2026-03-21T00:00:00+00:00 -> 2026-04-10T00:00:00+00:00`
- `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00`
- `2026-04-30T00:00:00+00:00 -> 2026-05-20T00:00:00+00:00`
- `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`
- `2026-05-10T00:00:00+00:00 -> 2026-05-30T00:00:00+00:00`

Scenario summary:

| Scenario | Avg return | Positive windows | Passed windows | Trades | Active cycles |
| --- | ---: | ---: | ---: | ---: | ---: |
| `default` | `-0.1154%` | `1/5` | `1/5` | `38` | `23` |
| `cost_hurdle_no_regime` | `-0.1801%` | `1/5` | `1/5` | `36` | `28` |
| `raw_rank_zero_cost_no_regime` | `-0.0599%` | `1/5` | `1/5` | `44` | `33` |

Parameter grid:

- Grid size: `864` current-cost configurations across lookback, rebalance
  interval, `top_n`, edge buffer, minimum absolute momentum, and regime mode.
- `0 / 864` had positive average return.
- `0 / 864` passed at least `3 / 5` windows.
- `0 / 864` were positive in at least `3 / 5` windows.
- `0 / 864` beat equal-weight in at least `4 / 5` windows.

Best grid row:

| Lookback | Rebalance bars | Top N | Edge bps | Min abs bps | Regime | Avg return | Passed | Positive | Trades | Active cycles |
| ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| `42` | `12` | `1` | `0` | `0` | `btc_only` | `-0.0401%` | `2/5` | `2/5` | `15` | `13` |

Best row by window:

| Window | Status | Return | Active cycles | Trades | Equal-weight return |
| --- | --- | ---: | ---: | ---: | ---: |
| `20260321-20260410` | `research_pass` | `+0.2175%` | `4` | `5` | `-0.1306%` |
| `20260410-20260430` | `research_fail` | `-0.0824%` | `5` | `4` | `+0.0856%` |
| `20260430-20260520` | `research_pass` | `+0.0367%` | `3` | `4` | `-0.0453%` |
| `20260505-20260525` | `research_fail` | `-0.3724%` | `1` | `2` | `-0.2700%` |
| `20260510-20260530` | `research_fail` | `+0.0000%` | `0` | `0` | `-0.5841%` |

Simple baselines at `5%` allocation:

| Baseline | Avg return | Positive windows |
| --- | ---: | ---: |
| Cash | `+0.0000%` | `0/5` |
| Equal-weight starter basket | `-0.1889%` | `1/5` |
| BTC-only buy-and-hold | `-0.0564%` | `3/5` |
| Oracle best single pair per window | `-0.0054%` | `4/5` |

BTC-only absolute-momentum diagnostic:

- Best row averaged `+0.0189%`, but passed only `1/5` windows and was positive
  in only `2/5`.
- This is more plausible as a defensive overlay than the alt-rotation family,
  but it is not a standalone promotion candidate under the current gates.

Conclusion:

- Do not wire `rs_rotation_v2` as a runtime strategy.
- Do not keep tuning raw relative-strength rotation. The multi-window evidence
  says this signal family is still too weak after costs and gates.
- The only useful idea that survived the sweep is not rotation itself; it is a
  defensive market-state filter that sometimes keeps the book in cash. Treat
  that as future risk-overlay research, separate from `rs_rotation`.

### 2026-05-30 Market Regime Overlay Decision

Decision:

- Keep `rs_rotation` / `rs_rotation_v2` standalone strategy work research-only
  unless a new written hypothesis changes the evidence target.
- Move the surviving defensive behavior into a research-only portfolio-level
  market regime overlay lane.
- Do not wire runtime blocking, runtime clamping, or config defaults until the
  overlay proves itself in multi-window replay comparison.

The hardened plan is recorded in
[`market-regime-overlay-plan.md`](./market-regime-overlay-plan.md). The next
slice should implement only the cache-only evaluator and comparison report:

- `krakked market-regime-research`
- `krakked market-regime-overlay-backtest`

Promotion requires improved or preserved average return after costs, drawdown
improvement in at least `3 / 5` windows, no weak-signal regression, explicit
operator-readable reason codes, and no strict-data gaps.

### 2026-05-30 Market Regime Overlay Research Implementation

Implemented the research-only overlay lane:

- `krakked market-regime-research`
- `krakked market-regime-overlay-backtest`

Both commands remain cache-only and do not change runtime strategy behavior,
live-trading gates, config defaults, or normal replay semantics.

Initial rolling-window artifacts:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-research-20260530.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-overlay-backtest-20260530.json`

Initial rolling-window result:

- Window: `2026-05-10T00:00:00+00:00 -> 2026-05-30T00:00:00+00:00`.
- Strict-data mode: passed.
- Research classifier cycles: `121` total, `0` risk-on, `48` neutral, `73`
  risk-off.
- Top reasons: `btc_momentum_negative`, `basket_momentum_negative`, and
  warmup `insufficient_data`.
- Overlay replay comparison: baseline and overlay both stayed `weak_signal`,
  ending equity stayed `$10,000.00`, fills stayed `0`, and overlay interventions
  stayed `0`.

Readout:

- The implementation and report path are working.
- This is not promotion evidence yet. With the current starter defaults,
  the replay produced no strategy intents, so the overlay had nothing to clamp
  or block.
- The next useful task is the five-window overlay comparison from
  `docs/market-regime-overlay-plan.md`, not runtime wiring.

### 2026-05-30 Market Regime Overlay Five-Window Fixed Defaults

Artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-overlay-five-window-20260530\aggregate.json`

Window results:

| Window | Baseline return | Overlay return | Baseline fills | Overlay fills | Overlay interventions | Trust change |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `20260321-20260410` | `+0.0422%` | `+0.0422%` | `2` | `2` | `0` | `limited -> limited` |
| `20260410-20260430` | `-0.1130%` | `+0.0000%` | `4` | `0` | `12` | `decision_helpful -> weak_signal` |
| `20260430-20260520` | `+0.0000%` | `+0.0000%` | `0` | `0` | `0` | `weak_signal -> weak_signal` |
| `20260505-20260525` | `+0.0000%` | `+0.0000%` | `0` | `0` | `0` | `weak_signal -> weak_signal` |
| `20260510-20260530` | `+0.0000%` | `+0.0000%` | `0` | `0` | `0` | `weak_signal -> weak_signal` |

Aggregate:

- Average baseline return: `-0.0142%`.
- Average overlay return: `+0.0084%`.
- Total baseline fills: `6`.
- Total overlay fills: `2`.
- Overlay interventions: `12`, all blocks, all in one window.
- Drawdown improved or preserved in `5 / 5` windows, but the only material
  improvement came from eliminating all trades in the losing window.

Decision:

- Do not runtime-wire the fixed-default overlay.
- Do not call this a promotion pass. It violates the gate that the overlay must
  not turn a decision-helpful replay into a weak-signal replay.
- The result is useful but mostly says the current starter default replay is too
  sparse for overlay evaluation.
- The next research step should be a controlled exposure scenario, not a broad
  parameter sweep.

### 2026-05-30 Market Regime Controlled Exposure Scenarios

Implemented:

- `krakked market-regime-exposure-research`

This command is cache-only and research-only. It simulates controlled long
exposure over cached OHLC, including fees, rebalancing, equity curves, drawdown,
and exposure percentage. It does not use the strategy engine, order router, or
live/paper execution path.

Scenario set:

- `starter_equal_weight`
- `btc_only`
- `alt_equal_weight`

Overlay modes:

- `entry_guard`: block/clamp only new or increased exposure.
- `target_scale`: scale target exposure by market state; neutral halves target
  exposure and risk-off targets cash.

Default controlled exposure artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-scenarios-20260530\aggregate.json`

Default settings:

- Allocation: `20%`.
- Rebalance interval: `6` bars.
- Timeframe: `4h`.
- Fees: `25 bps`.

Default five-window aggregate:

| Scenario | Mode | Avg return delta | Positive windows | Avg drawdown delta | Drawdown improved | Avg overlay active cycles |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `starter_equal_weight` | `entry_guard` | `-0.0309%` | `2 / 5` | `-0.0770%` | `5 / 5` | `100.0%` |
| `starter_equal_weight` | `target_scale` | `+0.3158%` | `3 / 5` | `-1.1280%` | `4 / 5` | `63.6%` |
| `btc_only` | `entry_guard` | `-0.0253%` | `2 / 5` | `-0.0646%` | `4 / 5` | `100.0%` |
| `btc_only` | `target_scale` | `+0.0410%` | `2 / 5` | `-0.7718%` | `4 / 5` | `63.6%` |
| `alt_equal_weight` | `entry_guard` | `-0.0329%` | `1 / 5` | `-0.0992%` | `5 / 5` | `100.0%` |
| `alt_equal_weight` | `target_scale` | `+0.4077%` | `3 / 5` | `-1.2726%` | `4 / 5` | `63.6%` |

Allocation sensitivity artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-sensitivity-20260530\aggregate.json`

Allocation sensitivity readout:

- Tested `5%`, `20%`, and `50%` allocation.
- `target_scale` stayed average-return positive for starter and alt-basket
  scenarios at all tested allocations.
- `entry_guard` stayed average-return negative at all tested allocations.
- Target-scale was not cash-only: average active cycles stayed `63.6%`.

Lookback sensitivity artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-lookback-20260530\aggregate.json`

Lookback sensitivity readout:

- Tested target-scale at `21`, `42`, and `63` bars with `20%` allocation.
- `63` bars was strongest:
  - `starter_equal_weight`: average return delta `+0.57%`, positive in `4 / 5`,
    drawdown improved in `5 / 5`.
  - `alt_equal_weight`: average return delta `+0.66%`, positive in `4 / 5`,
    drawdown improved in `5 / 5`.
  - `btc_only`: average return delta `+0.31%`, positive in `3 / 5`, drawdown
    improved in `5 / 5`.

Decision:

- Discard `entry_guard` as the leading runtime shape.
- Continue researching `target_scale` as a portfolio target-exposure throttle.
- Do not runtime-wire it yet. The result is promising, but it is synthetic
  exposure evidence rather than actual starter-strategy intent evidence.
- The next serious step is a strategy-like target exposure adapter plus longer
  out-of-sample windows, not a broad parameter grid.

### 2026-05-30 Market Regime Strategy-Proxy Target-Scale Sweep

Implemented:

- `trend_proxy` scenario for `krakked market-regime-exposure-research`
- `krakked market-regime-exposure-sweep`

`trend_proxy` rules:

- Uses cached `4h` OHLC only.
- Computes `63`-bar momentum at each rebalance.
- Requires momentum `>= 150 bps`.
- Ranks eligible starter pairs by momentum.
- Targets the top `4` pairs equally inside the configured allocation.
- Targets cash when no pair qualifies.
- Does not use the market-regime classifier for baseline target selection.

Sweep artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-proxy-sweep-20260530\aggregate.json`

Command:

```bash
poetry run krakked market-regime-exposure-sweep \
  --window-set recent_20d \
  --window-set long_4h \
  --scenario trend_proxy \
  --overlay-mode target_scale \
  --allocation-pct 5 \
  --allocation-pct 20 \
  --target-lookback-bars 63 \
  --min-momentum-bps 150 \
  --max-target-pairs 4 \
  --rebalance-interval-bars 6 \
  --fee-bps 25 \
  --strict-data \
  --save-dir C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-proxy-sweep-20260530
```

Results:

| Window set | Allocation | Avg return delta | Positive windows | Drawdown improved | Min overlay active cycles | Min exposure ratio | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `recent_20d` | `5%` | `+0.0807%` | `4 / 5` | `4 / 5` | `0.0%` | `0.0000` | fail |
| `recent_20d` | `20%` | `+0.3221%` | `4 / 5` | `4 / 5` | `0.0%` | `0.0000` | fail |
| `long_4h` | `5%` | `+0.1375%` | `5 / 6` | `4 / 6` | `3.3%` | `0.1268` | fail |
| `long_4h` | `20%` | `+0.5477%` | `5 / 6` | `5 / 6` | `3.3%` | `0.1265` | fail |

Readout:

- Return and drawdown evidence were directionally good.
- The promotion gate still failed because the overlay did not stay active
  enough and cut exposure too far in the weakest windows.
- Recent `2026-05-10 -> 2026-05-30` had no baseline trend-proxy exposure, so it
  could not support a runtime-throttle conclusion.
- The long set exposed the same issue less severely: January/February had only
  `3.3%` overlay active cycles and about `12.7%` of baseline average exposure.

Decision:

- Do not runtime-wire target-scale.
- The market-regime classifier is still useful as an operator-facing market
  state and as research input.
- Runtime throttling needs either a less sparse target source or a deliberately
  softer scaling rule before it is worth testing again.

### 2026-05-30 Dense Trend-Rank Proxy Follow-Up

Implemented:

- `trend_rank_proxy` scenario for `krakked market-regime-exposure-research`
- Existing `market-regime-exposure-sweep` support for the new scenario

`trend_rank_proxy` rules:

- Uses cached `4h` OHLC only.
- Ranks starter pairs by momentum using up to the configured lookback.
- Starts after a two-bar warmup instead of waiting for the full `63` bars.
- Does not require positive absolute momentum.
- Targets the top `4` pairs equally inside the configured allocation.
- Does not use the market-regime classifier for baseline target selection.

Primary sweep artifacts:

- Hard scale:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-sweep-20260530\aggregate.json`
- Soft scale:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-soft-scale-sweep-20260530\aggregate.json`

Hard-scale result, `neutral=0.5`, `risk_off=0.0`:

| Window set | Allocation | Avg return delta | Positive windows | Drawdown improved | Min overlay active cycles | Min exposure ratio | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `recent_20d` | `5%` | `+0.1609%` | `4 / 5` | `5 / 5` | `49.6%` | `0.2606` | fail |
| `recent_20d` | `20%` | `+0.6441%` | `4 / 5` | `5 / 5` | `49.6%` | `0.2606` | fail |
| `long_4h` | `5%` | `+0.1087%` | `4 / 6` | `4 / 6` | `36.5%` | `0.1892` | fail |
| `long_4h` | `20%` | `+0.4320%` | `4 / 6` | `4 / 6` | `36.5%` | `0.1891` | fail |

Soft-scale result, `neutral=0.75`, `risk_off=0.25`:

| Window set | Allocation | Avg return delta | Positive windows | Drawdown improved | Min overlay active cycles | Min exposure ratio | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `recent_20d` | `5%` | `+0.1111%` | `3 / 5` | `5 / 5` | `95.0%` | `0.5106` | pass |
| `recent_20d` | `20%` | `+0.4436%` | `3 / 5` | `5 / 5` | `95.0%` | `0.5106` | pass |
| `long_4h` | `5%` | `+0.0795%` | `3 / 6` | `4 / 6` | `96.7%` | `0.4391` | fail |
| `long_4h` | `20%` | `+0.3125%` | `3 / 6` | `4 / 6` | `96.7%` | `0.4389` | fail |

Adjacent soft-scale checks:

- `neutral=0.80`, `risk_off=0.35`: recent passed, long failed with `3 / 6`
  positive return windows.
- `neutral=0.85`, `risk_off=0.50`: recent passed, long failed with `3 / 6`
  positive return windows.

Readout:

- The denser rank-only source fixed the obvious cash-only failure from
  `trend_proxy`.
- Hard zero-exposure risk-off scaling still cuts too deeply.
- Softer scaling fixes exposure quality and recent-window behavior, but long
  out-of-sample breadth remains short of the `4 / 6` promotion gate.

Decision:

- Do not runtime-wire the market-regime target-scale overlay.
- Keep `trend_rank_proxy` as a research-only target-source scenario.
- The next research pass should improve signal quality before trying more
  runtime-adjacent wiring.

### 2026-05-30 Signal-Quality Concentration Pass

Research finding:

- `trend_rank_proxy --max-target-pairs 4` was too broad for the current starter
  universe because it usually selected all four configured pairs.
- The target source needed concentration before more formula work.

Follow-up command shape:

```bash
poetry run krakked market-regime-exposure-sweep \
  --window-set recent_20d \
  --window-set long_4h \
  --scenario trend_rank_proxy \
  --overlay-mode target_scale \
  --allocation-pct 5 \
  --allocation-pct 20 \
  --target-lookback-bars 63 \
  --max-target-pairs 2 \
  --rebalance-interval-bars 6 \
  --fee-bps 25 \
  --neutral-allocation-multiplier 0.75 \
  --risk-off-allocation-multiplier 0.25 \
  --strict-data \
  --save-dir C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-top2-soft-scale-sweep-20260530
```

Artifacts:

- Top 1:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-top1-soft-scale-sweep-20260530\aggregate.json`
- Top 2:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-top2-soft-scale-sweep-20260530\aggregate.json`
- Top 3:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-top3-soft-scale-sweep-20260530\aggregate.json`

Top 2 result:

| Window set | Allocation | Avg return delta | Positive windows | Drawdown improved | Min overlay active cycles | Min exposure ratio | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `recent_20d` | `5%` | `+0.1285%` | `4 / 5` | `5 / 5` | `95.0%` | `0.5105` | pass |
| `recent_20d` | `20%` | `+0.5144%` | `4 / 5` | `5 / 5` | `95.0%` | `0.5105` | pass |
| `long_4h` | `5%` | `+0.1337%` | `4 / 6` | `4 / 6` | `96.7%` | `0.4389` | pass |
| `long_4h` | `20%` | `+0.5356%` | `4 / 6` | `4 / 6` | `96.7%` | `0.4388` | pass |

Decision:

- Top 1, top 2, and top 3 concentrated rank variants all passed the existing
  research promotion gate under soft target-scale.
- Top 2 is the preferred candidate because it avoids single-asset concentration
  while still fixing the broad equal-weight problem.
- This is enough evidence to plan a runtime risk-throttle slice, but not enough
  to enable runtime behavior in this pass.

### 2026-05-30 Gate 2 Runtime-Throttle Replay Proof

Gate 2 adds the operator-facing comparison path needed before considering any
runtime enablement:

```bash
poetry run krakked market-regime-throttle-backtest \
  --start <iso> \
  --end <iso> \
  --strict-data \
  --json
```

The command runs a baseline replay with the runtime throttle disabled and a
second replay with the default-disabled throttle forced on. Both runs use the
normal offline strategy, risk, order router, OMS, and simulation path. This is
not the research-only post-plan overlay.

The report is intentionally framed as research evidence. It records real
strategy actions and fills, data readiness, execution errors, replay trust
level, runtime throttle intervention counts, and the regime reason codes that
caused any target-scale reductions.

Decision:

- Keep `risk.market_regime_throttle.enabled: false` by default.
- Use the Gate 2 command to prove actual strategy-intent behavior on rolling
  windows before any operator considers enabling the throttle.
- Do not treat a passing Gate 2 report as live/paper approval by itself.

### 2026-05-30 Strategy Activity Sweep And Gate 2 Rerun

The current rolling Gate 2 window (`2026-05-10 -> 2026-05-30`) had ready data
but produced `0` actions and `0` fills, so it could not prove throttle behavior.
The follow-up diagnostic added:

```bash
poetry run krakked strategy-activity-sweep \
  --window-set recent_20d \
  --window-set long_4h \
  --strict-data \
  --save-dir C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\strategy-activity-sweep-20260530
```

Artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\strategy-activity-sweep-20260530\aggregate.json`

Activity result:

- Configured pack (`trend_core`, `majors_mean_rev`): ready `8 / 11`, actions
  `4 / 11`, fills `4 / 11`.
- `trend_core`: same activity as the configured pack, so it is the active
  source.
- `majors_mean_rev`: ready `8 / 11`, but `0 / 11` action/fill windows.
- `vol_breakout` and starter-all: blocked by missing `15m` replay coverage in
  all 11 windows.

Gate 2 was then rerun on every action/fill window from the diagnostic:

| Window | Baseline actions | Baseline fills | Throttle interventions | Gate 2 |
| --- | ---: | ---: | ---: | --- |
| `2026-03-21 -> 2026-04-10` | `76` | `2` | `0` | pass |
| `2026-04-10 -> 2026-04-30` | `12` | `4` | `6` | pass |
| `2026-03-21 -> 2026-04-20` | `737` | `9` | `0` | pass |
| `2026-04-20 -> 2026-05-20` | `388` | `4` | `21` | pass |

Representative report:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-throttle-backtest-active-window-20260530.json`

Decision:

- Gate 2 runtime plumbing is proven against real strategy intents.
- The current rolling default replay is still not promotion evidence because
  the configured pack emitted no intents in that window.
- Runtime throttle remains default-disabled pending an operator decision on
  whether passing historical action windows are enough evidence for paper-only
  enablement, or whether current-window activity must return first.

### 2026-05-30 Fresh-Bar Hygiene And trend_core Signal Quality

The current-window inactivity gap was partly a replay hygiene issue: multi-
timeframe strategies were being evaluated on every replay timestamp even when a
given strategy timeframe had no new closed bar. The strategy engine now records
the latest evaluated bar timestamp per strategy/timeframe and skips duplicate
timeframe contexts until a fresh bar appears.

Current rolling proof window:

- `2026-05-10T00:00:00Z -> 2026-05-30T00:00:00Z`

Artifacts:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\strategy-action-diagnostics-trend-core-current-freshgate-20260530.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\trend-core-signal-quality-current-20260530.json`
- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\backtest-current-freshgate-20260530.json`

Fresh-bar replay impact:

| Metric | Before fresh gate | After fresh gate |
| --- | ---: | ---: |
| Actions | `314` | `204` |
| Intents emitted | `2175` | `1344` |
| Actions after scoring | `414` | `228` |
| Skipped stale timeframe contexts | n/a | `360` |
| Fills | `8` | `8` |
| Return | `-0.6040%` | `-0.6040%` |

The replay is now more truthful: it no longer counts stale 4h evaluations as
new signal work on every 1h cycle. It still does not prove strategy quality.

Signal-quality result:

- Fresh-bar trend_core signals: `228`.
- 6-bar mean forward return: `-0.8007%`.
- 6-bar median forward return: `-0.7005%`.
- 6-bar hit rate: `26.3%`.
- 1h signals: `166`, 6-bar mean `-0.7555%`, hit rate `22.9%`.
- 4h signals: `62`, 6-bar mean `-0.9217%`, hit rate `35.5%`.
- Stronger trend-strength bucket still did not outperform the weakest bucket.

Decision:

- Keep the fresh-bar strategy evaluation gate.
- Keep `trend_core` as unpromoted research evidence, not a strategy-quality
  candidate.
- Do not loosen caps, enable market-regime runtime throttle by default, or
  promote a cap-aligned proxy from this result.
- The next strategy-quality work should improve the target source itself, not
  continue wrapping the current trend_core signal.

### 2026-05-30 Target-Source Research Harness

Purpose:

- Move away from tuning the current `trend_core` target source and test explicit
  target-weight adapters directly against cached `4h` OHLC.
- Keep the work research-only: no runtime config, strategy defaults, risk
  behavior, order routing, paper/live execution, or operator UI behavior change.

Command shape:

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
  --save-dir C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\target-source-research-20260530
```

Gate:

- A target source must beat `rank_top2` on average return and average max
  drawdown in each requested window set.
- It must be positive or near-flat in at least `3 / 5` recent windows and
  `4 / 6` long windows.
- The current rolling `2026-05-10 -> 2026-05-30` window must not be a clear
  negative outlier at 20 percent allocation.
- Average exposure must remain adequate unless the scenario is explicitly
  defensive-only.
- Strict data must pass and reports must state `research_only=true` and
  `runtime_wiring_approved=false`.

Verification artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\target-source-research-20260530\aggregate.json`

Run result:

- Strict data passed for every requested window.
- Reports written: `132` (`2` window sets, `11` windows, `6` scenarios, `2`
  allocations).
- Candidate scenarios at the primary 20 percent allocation: none.

20 percent allocation summary:

| Window set | Scenario | Avg return | Avg max DD | Near-flat windows | Gate |
| --- | --- | ---: | ---: | ---: | --- |
| recent_20d | rank_top2 | `-1.0187%` | `2.7242%` | `0 / 5` | fail |
| recent_20d | dual_momentum_top2 | `-0.4598%` | `0.8431%` | `3 / 5` | fail |
| recent_20d | vol_adj_dual_momentum_top2 | `-0.3740%` | `0.7983%` | `3 / 5` | fail |
| recent_20d | pullback_vol_adj_top2 | `-0.3387%` | `0.7895%` | `3 / 5` | fail |
| recent_20d | oversold_reversion_top1 | `-0.2250%` | `1.3590%` | `2 / 5` | fail |
| recent_20d | hybrid_state_source | `-0.9374%` | `1.8715%` | `1 / 5` | fail |
| long_4h | rank_top2 | `-1.7436%` | `3.5772%` | `1 / 6` | fail |
| long_4h | dual_momentum_top2 | `-0.8388%` | `1.8207%` | `2 / 6` | fail |
| long_4h | vol_adj_dual_momentum_top2 | `-0.6685%` | `1.7685%` | `2 / 6` | fail |
| long_4h | pullback_vol_adj_top2 | `-0.2718%` | `1.5460%` | `3 / 6` | fail |
| long_4h | oversold_reversion_top1 | `-0.0433%` | `2.0533%` | `4 / 6` | pass |
| long_4h | hybrid_state_source | `-0.6456%` | `2.8276%` | `1 / 6` | fail |

Decision:

- The source edge is not currently proven. Do not wire any of these sources into
  runtime strategy/risk behavior from this evidence alone.
- The top-2 momentum baseline remains useful as a comparison baseline, not as a
  candidate.
- The defensive oversold source is the only scenario with a partial pass: it
  passed the long-window gate at 20 percent, but did not pass the recent-window gate
  and therefore remains operator/research evidence only.
- 5 percent allocation is scale-sensitivity evidence only; it is intentionally
  not a promotion candidate.

### 2026-05-30 Target-Source Loss Decomposition

The target-source harness now writes per-rebalance traces and diagnostic failure
buckets for each run. Trace rows include timestamp, selected pairs, per-pair
scores/features, target weights, cash targeting, equity/exposure before and
after rebalance, fees, next-rebalance forward returns, and selected-vs-best
forward-return gaps.

Updated artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\target-source-research-20260530\aggregate.json`

20 percent decomposition summary:

| Scenario | Avg selected-vs-best next-rebalance gap | Avg cash target rebalances | Avg fee drag | Hidden pair-edge windows |
| --- | ---: | ---: | ---: | ---: |
| rank_top2 | `-1.0437%` | `3.9%` | `0.4671%` | `7` |
| dual_momentum_top2 | `-1.3580%` | `77.4%` | `0.2502%` | `2` |
| vol_adj_dual_momentum_top2 | `-1.2642%` | `77.4%` | `0.2504%` | `1` |
| pullback_vol_adj_top2 | `-1.1594%` | `79.6%` | `0.3194%` | `1` |
| oversold_reversion_top1 | `-1.0296%` | `76.6%` | `0.5913%` | `7` |
| hybrid_state_source | `-1.1327%` | `60.6%` | `0.6823%` | `9` |

Diagnosis:

- The weak readout is not driven by one thing. Momentum-like sources often improved
  drawdown versus `rank_top2`, but they mostly did it by going cash too often;
  the sparse exposure then left too little edge to overcome fees and bad picks.
- `rank_top2` stayed active, but frequently held the wrong pair mix and showed
  slow-exit/negative-momentum holds in losing windows.
- The defensive/oversold family exposed possible pair-level edges in isolated
  windows, but allocation timing was not good enough: selected pairs still
  trailed the best available pair by about one percentage point per rebalance
  on average.
- Hidden pair-edge windows mean the universe sometimes contains useful
  single-pair behavior, but the tested allocation rules are too crude to
  harvest it reliably.

Decision:

- Runtime wiring remains unsupported by the current evidence.
- Do not spend the next source pass on more top-N momentum variants unless the
  hypothesis changes.
- If strategy-source work continues, the next source should be pair-local first:
  score each pair's own setup/exit quality and only allocate after that edge is
  proven, instead of ranking weak global momentum snapshots.

### 2026-05-30 Pair-Local Source Proof Gate

Purpose:

- Answer the current strategy-source question: does any individual starter pair
  have repeatable setup/exit behavior that survives fees, drawdown, recent and
  long windows, and the current rolling window?

Command:

```bash
poetry run krakked pair-local-source-research \
  --window-set recent_20d \
  --window-set long_4h \
  --scenario pair_dual_momentum \
  --scenario pair_vol_adj_momentum \
  --scenario pair_trend_pullback \
  --scenario pair_oversold_reversion \
  --scenario pair_breakout_continuation \
  --allocation-pct 20 \
  --allocation-pct 5 \
  --timeframe 4h \
  --rebalance-interval-bars 6 \
  --fee-bps 25 \
  --strict-data \
  --save-dir C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\pair-local-source-research-20260530
```

Artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\pair-local-source-research-20260530\aggregate.json`

Result:

- Strict data passed for every requested window.
- Reports written: `110` (`2` window sets, `11` windows, `5` scenarios, `2`
  allocations).
- `promote_pair_local_source=false`
- `runtime_wiring_approved=false`

Best 20 percent slices:

| Window set | Pair | Scenario | Avg return | Near-flat windows | Avg max DD | Gate |
| --- | --- | --- | ---: | ---: | ---: | --- |
| long_4h | SOL/USD | pair_trend_pullback | `+0.3069%` | `4 / 6` | `0.4431%` | fail |
| recent_20d | ETH/USD | pair_trend_pullback | `+0.1276%` | `4 / 5` | `0.1457%` | fail |
| recent_20d | ETH/USD | pair_oversold_reversion | `+0.1031%` | `3 / 5` | `0.6878%` | pass |
| recent_20d | ETH/USD | pair_breakout_continuation | `+0.0358%` | `3 / 5` | `0.3006%` | fail |
| recent_20d | BTC/USD | pair_trend_pullback | `+0.0291%` | `5 / 5` | `0.0246%` | fail |
| long_4h | ETH/USD | pair_trend_pullback | `+0.0258%` | `4 / 6` | `0.4183%` | fail |

Diagnosis:

- ETH/USD `pair_oversold_reversion` passed the recent-window gate but did not
  pass long-window proof, so it is not currently promotable.
- SOL/USD and ETH/USD trend-pullback showed the most interesting positive
  slices, but their active exposure was too sparse and they missed too much
  pair upside while cash.
- The latest pair-local gate did not find a repeatable source that survives both
  recent and long out-of-sample sets.

Decision:

- Pause strategy-source development for this lane until a new written
  hypothesis exists.
- Do not wire any source into paper runtime from this evidence alone.
- Keep Krakked's near-term work focused on replay/data reliability, operator
  visibility, risk-state reporting, and paper-mode safety/observability unless
  a genuinely new strategy hypothesis is introduced.

### 2026-05-31 Runtime Strategy Evidence Sweep At ML-Cost Proxy

Purpose:

- Check the existing runtime strategies under the same evidence posture used
  for the ML proof pass instead of implying ML alone was uniquely weak.
- Keep this as research evidence only: no runtime config, strategy defaults,
  risk behavior, order routing, paper/live execution, or operator UI behavior
  changed.

Command:

```bash
poetry run krakked strategy-activity-sweep \
  --config reports/ml/ml-baseline-proof-20260531/ml-proof-config.yaml \
  --window-set recent_20d \
  --window-set long_4h \
  --group configured \
  --group starter_all \
  --group trend_core \
  --group vol_breakout \
  --group majors_mean_rev \
  --strategy rs_rotation \
  --starting-cash-usd 10000 \
  --fee-bps 30 \
  --strict-data \
  --save-dir reports/strategy-evidence-sweep-20260531-runtime-30bps \
  --json
```

Notes:

- Runtime replay currently has one fill-cost input, so `--fee-bps 30` is an
  all-in proxy for the ML proof pass' 10 bps fee plus 20 bps slippage.
- Cash remains the primary baseline at `0.0%`.
- Equal-weight buy-and-hold was computed separately over cached `4h` OHLC for
  context. It is not runtime approval.

Result:

| group | ready windows | fill windows | positive ready windows | avg ready return | avg ready max DD | current recent | current long |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| configured | `0 / 11` | `0 / 11` | `0 / 11` | n/a | n/a | data not ready | data not ready |
| starter_all | `0 / 11` | `0 / 11` | `0 / 11` | n/a | n/a | data not ready | data not ready |
| trend_core | `5 / 11` | `5 / 11` | `0 / 5` | `-0.2683%` | `0.6393%` | `-0.6080%` | `-0.3329%` |
| vol_breakout | `0 / 11` | `0 / 11` | `0 / 11` | n/a | n/a | data not ready | data not ready |
| majors_mean_rev | `6 / 11` | `0 / 11` | `0 / 6` | `0.0000%` | `0.0000%` | `0.0000%` | `0.0000%` |
| rs_rotation | `6 / 11` | `6 / 11` | `0 / 6` | `-0.3157%` | `0.4908%` | `-0.2910%` | `-0.3150%` |

Equal-weight buy-and-hold context with the same 30 bps one-way cost:

| basket | recent avg return | recent positive windows | recent avg max DD | long avg return | long positive windows | long avg max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BTC/ETH | `-3.8807%` | `2 / 5` | `10.3009%` | `-4.3960%` | `3 / 6` | `14.4256%` |
| starter four | `-4.3540%` | `1 / 5` | `11.6860%` | `-5.3545%` | `2 / 6` | `15.6744%` |

Decision:

- ML should be described as retained but unpromoted under current evidence, not
  removed or singled out from the rest of the strategy set.
- The same posture applies to the bundled runtime strategies: the active
  trading strategies did not beat cash in any ready/fill window, and inactive
  strategies only matched cash by not trading.
- `trend_core` and `rs_rotation` lost less than equal-weight buy-and-hold
  because they were small/capped and often defensive, not because source edge
  was proven.
- `vol_breakout` and the configured starter pack have a separate evidence
  hygiene problem: the strategy requires `15m` data, but strict replay coverage
  is not maintained for that lane.
- The next fair-comparison improvement is a durable unified strategy evidence
  command/report that includes cash and buy-and-hold baselines directly, rather
  than relying on separate ML and runtime-strategy scoreboards.

### 2026-05-31 Unified Strategy Evidence Scoreboard

Purpose:

- Put ML, starter strategies, disabled research strategies, cash, and
  equal-weight buy-and-hold into one comparable runtime replay context.
- Replace separate hand-stitched ML/runtime comparisons with one report using
  the same cached data rules, synthetic wallet, risk engine, order simulation,
  and fee assumption.

Command:

```bash
poetry run krakked strategy-evidence-scoreboard \
  --config reports/ml/ml-baseline-proof-20260531/ml-proof-config.yaml \
  --window-set recent_20d \
  --window-set long_4h \
  --starting-cash-usd 10000 \
  --fee-bps 30 \
  --strict-data \
  --save-dir reports/strategy-evidence-scoreboard-20260531-runtime-30bps
```

Artifact:

- `C:\Users\Rob\Documents\dev\krakked\reports\strategy-evidence-scoreboard-20260531-runtime-30bps\aggregate.json`

Shared context:

- Same `run_backtest` runtime replay path for every strategy row.
- Same configured risk caps, order router, OMS simulation, synthetic wallet, and
  strict cached-data rule.
- Same 30 bps per-fill cost assumption for strategy replays and equal-weight
  buy-and-hold entry/exit context.

Scoreboard:

| group | ready windows | fill windows | positive ready windows | avg ready return | avg ready max DD | current recent | current long | status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| configured | `0 / 11` | `0 / 11` | `0 / 11` | n/a | n/a | data not ready | data not ready | `data_not_ready` |
| starter_all | `0 / 11` | `0 / 11` | `0 / 11` | n/a | n/a | data not ready | data not ready | `data_not_ready` |
| trend_core | `5 / 11` | `5 / 11` | `0 / 5` | `-0.2683%` | `0.6393%` | `-0.6080%` | `-0.3329%` | `unproven` |
| vol_breakout | `0 / 11` | `0 / 11` | `0 / 11` | n/a | n/a | data not ready | data not ready | `data_not_ready` |
| majors_mean_rev | `6 / 11` | `0 / 11` | `0 / 6` | `0.0000%` | `0.0000%` | `0.0000%` | `0.0000%` | `inactive_or_cash` |
| rs_rotation | `6 / 11` | `6 / 11` | `0 / 6` | `-0.3157%` | `0.4908%` | `-0.2910%` | `-0.3150%` | `unproven` |
| ai_predictor_alt | `6 / 11` | `0 / 11` | `0 / 6` | `0.0000%` | `0.0000%` | `0.0000%` | `0.0000%` | `inactive_or_cash` |
| ai_regression | `6 / 11` | `6 / 11` | `0 / 6` | `-0.6222%` | `0.6233%` | `-0.7278%` | `-0.8452%` | `unproven` |

Baselines:

- Cash: `0.0000%` return, `0.0000%` max drawdown.
- Equal-weight buy-and-hold over the starter universe: usable `11 / 11`,
  average return `-4.8997%`, average max drawdown `13.8615%`.

Decision:

- The earlier ML walk-forward result is useful ML diagnostics, but it is not a
  sufficient cross-strategy verdict by itself.
- In the unified runtime replay scoreboard, ML is not uniquely disqualified;
  it is one unproven strategy row among several unproven or inactive rows.
- No configured strategy row has enough evidence to promote runtime behavior
  today.
- The honest near-term product posture remains: retain ML and bundled
  strategies as research/investigation infrastructure, and use the unified
  scoreboard for future evidence claims.
