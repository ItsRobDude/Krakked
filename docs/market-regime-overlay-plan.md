# Market Regime Overlay Decision And Plan

## Decision

Do not continue `rs_rotation` or `rs_rotation_v2` as standalone trading
strategies.

The surviving idea is a portfolio-level defensive market-state overlay. It
should be researched as a risk/exposure control that can reduce or block new
risk during weak market regimes, not as another pair-ranking strategy.

This decision is based on the `2026-05-30` multi-window `rs_rotation_v2` sweep:

- default v2 averaged `-0.1154%`, was positive in `1/5` windows, and passed
  `1/5` windows;
- `864` current-cost parameter-grid variants produced `0` positive average
  return variants;
- `0 / 864` variants passed at least `3 / 5` windows;
- the only mildly useful behavior was defensive cash behavior during broad weak
  windows.

## What This Is

The market regime overlay is a portfolio-level state machine:

- `risk_on`: allow normal starter strategy behavior.
- `neutral`: allow exits and reduce new exposure targets.
- `risk_off`: allow exits and flattening, but block new opening risk.

It should produce clear reason codes and quantitative inputs so operators can
understand why exposure was reduced.

## What This Is Not

- Not a runtime change yet.
- Not a replacement `rs_rotation` strategy.
- Not another raw relative-strength ranking pass.
- Not a live-trading permission change.
- Not a change to existing strategy signal generation.
- Not a reason to loosen risk caps.

## Research-First Implementation Plan

### 1. Add A Cache-Only Research Evaluator

Add a new backtest/research module that reads cached OHLC only and labels each
replay cycle with a market state.

Inputs:

- configured starter universe, defaulting to `BTC/USD`, `ETH/USD`, `SOL/USD`,
  and `ADA/USD`;
- benchmark pair, defaulting to `BTC/USD`;
- timeframe, defaulting to `4h`;
- lookback windows for absolute momentum, basket momentum, realized volatility,
  and drawdown.

Outputs per cycle:

- timestamp;
- regime: `risk_on`, `neutral`, or `risk_off`;
- allocation multiplier: `1.0`, `0.5`, or `0.0`;
- reason codes, for example:
  - `btc_momentum_negative`
  - `basket_momentum_negative`
  - `btc_drawdown_exceeded`
  - `volatility_spike`
  - `insufficient_data`
- raw feature values used to make the decision.

### 2. Add A Research CLI

Add:

```bash
krakked market-regime-research \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --json \
  --save-report market-regime-research.json
```

Required behavior:

- cache-only, no Kraken network calls;
- strict-data option matching replay behavior;
- structured JSON report;
- nonzero exit on unsupported timeframe, missing strict-data coverage, or report
  write failure;
- no `--publish-latest` path until the report has a UI consumer.

### 3. Add Overlay Replay Comparison

The first useful proof is not whether the regime classifier looks plausible in
isolation. It is whether applying it to the starter pack improves replay
behavior.

Research command shape:

```bash
krakked market-regime-overlay-backtest \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --json \
  --save-report market-regime-overlay-backtest.json
```

Simulation rules:

- `risk_on`: no change to generated strategy intents.
- `neutral`: allow exits/flattening; reduce new or increased desired exposure
  by the configured multiplier, initially `0.5`.
- `risk_off`: allow exits/flattening; block new entries and increases.
- Never block risk-reducing actions.
- Record every overlay block or clamp separately from existing risk-engine block
  and clamp reasons.

The comparison report must include both baseline and overlay results:

- ending equity;
- return;
- max drawdown;
- filled orders;
- blocked actions;
- clamped actions;
- overlay-blocked actions;
- overlay-clamped actions;
- per-strategy PnL;
- risk-state cycle counts;
- top overlay reason counts.

### 4. Multi-Window Promotion Gate

Use the same five windows as the `rs_rotation_v2` sweep before considering any
runtime wiring:

- `2026-03-21T00:00:00+00:00 -> 2026-04-10T00:00:00+00:00`
- `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00`
- `2026-04-30T00:00:00+00:00 -> 2026-05-20T00:00:00+00:00`
- `2026-05-05T00:00:00+00:00 -> 2026-05-25T00:00:00+00:00`
- `2026-05-10T00:00:00+00:00 -> 2026-05-30T00:00:00+00:00`

Promotion candidate criteria:

- improves or preserves average return after costs;
- improves max drawdown in at least `3 / 5` windows;
- does not reduce ending equity by more than `0.05%` in more than one window;
- does not turn a decision-helpful replay into a weak-signal replay;
- gives non-empty, operator-readable reason codes for at least `95%` of overlay
  interventions;
