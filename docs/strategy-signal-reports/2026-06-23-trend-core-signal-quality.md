# trend_core Signal Quality Report: 2026-06-23

Date: 2026-06-23 PDT
Scope: one-shot `trend_core` falsification across `regime_diverse_4h`
Mode: research-only, cached OHLC evidence

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
  against an unconditional/random-entry baseline over the same bars and horizon.
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

## Cost Model

The CLI option remains `--fee-bps` for compatibility. In this module it is now
reported as a one-way all-in cost proxy:

- `one_way_all_in_cost_bps`: `25.0`
- `round_trip_all_in_cost_bps`: `50.0`
- `round_trip_all_in_cost_pct`: `0.5`
- Backward-compatible aliases remain: `fee_bps` and
  `round_trip_fee_hurdle_pct`

Cost note from the report:

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

The result supports moving the next research lane to genuinely new information
such as funding rates, perp-spot or perp-index basis, and defensive risk sizing
features. Continue treating current directional OHLC strategies on the four
majors as research-only unless a later predeclared evidence lane overturns this
verdict.
