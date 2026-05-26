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