- no execution errors;
- no missing or partial strict-data coverage.

Hard fail criteria:

- average return worsens and max drawdown does not improve;
- profitable windows are mostly flattened into cash;
- overlay blocks exits or flattening;
- intervention reasons are vague or missing;
- fewer than `3 / 5` windows show a drawdown benefit;
- the overlay only helps by avoiding all trading.

### 5. Runtime Wiring Only After Research Passes

If the overlay passes the multi-window gate, wire it as a risk/execution guard,
not as strategy logic.

Runtime integration principles:

- strategies continue to emit intents normally;
- overlay decisions are applied after strategy intent generation and before
  order routing;
- exits and emergency flattening remain allowed;
- live order gates remain unchanged;
- all overlay interventions are persisted and visible in operator summaries;
- a config flag must keep the overlay disabled until explicitly enabled.

Suggested future config shape:

```yaml
risk:
  market_regime_overlay:
    enabled: false
    timeframe: "4h"
    benchmark_pair: "BTC/USD"
    risk_off_allocation_multiplier: 0.0
    neutral_allocation_multiplier: 0.5
```

Do not add this runtime config until the research report justifies it.

## Initial Test Plan

Unit tests:

- classifier emits `risk_off` when BTC and basket momentum are negative;
- classifier emits `neutral` for mixed benchmark/basket evidence;
- classifier emits `risk_on` when benchmark and basket trend positively;
- insufficient data returns an explicit `insufficient_data` reason;
- `risk_off` blocks entries/increases but allows exits;
- `neutral` clamps new exposure but allows exits;
- overlay reason counts are included in report output.

CLI tests:

- JSON report prints structured payload;
- `--pair`, `--timeframe`, and `--strict-data` constrain evaluation;
- unsupported timeframe fails clearly;
- save-report writes JSON;
- failures exit nonzero.

Replay tests:

- baseline and overlay summaries are both present;
- overlay interventions are counted separately from risk-engine blocks/clamps;
- no overlay path can enable live order submission;
- replay remains cache-only.

Verification:

```bash
poetry run pytest tests/test_market_regime_overlay.py tests/test_cli.py -k "market_regime"
poetry run pytest
poetry run krakked market-regime-research --start <iso> --end <iso> --json
poetry run krakked market-regime-overlay-backtest --start <iso> --end <iso> --json
git diff --check
```

## Implementation Status

Implemented on `2026-05-30` as a research-only lane:

- added `krakked market-regime-research`;
- added `krakked market-regime-overlay-backtest`;
- added a replay-only plan transform hook used by the overlay comparison;
- kept normal `backtest` and runtime strategy behavior unchanged;
- kept runtime config and operator UI wiring out of scope.

Initial rolling-window evidence:

```bash
poetry run krakked market-regime-research \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --strict-data \
  --save-report C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-research-20260530.json

poetry run krakked market-regime-overlay-backtest \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --strict-data \
  --save-report C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-overlay-backtest-20260530.json
```

Results:

- strict cached data coverage was usable for both commands;
- the classifier labeled `121` research cycles: `0` risk-on, `48` neutral,
  and `73` risk-off;
- the overlay comparison baseline and overlay both stayed `weak_signal` with
  `0` fills, `0` blocked actions, and `0` overlay interventions;
- this proves the command path and report shape, but it is not promotion
  evidence because there were no generated strategy intents for the overlay to
  block or clamp.

Five-window fixed-default comparison:

- Aggregate report:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-overlay-five-window-20260530\aggregate.json`
- Average baseline return: `-0.0142%`.
- Average overlay return: `+0.0084%`.
- Total baseline fills: `6`.
- Total overlay fills: `2`.
- Overlay interventions: `12`, all blocks, all in
  `2026-04-10T00:00:00+00:00 -> 2026-04-30T00:00:00+00:00`.
- Weak-signal windows: `4 / 5`.

Readout:

- The overlay did not produce enough broad action/intervention evidence for
  runtime promotion.
- The only material benefit came from fully blocking the one losing
  decision-helpful window, which turned that replay into `weak_signal`.
- This fails the promotion rule that the overlay must not turn a
  decision-helpful replay into a weak-signal replay.
- Treat the fixed-default overlay as research-inconclusive and
  not-promotion-ready.

## Recommendation

Do not tune a large parameter grid yet. The next useful task is a research-only
exposure scenario that creates enough baseline risk to test whether the overlay
reduces bad exposure without simply flattening the book into cash.

## Controlled Exposure Scenario Results

Implemented on `2026-05-30`:

```bash
poetry run krakked market-regime-exposure-research \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --strict-data \
  --save-report market-regime-exposure-research.json
