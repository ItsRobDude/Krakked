# Configuration Guide

The bot reads configuration files from the OS-specific user configuration directory returned by `appdirs.user_config_dir("kraken_bot")`:

| Platform | Path |
| --- | --- |
| Linux | `~/.config/kraken_bot/` |
| macOS | `~/Library/Application Support/kraken_bot/` |
| Windows | `C:\\Users\\<User>\\AppData\\Local\\kraken_bot\\` |

Place your real configuration files in that directory:

* `config.yaml` – base settings (region, universe, strategies, execution defaults, etc.).
* `config.<env>.yaml` – optional overlay loaded after the base file, where `<env>` is the effective environment (see below).

## Bootstrapping from examples

Starter files live in `config_examples/` at the repository root. Copy them into your user configuration directory and edit as needed:

```bash
mkdir -p "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('kraken_bot'))
PY)"
cp config_examples/config.yaml "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('kraken_bot'))
PY)"/
cp config_examples/config.paper.yaml "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('kraken_bot'))
PY)"/
cp config_examples/config.live.yaml "$(python - <<'PY'
import appdirs
print(appdirs.user_config_dir('kraken_bot'))
PY)"/
```

You can keep all files side by side in the config directory; the loader will automatically read the base file plus the environment-specific overlay.

## Environment selection

Set `KRAKEN_BOT_ENV` to choose which overlay is applied:

* `dev`
* `paper`
* `live`

If `KRAKEN_BOT_ENV` is missing or any other value, the bot defaults to the `paper` overlay. The loader always reads `config.yaml` first and then merges in `config.<env>.yaml` (if present) from the same directory, so per-environment tweaks stay isolated while shared settings live in the base file.
