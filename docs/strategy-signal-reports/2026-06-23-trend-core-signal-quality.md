# trend_core Signal Quality Report: 2026-06-23

Date: 2026-06-23 PDT
Scope: one-shot `trend_core` falsification across `regime_diverse_4h`
Mode: research-only, cached OHLC evidence

> **Re-run update (PR858, 2026-06-24):** the numbers in the original sections
> below predate the corrected harness. Re-running the same command through the
> PR857 harness **confirms** `edge_not_proven` — now on a drift-controlled
> (baseline-controlled) measurement rather than a charitable diagnostic. See
> [PR858 Re-run](#pr858-re-run-2026-06-24-baseline-controlled-result) for the
> authoritative result and decision.

## Verdict

`trend_core` did not pass the predeclared regime-consistency bar.

The run produced regime-diverse evidence, but none of the strict-ready
evaluable windows passed the signal-quality gate:

- Window set: `regime_diverse_4h`
- Windows reported: `6`
- Strict-ready evaluable windows: `3`
- Passing evaluable windows: `0`
- K/N result: `0 / 3`
- Regime bucket counts: `uptrend=2`, `downtrend=1`,
  `chop_or_transition=2`, `current_rolling=1`
- Regime coverage sufficient: `true`
- Aggregate status: `edge_not_proven`
- Lane verdict: `retire_directional_ohlc_on_majors_for_now`

Decision: retire the directional-OHLC-on-majors lane for now, including
`trend_core`, `majors_mean_rev`, and `rs_rotation`. This is not a live strategy
promotion request, and no runtime strategy, allowlist, risk, execution, schema,
or UI behavior changed.

## Methodology Correction (PR856)

A code review after this run found that the harness that produced these numbers
is a **charitable, drift-uncontrolled diagnostic**, not the baseline-controlled
falsification the framing above implies. The retirement verdict still holds —
every measurement bias below inflates the signal, and `trend_core` failed anyway
— but the rigor claims must be read with these corrections:

- **No unconditional baseline.** Forward returns were judged against a fixed
  round-trip fee hurdle plus a within-window trend-strength quartile delta, not
  against an unconditional all-bars baseline over the same bars and horizon.
  "Positive after fees" here cannot distinguish real timing edge from market
  drift.
- **Same-bar-close entry.** Entries were filled at the signal bar's own close —
  the bar the signal was computed from — not the next bar's open, biasing
  forward returns upward by one bar of momentum.
- **Adverse excursion was evidence-only.** Max adverse excursion was reported
  but never gated the verdict.
- **Strict-data was not enforced per window.** The window-set path computed and
  tabled stats for partial windows; `--strict-data` only annotated coverage, it
  did not abort.
- **Consistency was N-of-N (unanimous), not a tunable K-of-N.** "K/N = 0/3" is
  numerically true, but the implemented bar requires every evaluable window to
  pass.
- **Lane retirement is an inference.** Only `trend_core` was measured;
  `majors_mean_rev` and `rs_rotation` are retired by inference (trend_core is the
  strongest directional-OHLC candidate and prior evidence is negative), not by
  direct measurement in this run.

As of PR856 the tool can no longer emit a promotable verdict: a heuristic pass is
surfaced as `diagnostic_candidate_unverified` with `promotion_ready=false` and
`promotion_blocked_reason="baseline_control_not_implemented"`. A
baseline-controlled, next-bar-open measurement harness (PR857) is required before
any future `candidate` verdict can be trusted.

**Update (PR857):** that harness now exists — next-bar-open entry, exact-horizon
exit, an unconditional all-bars baseline, and a fee + slippage round-trip cost. A
signal can now earn `candidate_signal` only by beating the baseline by at least
the round-trip cost (and `promotion_ready` still stays `false` pending
out-of-sample validation). The numbers in this report predate the corrected
harness; a re-run of `trend_core` through it (PR858) supersedes them — see below.

## PR858 Re-run (2026-06-24): Baseline-Controlled Result

Re-running the same `regime_diverse_4h` command through the corrected PR857
harness (next-bar-open entry, exact-horizon exit, unconditional all-bars
baseline, fee + slippage round-trip cost) **confirms the verdict**. The evaluable
windows are now baseline-controlled, so the verdict rests on a drift-controlled
measurement rather than a charitable diagnostic. The aggregate *also* still fails
on strict-data coverage — two `chop_or_transition` windows are non-evaluable, so
regime coverage is incomplete — but that gap fails closed: it can only block a
pass, never manufacture one.

- Aggregate status: `edge_not_proven`
- `baseline_controlled`: `true`
- K/N result: `0 / 2` evaluable windows pass (pre-registered N-of-N rule)
- Regime coverage sufficient: `false` (evaluable buckets cover only `uptrend` and
  `downtrend`; both `chop_or_transition` windows are non-evaluable on strict data)
- Lane verdict: `retire_directional_ohlc_on_majors_for_now`
- Round-trip cost: `0.50%` (`--fee-bps 25`, `--slippage-bps 0`); required edge
  over baseline: `0.50%`

Two things changed materially versus the original PR855 run, and both come from
the corrected harness:

1. **The warmup-aware strict-data fix (PR857) dropped a window.** The original
   run counted 3 strict-ready evaluable windows; the corrected run counts 2. The
   large uptrend window `20251221-20260120` (257 signals) now fails strict data
   because its **indicator warmup** coverage is partial across all four pairs and
   timeframes — a gap the old narrow check ignored. It is correctly non-evaluable.
2. **The baseline exposes drift the old harness could not see.** That same uptrend
   window had an apparently healthy `+0.51%` mean forward return, but the
   unconditional all-bars baseline over the same window earned `+0.19%`. The
   signal's edge over indiscriminate entry is only `+0.32%` — below the `0.50%`
   round-trip cost. Most of the apparent edge was market drift, exactly the
   failure mode the PR855 harness could not detect.

Per-window primary-horizon (6-bar) detail (entry at next-bar open, exact horizon,
scored against the all-bars baseline; `n=700` baseline samples per non-current
window):

| Window | Bucket | Evaluable | Signals | Mean | Net mean | Baseline mean | Signal − baseline | Status |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `20251221-20260120` | uptrend | no (warmup gap) | 257 | +0.510% | +0.010% | +0.192% | +0.318% | edge_not_proven |
| `20260120-20260219` | downtrend | yes | 6 | +1.155% | +0.655% | −1.132% | +2.287% | edge_not_proven |
| `20260219-20260321` | uptrend | yes | 286 | −0.705% | −1.205% | +0.250% | −0.955% | edge_not_proven |
| `20260321-20260420` | chop_or_transition | no (1h gap) | 262 | −0.086% | −0.586% | +0.052% | −0.138% | edge_not_proven |
| `20260420-20260520` | chop_or_transition | no (1h gap) | 264 | +0.108% | −0.392% | −0.047% | +0.155% | edge_not_proven |
| `20260510-20260530` | current_rolling | no (current) | 60 | −0.963% | −1.463% | −0.715% | −0.247% | edge_not_proven |

The two evaluable windows fail decisively. The downtrend window has only 6
primary-horizon samples (overlap-adjusted floor ≈ 1) and a 50% hit rate; the
`20260219-20260321` uptrend window is net-negative after cost and *underperforms*
the baseline by `−0.95%`. No window clears the net-positive, beats-baseline-by-
cost, hit-rate, and trend-strength-monotonicity checks together.

### Decision

The corrected, baseline-controlled harness **confirms** the directional-OHLC-on-
majors lane is not productive. Stop tuning `trend_core`, `majors_mean_rev`, and
`rs_rotation` against OHLC-only major-pair data. Keep them research/paper-only as
diagnostics and event generators, but do not spend further cycles polishing
directional signals from the same four-majors candle data. The next research lane
is genuinely new information (funding rates, perp-spot / perp-index basis) and
defensive risk sizing — not another pass over the same OHLC.

## Command

```bash
poetry run krakked trend-core-signal-quality \
  --window-set regime_diverse_4h \
  --pair BTC/USD \
  --pair ETH/USD \
  --pair SOL/USD \
  --pair ADA/USD \
  --timeframe 4h \
  --forward-horizon-bars 1 \
  --forward-horizon-bars 3 \
  --forward-horizon-bars 6 \
  --fresh-bars-only \
  --strict-data \
  --save-report reports/trend-core-signal-quality-20260623/regime-diverse-4h.json
```

Local JSON artifact:
`reports/trend-core-signal-quality-20260623/regime-diverse-4h.json`

The JSON artifact is intentionally under ignored `reports/`; this dated markdown
is the durable summary.

## Original PR855 Run Cost Model

This describes the cost model of the **original PR855 run** that produced the
numbers above. It had no separate slippage field: `--fee-bps` was used as a
one-way all-in cost proxy with a round-trip hurdle. **PR857 supersedes the
harness** with an explicit `--slippage-bps` input, so the round-trip cost is now
`2 * (fee_bps + slippage_bps)`; the figures below are historical.

- `one_way_all_in_cost_bps`: `25.0`
- `round_trip_all_in_cost_bps`: `50.0`
- `round_trip_all_in_cost_pct`: `0.5`
- Backward-compatible aliases remain: `fee_bps` and
  `round_trip_fee_hurdle_pct`

Cost note emitted by the original run:

> fee_bps is used as a one-way all-in cost proxy; no separate slippage model is
> applied in this module.

## Window Results

Primary horizon: `6` bars.

| Window | Bucket | Strict-ready | Evaluable | Signals | 6-bar mean | Hit rate | Fee-hit | Mean adverse | Max adverse | Status |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `20251221-20260120` | uptrend | yes | yes | 257 | +0.5102% | 60.3% | 54.5% | 1.7805% | 8.6425% | edge_not_proven |
| `20260120-20260219` | downtrend | yes | yes | 6 | +1.1797% | 50.0% | 50.0% | 2.8172% | 7.0049% | edge_not_proven |
| `20260219-20260321` | uptrend | yes | yes | 286 | -0.7025% | 40.2% | 34.3% | 2.8988% | 10.0542% | edge_not_proven |
| `20260321-20260420` | chop_or_transition | no | no | 262 | -0.0864% | 48.4% | 41.9% | 2.1271% | 6.8484% | edge_not_proven |
| `20260420-20260520` | chop_or_transition | no | no | 264 | +0.1079% | 48.9% | 36.0% | 1.5871% | 6.0283% | edge_not_proven |
| `20260510-20260530` | current_rolling | no | no | 60 | -0.9618% | 33.3% | 21.7% | 2.3511% | 6.0283% | edge_not_proven |

Gate reasons:

- `20251221-20260120`: stronger trend-strength bucket did not outperform the
  weakest bucket.
- `20260120-20260219`: only six primary-horizon samples, hit rate below 55%,
  and stronger trend-strength bucket did not outperform the weakest bucket.
- `20260219-20260321`: mean forward return did not clear the all-in round-trip
  hurdle, median forward return was not positive, hit rate was below 55%, and
  stronger trend-strength bucket did not outperform the weakest bucket.
- `20260321-20260420`: non-current strict-data gap due partial `1h` series for
  all four starter pairs.
- `20260420-20260520`: non-current strict-data gap due partial `1h` series for
  all four starter pairs.
- `20260510-20260530`: current rolling window, reported but excluded from the
  regime-consistency gate.

Aggregate gate reasons:

- one or more non-current regime windows failed strict data coverage.
- 3 evaluable windows failed signal-quality gates.

## Interpretation

This is a one-shot falsification result, not another invitation to tune
`trend_core`. The strongest current directional OHLC starter did not show
consistent fee-aware forward-return evidence across the regime mix. Two
non-current chop windows also failed the strict-data requirement because their
supporting `1h` series are partial.

The original PR855 verdict above was reached with an upward-biased harness
(same-bar-close entry, horizon stretching, no drift control), and the **PR858
re-run through the corrected baseline-controlled harness confirms it** rather than
overturning it — the apparent positives in the original numbers were largely
market drift, and the corrected entry/horizon/baseline logic only widens the gap
between the signal and a profitable bar.

The result supports moving the next research lane to genuinely new information
such as funding rates, perp-spot or perp-index basis, and defensive risk sizing
features. `trend_core`, `majors_mean_rev`, and `rs_rotation` stay research/paper-
only as diagnostics and event generators; do not spend further cycles polishing
directional signals from OHLC-only major-pair data unless a later predeclared
evidence lane overturns this verdict.

Start that lane with the public-data feasibility probe:

```bash
poetry run krakked funding-basis-feasibility \
  --pair BTC/USD \
  --pair ETH/USD \
  --pair SOL/USD \
  --pair ADA/USD \
  --start 2025-12-01T00:00:00Z \
  --end 2026-06-21T20:00:00Z \
  --window-set regime_diverse_4h \
  --timeframe 4h \
  --save-report reports/funding-basis-feasibility.json \
  --json
```

Only scope historical backfill if the report proves point-in-time historical
usability. If publish timing is unknown, the next honest step is forward
collection; if Kraken public data is incomplete, stop the Kraken-only lane or
evaluate another source.