```

The command is cache-only and research-only. It does not use the strategy
engine, order router, or live/paper execution path. It simulates controlled
long exposure with fees, rebalancing, equity curves, drawdown, and exposure
percentages.

Scenarios:

- `starter_equal_weight`: equal-weight exposure across the configured starter
  universe.
- `btc_only`: benchmark-only exposure.
- `alt_equal_weight`: equal-weight exposure across non-benchmark starter pairs.

Overlay modes:

- `entry_guard`: uses the execution-plan overlay shape; blocks/clamps new or
  increased exposure but does not force target de-risking.
- `target_scale`: scales desired target exposure by regime; neutral halves the
  target and risk-off targets cash, with exits allowed.

Five-window default exposure run:

- Allocation: `20%`.
- Rebalance cadence: every `6` `4h` bars.
- Fee model: `25 bps`.
- Aggregate report:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-scenarios-20260530\aggregate.json`

Readout:

- `entry_guard` is not promising. It improved drawdown slightly, but average
  return delta was negative for all three scenarios.
- `target_scale` is the useful shape. It was not cash-only, averaging `63.6%`
  active cycles and about `7.9%` average exposure at the `20%` allocation.
- At `20%` allocation, target-scale results were:
  - `starter_equal_weight`: average return delta `+0.3158%`, positive in
    `3 / 5` windows, average drawdown delta `-1.1280%`, drawdown improved in
    `4 / 5` windows.
  - `alt_equal_weight`: average return delta `+0.4077%`, positive in `3 / 5`,
    average drawdown delta `-1.2726%`, drawdown improved in `4 / 5`.
  - `btc_only`: average return delta `+0.0410%`, positive in `2 / 5`, average
    drawdown delta `-0.7718%`, drawdown improved in `4 / 5`.

Allocation sensitivity:

- Aggregate report:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-sensitivity-20260530\aggregate.json`
- Tested `5%`, `20%`, and `50%` allocation.
- Target-scale remained positive for starter and alt-basket scenarios at all
  three allocations, and drawdown improved in `4 / 5` windows.
- Entry-guard remained average-return negative at all three allocations.

Lookback sensitivity:

- Aggregate report:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-lookback-20260530\aggregate.json`
- Tested target-scale only at `21`, `42`, and `63` bars using `20%`
  allocation.
- `63` bars was strongest:
  - `starter_equal_weight`: average return delta `+0.57%`, positive in `4 / 5`,
    drawdown improved in `5 / 5`.
  - `alt_equal_weight`: average return delta `+0.66%`, positive in `4 / 5`,
    drawdown improved in `5 / 5`.
  - `btc_only`: average return delta `+0.31%`, positive in `3 / 5`, drawdown
    improved in `5 / 5`.

Decision:

- Do not pursue `entry_guard` as the primary runtime shape.
- Continue research on `target_scale` as a portfolio target-exposure throttle.
- Do not runtime-wire yet. The evidence is promising but still comes from
  synthetic controlled exposure, not actual starter strategy intent.
- The next research slice should test target-scale against a strategy-like
  target exposure adapter and a longer out-of-sample window set before any
  config or runtime integration.

## Strategy-Proxy Target-Scale Sweep

Implemented on `2026-05-30`:

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
  --strict-data \
  --save-dir C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-proxy-sweep-20260530
