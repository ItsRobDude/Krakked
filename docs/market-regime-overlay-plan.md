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

## Recommendation

Build only the research evaluator and comparison report next. Stop before
runtime wiring unless the multi-window evidence clears the promotion gate above.
