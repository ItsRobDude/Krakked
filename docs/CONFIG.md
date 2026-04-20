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

* Execution defaults to `paper` with validate-only order handling.
* The starter universe is limited to `BTC/USD`, `ETH/USD`, `SOL/USD`, and `ADA/USD`.
* Historical backfill defaults to `1h` and `4h`.
* Live websocket OHLC defaults to a single `1m` stream.
* The enabled starter strategy pack is:
  * `trend_core`
  * `vol_breakout`
  * `majors_mean_rev`
  * `rs_rotation`
* The shipped examples intentionally split defaults by environment:
  * `paper` enables the full 4-strategy starter pack
  * `live` keeps the same 4 starter strategies configured, but enables only `trend_core` and `majors_mean_rev` by default
* ML is disabled by default until the operator explicitly opts in.
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
