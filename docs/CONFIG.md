# Configuration Guide

The bot reads configuration files from the OS-specific user configuration directory returned by `appdirs.user_config_dir("krakked")`:

Krakked now uses a single internal and external namespace: `krakked` / `KRAKKED_*`.

| Platform | Path |
| --- | --- |
| Linux | `~/.config/krakked/` |
| macOS | `~/Library/Application Support/krakked/` |
| Windows | `C:\\Users\\<User>\\AppData\\Local\\krakked\\` |

Place your real configuration files in that directory:

* `config.yaml` – base settings (region, universe, strategies, execution defaults, etc.).
* `config.<env>.yaml` – optional overlay loaded after the base file, where `<env>` is the effective environment (see below).

## Bootstrapping from examples

Starter files live in `config_examples/` at the repository root. Copy them into your user configuration directory and edit as needed:

```bash
mkdir -p "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('krakked'))
PY)"
cp config_examples/config.yaml "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('krakked'))
PY)"/
cp config_examples/config.paper.yaml "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('krakked'))
PY)"/
cp config_examples/config.live.yaml "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('krakked'))
PY)"/
```

You can keep all files side by side in the config directory; the loader will automatically read the base file plus the environment-specific overlay.

## Current operator defaults

Krakked now boots with a conservative operator-first starter profile unless you override it:

* Execution defaults to `paper` with a persistent synthetic paper wallet and live submission still gated behind explicit opt-in.
* Normal paper config uses `execution.validate_only: false` because no Kraken live order is submitted. Operator safety summaries may still treat paper as effectively safe/validated; that is not the same as the raw config flag.
* Live, non-validate opening risk is also blocked unless the action's strategy ID is explicitly listed in `execution.live_strategy_allowlist`; the default empty list approves no strategies for live order submission.
* The starter universe is limited to `BTC/USD`, `ETH/USD`, `SOL/USD`, and `ADA/USD`.
* Historical backfill defaults to `1h`, `4h`, and `1d`.
* Long-running sessions refresh configured OHLC tails shortly after configured timeframe boundaries, with an hourly interval fallback by default (`market_data.ohlc_tail_refresh_interval_seconds: 3600`); set it to `0` to disable scheduled tail refresh.
* Live websocket OHLC defaults to a single `1m` stream.
* The runtime market-regime throttle exists as default-disabled risk plumbing:
  `risk.market_regime_throttle.enabled: false`. If deliberately enabled, it
  uses cached `4h` OHLC, the 63-bar classifier, and target-scale multipliers
  of `1.0` in risk-on, `0.75` in neutral, and `0.25` in risk-off. Missing
  classifier data blocks new/increasing risk by default while allowing
  reductions.
* The enabled starter strategy pack is:
  * `trend_core`
  * `majors_mean_rev`
* Starter strategy inputs are explicit in generated configs:
  * `trend_core` evaluates `BTC/USD`, `ETH/USD`, `SOL/USD`, and `ADA/USD`
    on `1h` and `4h`, with `1d` regime context.
  * `majors_mean_rev` evaluates `BTC/USD` and `ETH/USD` on `1h`, with
    `lookback_bars: 50`, `band_width_bps: 150`, and `max_positions: 2`.
* `vol_breakout` remains configured with conservative caps for manual
  research, but is disabled by default because it requires `15m` OHLC and the
  default replay/backfill set intentionally stays on `1h`, `4h`, and `1d`.
* `rs_rotation` remains configured with conservative caps for manual research,
  but is disabled by default after replay evidence showed the v1 signal was not
  promotion-ready.
* The starter pack and ML strategies are research-stage until the unified
  strategy evidence scoreboard shows a repeatable edge under the same replay
  context.
* The shipped examples intentionally split defaults by environment:
  * `paper` enables the active starter pack above
  * `live` keeps the same configured strategies, enables only `trend_core` and `majors_mean_rev` by default for evaluation, and leaves `execution.live_strategy_allowlist: []`