```

`trend_proxy` is a strategy-like target adapter, not a runtime strategy. At each
rebalance it ranks starter pairs by `63`-bar `4h` momentum, requires at least
`150 bps`, targets the top `4` equally inside the configured allocation, and
targets cash when no pair qualifies. The baseline target selection does not use
the market-regime classifier.

Artifact:

- `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-proxy-sweep-20260530\aggregate.json`

Gate result:

| Window set | Allocation | Avg return delta | Positive windows | Drawdown improved | Min overlay active cycles | Min exposure ratio | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `recent_20d` | `5%` | `+0.0807%` | `4 / 5` | `4 / 5` | `0.0%` | `0.0000` | fail |
| `recent_20d` | `20%` | `+0.3221%` | `4 / 5` | `4 / 5` | `0.0%` | `0.0000` | fail |
| `long_4h` | `5%` | `+0.1375%` | `5 / 6` | `4 / 6` | `3.3%` | `0.1268` | fail |
| `long_4h` | `20%` | `+0.5477%` | `5 / 6` | `5 / 6` | `3.3%` | `0.1265` | fail |

Decision:

- The sweep passes the return and drawdown parts of the gate.
- It fails the exposure-quality parts of the gate: overlay active cycles and
  overlay-vs-baseline exposure ratio are too low in at least one window set.
- Do not runtime-wire target-scale from this evidence.
- Treat market-regime state as useful for operator visibility and continued
  research, but not yet as an execution throttle.
- The next implementation should either stop at an operator-facing market-state
  indicator or design a less sparse target source before revisiting runtime
  throttling.

## Dense Trend-Rank Proxy Follow-Up

Implemented on `2026-05-30` after the first `trend_proxy` sweep showed that
the target source was too sparse to support a runtime decision.

`trend_rank_proxy` is a denser strategy-proxy target adapter:

- Uses cached `4h` OHLC only.
- Ranks starter pairs by momentum using up to the configured lookback.
- Starts after a two-bar warmup instead of waiting for a full `63` bars.
- Does not require positive absolute momentum.
- Targets the top `4` pairs equally inside the configured allocation.
- Does not use the market-regime classifier for baseline target selection.

Artifacts:

- Hard scale, `neutral=0.5`, `risk_off=0.0`:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-sweep-20260530\aggregate.json`
- Soft scale, `neutral=0.75`, `risk_off=0.25`:
  `C:\Users\Rob\AppData\Local\krakked\krakked\reports\backtests\market-regime-exposure-trend-rank-proxy-soft-scale-sweep-20260530\aggregate.json`
- Adjacent soft scales, `neutral=0.80`, `risk_off=0.35` and
  `neutral=0.85`, `risk_off=0.50`, under matching `soft-scale-n080-r035` and
  `soft-scale-n085-r050` report directories.

Hard-scale result:

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

Adjacent soft-scale checks did not repair the long-window breadth failure:

| Scale | Long avg return delta (`5%` / `20%`) | Long positive windows | Long drawdown improved | Recent gate | Long gate |
| --- | ---: | ---: | ---: | --- | --- |
| `neutral=0.80`, `risk_off=0.35` | `+0.0689% / +0.2698%` | `3 / 6` | `5 / 6` | pass | fail |
| `neutral=0.85`, `risk_off=0.50` | `+0.0536% / +0.2086%` | `3 / 6` | `5 / 6` | pass | fail |

Decision:

- `trend_rank_proxy` fixed the target-source sparsity enough to make the
  exposure-quality gate informative.
- Hard `risk_off=0.0` target-scale is too aggressive for this target source.
- Softer scaling passes the recent set but still fails long-window breadth.
- Runtime wiring remains blocked.
- The next useful research should improve the target source itself, not keep
  tuning the same broad scale knobs. Candidate directions are signal-quality
  filters, volatility-adjusted ranking, or a simple cash/benchmark fallback
  whose baseline exposure remains stable enough for the overlay test to be
  meaningful.

## Signal-Quality Concentration Pass

Implemented on `2026-05-30`.

The dense `trend_rank_proxy` pass revealed a practical issue with the first
setup: with the current starter universe, `max_target_pairs=4` usually selected
all four configured pairs. That made the scenario behave like equal-weight
starter exposure after warmup, so it did not meaningfully test signal quality.

The follow-up kept the same research-only scenario and soft target-scale
settings, but swept concentrated rank selections:

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

All three concentrated variants passed the existing promotion gate. Top 2 is
the preferred research candidate because it improves signal quality without
making the proxy single-asset concentrated.

Top 2 result:

| Window set | Allocation | Avg return delta | Positive windows | Drawdown improved | Min overlay active cycles | Min exposure ratio | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `recent_20d` | `5%` | `+0.1285%` | `4 / 5` | `5 / 5` | `95.0%` | `0.5105` | pass |
| `recent_20d` | `20%` | `+0.5144%` | `4 / 5` | `5 / 5` | `95.0%` | `0.5105` | pass |
| `long_4h` | `5%` | `+0.1337%` | `4 / 6` | `4 / 6` | `96.7%` | `0.4389` | pass |
| `long_4h` | `20%` | `+0.5356%` | `4 / 6` | `4 / 6` | `96.7%` | `0.4388` | pass |

Decision:

- Signal quality improved enough for the research gate to pass when
  `trend_rank_proxy` is limited to the top `2` ranked pairs.
- This is the first market-regime target-scale candidate that clears both
  recent and long cache-only window sets at `5%` and `20%` allocations.
- This is still research evidence, not runtime approval. No live, paper, risk
  engine, strategy-default, or order-routing behavior changes in this pass.
