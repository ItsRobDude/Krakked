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