* ML is disabled by default until the operator explicitly opts in.
* The cockpit may show a display-only BTC/USD `4h` RiskMetrics EWMA volatility
  signal. It is operator context only: it does not affect strategy selection,
  sizing, vetoes, or order flow.
* First-run risk defaults are explicit and conservative:
  * `max_open_positions: 4`
  * `max_risk_per_trade_pct: 1.0`
  * `max_portfolio_risk_pct: 10.0`
  * `max_per_asset_pct: 5.0`
  * `max_per_strategy_pct: 5.0` for each starter strategy

## Environment selection

Set `KRAKKED_ENV` to choose which overlay is applied:

* `dev`
* `paper`
* `live`

If `KRAKKED_ENV` is missing or any other value, the bot defaults to the `paper` overlay. The loader always reads `config.yaml` first and then merges in `config.<env>.yaml` (if present) from the same directory, so per-environment tweaks stay isolated while shared settings live in the base file.

## Refreshing OHLC tails before replay

Backtests and preflight checks intentionally read cached OHLC only. To update those caches before a rolling replay window, run:

```bash
krakked refresh-ohlc
```

Use `--pair`, `--timeframe`, `--since`, and `--json` for targeted replay prep or automation. The command uses public Kraken market-data endpoints only; it does not require private credentials and does not change live-trading gates.

For operator shortcuts, `krakked replay-ready --start <iso> --end <iso>` runs the same public OHLC refresh and then prints replay preflight readiness, while `krakked replay-run --start <iso> --end <iso>` refreshes, requires clean readiness, runs the replay, and publishes the latest replay summary. Use `backtest-preflight` and `backtest` directly when you need a strictly cached/offline replay without the network refresh step.

To prove cached OHLC continuity without running a replay, use:

```bash
krakked ohlc-continuity \
  --pair BTC/USD --pair ETH/USD \
  --timeframe 1h \
  --start 2025-12-01T00:00:00Z \
  --end 2026-06-16T01:00:00Z \
  --json
```

The report shows first/last observed bars, expected interval, observed bar
count, duplicate timestamp count, missing interval count, and exact gap ranges.
Treat gaps as factual observations; the replay or operator gate decides whether
they are acceptable for the source and market.

Backtests now use cached pre-window OHLC for indicator, regime, and risk warmup by default. The replay still starts the synthetic wallet and decision timeline at the requested `--start`; warmup bars only feed `get_ohlc(...)` lookbacks. Use `--warmup-days 0` on `backtest` or `backtest-preflight` only when you intentionally want exact-window legacy behavior. With `--strict-data`, missing or partial warmup series fail the run the same way missing execution-window series do.

## Proving the runtime market-regime throttle

`risk.market_regime_throttle` is not enabled by default. Before any operator
chooses to enable it, compare the current strategy replay against the same
replay with the runtime throttle forced on:

```bash
krakked market-regime-throttle-backtest \
  --start 2026-05-10T00:00:00Z \
  --end 2026-05-30T00:00:00Z \
  --strict-data \
  --json
```

This command is cache-only and uses the real strategy, risk, order router, and
simulation execution path. It does not use the older research-only
post-processing overlay and does not change config defaults. The report includes
baseline versus throttled replay summaries, runtime throttle intervention
counts, regime/reason counts, and Gate 2 checks that require real strategy
actions, filled orders, clean data, no execution errors, no trust regression,
and non-empty reasons when the throttle intervenes.

If a current rolling replay has ready data but zero strategy actions, diagnose
strategy activity before drawing conclusions from Gate 2:

```bash
krakked strategy-activity-sweep \
  --window-set recent_20d \
  --window-set long_4h \
  --strict-data \
  --save-dir strategy-activity-sweep
```

The sweep runs normal cache-only backtests across configured and starter
strategy groups, then classifies each window as `filled`, `no_intents`,
`score_filtered`, `risk_blocked`, `data_not_ready`, or another explicit stage.
Use it to find a real action/fill window before rerunning
`market-regime-throttle-backtest`.