- The next slice may plan runtime risk-throttle wiring, but that plan needs an
  explicit operator-facing config boundary, a default-disabled rollout path,
  and replay proof against actual strategy intents before enabling anything.

## Gate 1 Runtime Plumbing

Gate 1 adds the runtime path without promoting the research candidate:

- `risk.market_regime_throttle` is present but default-disabled.
- When enabled, runtime computes one cached-`4h` classifier snapshot per
  decision cycle using the configured pairs, or the starter universe when no
  throttle-specific pair list is set.
- The only supported runtime mode is `target_scale`:
  - `risk_on`: leaves non-manual targets unchanged.
  - `neutral`: scales non-manual target exposure to `0.75`.
  - `risk_off`: scales non-manual target exposure to `0.25`.
- Manual exposure is not scaled.
- Exits and already-reducing targets are not blocked or scaled.
- If classifier data is unavailable, the default policy is `block_new_risk`,
  which clamps new/increasing non-manual targets to current exposure while
  still allowing reductions.
- Plan metadata records the throttle snapshot, regime, reason codes, and
  intervention counts when the throttle is enabled.

This remains a wiring gate only. Strategy defaults, replay semantics,
order routing, live/paper gates, and starter behavior are unchanged.

## Gate 2 Runtime Replay Proof

Gate 2 adds a cache-only replay comparison command around the actual runtime
throttle plumbing:

```bash
poetry run krakked market-regime-throttle-backtest \
  --start <iso> \
  --end <iso> \
  --strict-data \
  --json
```

This is different from `market-regime-overlay-backtest`. The older overlay
command transforms finished plans after strategy/risk sizing for research. The
Gate 2 command runs two full offline replays through the normal strategy, risk,
order router, OMS, and simulation layers:

- baseline replay with `risk.market_regime_throttle.enabled=false`;
- comparison replay with the same candidate throttle settings forced on.

The report records:

- baseline versus throttled return, drawdown, action, fill, and trust summaries;
- throttle metadata from real `ExecutionPlan.metadata`;
- regime and reason-code counts;
- blocked/clamped/throttled action counts;
- Gate 2 checks for real strategy activity, filled orders, clean data, no
  execution errors, no trust regression, and non-empty reasons when the
  throttle intervenes.

Gate 2 is still evidence only. Passing the command does not enable the runtime
throttle, change starter defaults, or change live/paper execution semantics.

## Strategy Activity Diagnostic

If Gate 2 fails because the baseline replay produced no strategy actions or
fills, run the strategy activity sweep before tuning market-regime parameters:

```bash
poetry run krakked strategy-activity-sweep \
  --window-set recent_20d \
  --window-set long_4h \
  --strict-data \
  --save-dir <report-dir>
```

The sweep keeps runtime config unchanged, disables the runtime throttle in each
diagnostic replay, and compares these groups by default:

- current configured strategy pack;
- starter pack (`trend_core`, `vol_breakout`, `majors_mean_rev`);
- each starter strategy alone.

The diagnostic classifies each replay as `filled`, `no_intents`,
`score_filtered`, `risk_blocked`, `no_orders`, `no_fills`, `data_not_ready`, or
`run_failed`. A Gate 2 rerun is meaningful only on windows where the baseline
strategy group has clean data, actions, and fills.

May 30 activity readout:

| Group | Ready windows | Action windows | Fill windows | Dominant issue |
| --- | ---: | ---: | ---: | --- |
| configured (`trend_core`, `majors_mean_rev`) | `8 / 11` | `4 / 11` | `4 / 11` | recent rolling windows have `no_intents`; older long windows miss 1h cache |
| starter_all | `0 / 11` | `0 / 11` | `0 / 11` | `vol_breakout` needs uncached `15m` lanes |
| trend_core | `8 / 11` | `4 / 11` | `4 / 11` | same activity source as configured pack |
| vol_breakout | `0 / 11` | `0 / 11` | `0 / 11` | missing `15m` replay coverage |
| majors_mean_rev | `8 / 11` | `0 / 11` | `0 / 11` | `no_intents` |

Gate 2 then passed on the four action/fill windows:

| Window | Actions | Fills | Result |
| --- | ---: | ---: | --- |
| `2026-03-21 -> 2026-04-10` | `76` | `2` | pass |
| `2026-04-10 -> 2026-04-30` | `12` | `4` | pass |
| `2026-03-21 -> 2026-04-20` | `737` | `9` | pass |
| `2026-04-20 -> 2026-05-20` | `388` | `4` | pass |

This proves the runtime throttle path can operate against real strategy
intents. It does not prove the current rolling window, which still has ready
data but no configured-strategy intents.
