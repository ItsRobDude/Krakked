# Krakked

Krakked is a modular Kraken trading system for California and broader U.S. use, built with a strong bias toward safety, testing, clean architecture, and an eventual path to live trading.

## 🚀 Current Status

This repository includes working, test-covered implementations across the core trading stack. Phases 1-7 are substantially implemented: the repo has connection/auth, market data, portfolio accounting, strategy/risk, OMS execution, a FastAPI control plane, orchestrator/runtime guardrails, metrics, schema checks, CI, packaging, and a documented Docker-first deployment path. Recent work also added strategy weighting, crash-safe ML checkpoint/resume foundations, release automation, and export/import-style operator tooling.

| Module | Status | Notes |
| :--- | :--- | :--- |
| **Phase 1: Connection** | ✅ Implemented | REST client with signed private calls, configurable rate limiting, nonce handling, and encrypted credential storage/validation. |
| **Phase 2: Market Data** | ✅ Implemented | Pair-universe discovery, OHLC backfill to a pluggable store, and WebSocket v2 streaming with staleness checks. |
| **Phase 3: Portfolio** | ✅ Implemented | Portfolio service with SQLite persistence, weighted-average cost PnL, fee tracking, and cashflow detection. |
| **Phase 4: Strategy & Risk** | ✅ Implemented with known follow-ups | Strategy loader with multi-timeframe scheduling, per-strategy/portfolio caps, liquidity gating, and staleness handling; order tagging/OMS wiring will land in Phase 5. |
| **Phase 5: Execution** | ✅ Implemented | OMS with market-data-driven routing, retries/backoff, dead-man switch hooks, panic cancel, and SQLite persistence; paper now uses a persistent synthetic account by default, with explicit `allow_live_trading` gates for live submission. |
| **Phase 6: UI/Control** | ✅ Implemented | CLI/web interface for monitoring and manual control. See [Phase 6 contract](docs/phases/phase6.md#status--todo) for the completed scope and API details. |
| **Phase 7: Ops & Runtime** | ✅ Implemented with follow-up validation | Orchestrator, structured logging, metrics, schema guard, CI, packaging, release workflow, backup/export/import, and Docker deployment docs are in place. |
| **Current Product Track** | 🚧 In progress | The main remaining work is deployment proof on a real Docker host, richer strategy/ML UX, live-readiness hardening, and commercial/distribution polish. |

The repo now has a strong engineering base and has moved past the original phase plan. The current work is less about building missing architecture and more about productization:

* Docker-first install, upgrade, backup, and release docs now exist.
* GitHub Actions covers CI and tag-driven release publishing.
* Strategy weighting and ML checkpoint/resume foundations are present in code.
* The next milestone is operational validation and UX polish rather than another major subsystem.

See the consolidated phase contract in [`docs/contract.md`](docs/contract.md) for the full design scope across Phases 1–7. Individual phase files remain available for historical reference.

See [`docs/product-roadmap.md`](docs/product-roadmap.md) for the current product direction and post-phase milestones: Docker-first deployment, California/U.S. positioning, live-trading goals, strategy weighting, ML roadmap, and productization priorities.

See [`docs/docker.md`](docs/docker.md) for the preferred self-hosted deployment flow.

See [`docs/onboarding.md`](docs/onboarding.md) for a beginner-friendly first-run path, [`docs/releases.md`](docs/releases.md) for the Docker/image release flow, [`docs/upgrades.md`](docs/upgrades.md) for update steps, [`docs/backup-restore.md`](docs/backup-restore.md) for backup/export/import, [`docs/unraid.md`](docs/unraid.md) for an Unraid-oriented deployment sketch, and [`docs/distribution.md`](docs/distribution.md) for the current distribution/commercial recommendation.

See [`docs/simulation.md`](docs/simulation.md) for the current offline replay / backtesting seam and its explicit limits.

## 🏗️ Architecture

The bot is organized into distinct modules:

*   **`connection`**: Low-level API interaction (REST only today). Handles auth, signing, retries, and rate limits, plus encrypted credential setup.
*   **`market_data`**: Abstracted data access. Builds the tradable universe, backfills OHLC, and exposes WebSocket v2 streaming caches with stale-data protection.
*   **`portfolio`**: Accounting engine. Tracks balances, positions, WAC PnL, and cashflows in memory with SQLite persistence.
*   **`strategy`**: Decision-making layer. Strategies emit intents that flow through the risk engine and into the OMS for synthetic paper or live execution routing.

## 📦 Installation & Setup

### 🐳 Preferred: Docker Compose

Krakked's primary deployment path is Docker-first, self-hosted operation. If you want the closest thing to the intended product shape, start with:

```bash
cp .env.example .env
mkdir -p deploy/config deploy/data deploy/state
cp config_examples/config.yaml deploy/config/config.yaml
cp config_examples/config.paper.yaml deploy/config/config.paper.yaml
cp config_examples/config.live.yaml deploy/config/config.live.yaml
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

Before starting, merge the container path overrides from `config_examples/config.container.yaml` into `deploy/config/config.yaml` so the UI, DB, and market-data cache all write to persisted mounted paths. Full instructions live in [`docs/docker.md`](docs/docker.md). If you are operating from a published image instead of a source checkout, use the base `compose.yaml` only and set `KRAKKED_IMAGE` / `KRAKKED_IMAGE_TAG` in `.env`.

### Prerequisites

*   **Python 3.11+**: Ensure Python is installed and added to your PATH.
*   **Poetry**: The dependency manager used for this project.

### 🐧 Linux / macOS

1.  **Install Poetry** (if not already installed):
    ```bash
    curl -sSL https://install.python-poetry.org | python3 -
    ```

2.  **Clone and Install**:
    ```bash
    git clone <repo-url>
    cd krakked
    poetry install
    ```

### 🪟 Windows 10 / 11

1.  **Install Python**:
    Download and install Python 3.11+ from [python.org](https://www.python.org/downloads/).
    *   *Important*: Check the box **"Add Python to PATH"** during installation.

2.  **Install Poetry** (via PowerShell):
    ```powershell
    (Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
    ```
    *   You may need to add `%APPDATA%\Python\Scripts` to your PATH if warned.

3.  **Clone and Install**:
    ```powershell
    git clone <repo-url>
    cd krakked
    poetry install
    ```

### 🎛️ Optional: Textual TUI dashboard

The core engine and FastAPI API can run without any UI dependencies installed. To use the Textual-based terminal dashboard, add
the optional extra:

```bash
poetry install -E tui
```

After installing the extra, launch the dashboard with `python ui/tui_dashboard.py` once your API is reachable. The base engine
remains lightweight when the TUI extra is omitted.

### 🛠️ Editable / dev installs

Poetry remains the preferred workflow. The CI pipeline installs dependencies with the dev group and the TUI extra enabled; mirror that locally for parity:

```bash
poetry install --with dev --extras tui
```

If you are working outside Poetry, you can still get an editable install from the repo root:

```bash
pip install -e .[tui]
```

### 🔄 Pre-commit hooks

Consistent formatting and linting are enforced by `pre-commit`. The CI workflow runs `poetry run pre-commit run --all-files` and will fail if hooks are not clean, so install them locally to avoid churn:

```bash
pip install pre-commit
pre-commit install
```

Run the full suite locally any time with:

```bash
poetry run pre-commit run --all-files
```

## ⚙️ Configuration

The bot uses two configuration files stored in your OS-specific user configuration directory (handled via `appdirs`). Example files live in `config_examples/`, and the overlay/merging rules are documented in [`docs/CONFIG.md`](docs/CONFIG.md).

The product, CLI, Python package, and config namespace now all use `krakked` / `KRAKKED_*`.

*   **Linux**: `~/.config/krakked/`
*   **macOS**: `~/Library/Application Support/krakked/`
*   **Windows**: `C:\Users\<User>\AppData\Local\krakked\`

### Files

1.  **`config.yaml`** (User Config):
    *   Contains region settings, universe selection, strategy parameters, and risk limits.
    *   *Example schema available in `src/krakked/config.py`*.

    Example with execution defaults:

    ```yaml
    region:
      code: "US_CA"
      capabilities:
        supports_margin: false
        supports_futures: false
        supports_staking: false
      default_quote: "USD"

    execution:
      mode: "paper"                  # "live" | "paper" | "dry_run"
      default_order_type: "limit"    # "market" | "limit"
      max_slippage_bps: 50            # 0.5% price protection for limit offsets
      time_in_force: "GTC"            # Good-til-cancel default
      post_only: false                # Maker-only preference
      validate_only: true             # Automatically flips to false when mode == "live"
      dead_man_switch_seconds: 600    # Auto-cancel window (0 to disable)
      max_retries: 3                  # Per-order retry budget
      retry_backoff_seconds: 2        # Initial retry delay
      retry_backoff_factor: 2.0       # Exponential backoff multiplier
      max_concurrent_orders: 10       # Concurrency guardrail
      min_order_notional_usd: 20.0    # Floor to avoid dust orders (risk-increasing BUYs only)
    ```

2.  **`secrets.enc`** (Encrypted Credentials):
    *   Stores your Kraken API Key and Secret securely.
    *   **Setup**: The bot includes a `krakked setup` utility to prompt for keys and create this file.

### Bootstrap helper

Most modules can start with a ready REST client and parsed configuration using:

```python
from krakked.bootstrap import bootstrap

client, app_config = bootstrap()
```

The helper calls `load_config()` and `secrets.load_api_keys(allow_interactive_setup=True)`, raising a `CredentialBootstrapError` when credentials are missing, cannot be decrypted (e.g., wrong `KRAKKED_SECRET_PW`), or fail validation. Interactive setups are only triggered when keys are missing, so non-interactive environments should supply `KRAKEN_API_KEY`/`KRAKEN_API_SECRET` or the decryption password to avoid errors.

### Strategy & Risk essentials (Phase 4)

To run the implemented Phase 4 features, set these keys in `config.yaml`:

*   **Strategy scheduling**: Provide `strategies.enabled` plus per-strategy `timeframes` (or `timeframe`) arrays to run multi-timeframe cycles. 【F:src/krakked/strategy/engine.py†L80-L119】
*   **Per-strategy caps**: Configure `risk.max_per_strategy_pct` to clamp exposure across strategies and `strategies.configs.<name>.userref` if you need consistent attribution. 【F:src/krakked/config.py†L48-L72】【F:src/krakked/strategy/risk.py†L263-L349】
*   **Portfolio caps**: Use `risk.max_portfolio_risk_pct`, `risk.max_open_positions`, and `risk.max_per_asset_pct` to enforce total exposure limits. 【F:src/krakked/config.py†L40-L72】【F:src/krakked/strategy/risk.py†L263-L349】
*   **Liquidity gating**: Set `risk.min_liquidity_24h_usd` to block new exposure when recent volume is too low. 【F:src/krakked/config.py†L60-L72】【F:src/krakked/strategy/risk.py†L203-L249】
*   **Staleness handling**: Market data staleness and connection checks are enforced before intent generation; strategies surface `DataStaleError` to skip a timeframe when needed. 【F:src/krakked/strategy/engine.py†L33-L120】

### Phase 5 execution wiring

Phase 4 produces risk-adjusted actions that now flow through an OMS capable of paper/validate routing by default. Orders are built from plan deltas, priced off mid/bid/ask via `MarketDataAPI`, and constrained by `ExecutionConfig` guardrails (slippage bands, min notional, max concurrency). Submissions use retries/backoff for transient errors, apply a dead-man switch heartbeat when enabled, and persist to SQLite (`execution_orders` / `execution_results`) alongside in-memory tracking. The admin CLI provides listing, reconciliation, targeted cancels, and a panic cancel-all path that refreshes state after cancelation.

#### Strategy ID propagation and tagging

`strategy_id` remains carried end-to-end: strategies emit `StrategyIntent`, the risk engine normalizes that into `RiskAdjustedAction`, and the resulting `DecisionRecord` snapshots the same identifier for audit/history. Each strategy config supports an optional `userref` field in `StrategyConfig` to give the strategy a stable numeric tag that is forwarded into Kraken orders for attribution.

#### Canonical strategy identifiers

Use the shared strategy IDs and implementation type strings below everywhere—`strategies.configs` keys, `StrategyConfig.name`, `StrategyIntent.strategy_id`, and `risk.max_per_strategy_pct` keys—to keep config, risk, and UI labels aligned. `load_config` will coerce known IDs back to their canonical types when they diverge.

* `trend_core` (`type: trend_following`) – upgraded trend follower and current default.
* `dca_overlay` (`type: dca_rebalance`) – DCA/rebalance overlay.
* `vol_breakout` (`type: vol_breakout`) – volatility breakout.
* `majors_mean_rev` (`type: mean_reversion`) – BTC/ETH mean reversion.
* `rs_rotation` (`type: relative_strength`) – relative strength rotation.


### 🧪 Paper / Validate Quickstart

`krakked run-once` is pinned to the safest defaults: `execution.mode="paper"`, `validate_only=True`, and `allow_live_trading=False`, even if your config requests otherwise. Orders are priced from mid/bid/ask snapshots with slippage caps and written to SQLite for inspection.

To run a single synchronous cycle:

```bash
poetry run krakked run-once
```

### 📉 Offline Replay Quickstart

`krakked backtest` replays stored OHLC through the existing strategy, risk, router, and OMS layers without Kraken network calls. It uses an explicit synthetic USD bankroll and now leads with a simple trust/readiness readout so average users do not have to parse the whole report to see whether a run is decision-helpful.

```bash
poetry run krakked backtest-preflight \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z

poetry run krakked backtest \
  --start 2026-04-01T00:00:00Z \
  --end 2026-04-20T00:00:00Z \
  --starting-cash-usd 10000 \
  --save-report backtest-report.json \
  --publish-latest
```

Start with `backtest-preflight` if you want a quick local coverage check before running the strategy stack. Add `--fee-bps` to tune the flat taker-fee assumption, `--strict-data` to hard-fail on missing or partial coverage, `--db-path backtest.db` if you want to keep the SQLite decisions/orders/results after the run, and `--publish-latest` when you want the operator dashboard to read the latest validated replay summary. Use `poetry run krakked compare-backtests --baseline a.json --candidate b.json` to compare two saved reports without rerunning. See [`docs/simulation.md`](docs/simulation.md) for the current assumptions, limits, and replay smoke scenarios.

### ▶️ Running the bot

* **Full orchestrator**: `poetry run krakked run` starts the WebSocket loop, scheduler, strategies, execution, and FastAPI UI.
* **Non-interactive setup**: add `--allow-interactive-setup false` to block credential prompts in CI/servers while still booting the orchestrator.
* **Execution knobs**: `execution.mode` selects `paper` vs. `live`, `execution.allow_live_trading` is the final gate for live submissions, and dead-man/kill switches remain available via the configured cancel-all hooks.

Execution defaults stay conservative until you explicitly opt-in: `mode="paper"`, `validate_only=False`, and `allow_live_trading=False`, which now gives you a persistent synthetic paper account out of the box while still keeping live submission gated off until you deliberately enable it.【F:src/krakked/config.py†L29-L36】

To start the full orchestrator (market data WebSocket loop, portfolio sync scheduler, strategy cycles with execution, and the FastAPI UI in the same process):

```bash
poetry run krakked run
```

The runner bootstraps configuration/credentials, initializes market data + portfolio + strategy services, and hosts the UI on `config.ui.host:config.ui.port` when enabled. It listens for `SIGINT`/`SIGTERM` to stop scheduler loops, cancel any open orders, and shut down the WebSocket feed cleanly.

CLI helpers default to the local `portfolio.db` SQLite file; pass `--db-path` to point at an alternate location when needed.【F:src/krakked/cli.py†L20-L33】 After the run, inspect orders/results in the default SQLite store (`portfolio.db` unless overridden) or via the admin helper:

```bash
# Review open/pending orders and execution summaries
poetry run python -m krakked.execution.admin_cli list-open
poetry run python -m krakked.execution.admin_cli recent-executions

# Targeted or global cancels (remains safe in paper mode)
poetry run python -m krakked.execution.admin_cli cancel --plan-id <id>
poetry run python -m krakked.execution.admin_cli panic
```

To query SQLite directly:

```bash
sqlite3 portfolio.db \
  "SELECT plan_id, local_id, kraken_order_id, status, volume, price FROM execution_orders ORDER BY created_at DESC LIMIT 5;"
sqlite3 portfolio.db \
  "SELECT id, started_at, completed_at, status, summary FROM execution_results ORDER BY started_at DESC LIMIT 3;"
```

Key tables to review are `execution_orders` (every `LocalOrder` snapshot), `execution_order_events` (state transitions), and `execution_results` (per-plan outcome summaries). The admin CLI mirrors that data without requiring SQL and performs a reconciliation pass after panic cancel-all.

### 🚦 Console entry points

Use the packaged console script for the common workflows:

* `poetry run krakked run` — long-lived orchestrator with scheduler, OMS, and UI enabled.
* `poetry run krakked run-once` — single paper/validate-only cycle for quick safety checks.
* `poetry run krakked backtest-preflight --start <iso> --end <iso>` — check historical pair/timeframe coverage and replay readiness without running strategies.
* `poetry run krakked backtest --start <iso> --end <iso>` — offline replay over stored OHLC using the live strategy/risk/execution stack with slippage/fee-aware simulation fills, coverage preflight, and optional saved JSON reports.
* `poetry run krakked compare-backtests --baseline <report.json> --candidate <report.json>` — compare two saved replay reports without rerunning the strategy stack.
* `poetry run krakked migrate --db-path <path>` — create or migrate the SQLite portfolio store (defaults to `portfolio.db`).
* `poetry run krakked db-schema-version --db-path <path>` — inspect the current schema version recorded in the store.
* `poetry run krakked db-backup --db-path <path>` / `db-info` / `db-check` — backup and inspect the portfolio database before upgrades.
* `poetry run krakked export-install --output <archive.zip> --include-data` — capture config, SQLite state, and optional cached data for migration or support bundles.
* `poetry run krakked import-install --input <archive.zip> [--force]` — restore a previously exported install onto a new machine or deployment.
* `poetry run krakked setup` / `poetry run krakked smoke-test` — interactive credential bootstrap and a basic authenticated probe.

See the `--help` output of `poetry run krakked` for the full command list; all subcommands honor `--allow-interactive-setup` and `--db-path` where applicable.

### ✅ Enabling Live Trading (Advanced)

Live routing is guarded by multiple gates that must all be opened:

* **Set live mode**: `execution.mode: "live"`.
* **Disable validation-only**: `execution.validate_only: false` so Kraken will accept orders.
* **Affirm live intent**: `execution.allow_live_trading: true`; this defaults to `false` as a last-ditch safety catch.
* **Environment gates**: No additional env flag is required today—config values alone control live behavior.

Only adapters that submit orders honor live mode (`ExecutionAdapter`/`KrakenExecutionAdapter` and any CLI that boots the OMS with a REST client). The `krakked run-once` helper always forces paper/validate-only regardless of config so it cannot transmit live orders.

Before enabling live trading, run at least one paper `krakked run-once` cycle and review orders/results (via SQLite or the admin CLI) to validate sizing, tags, and guardrails.

### ↩️ Disabling Live Trading

To return to paper-only safety:

1. Set `execution.mode: "paper"` (or `"dry_run"`).
2. Set `execution.validate_only: true`.
3. Set `execution.allow_live_trading: false`.
4. Unset any future environment gate if added (none exist currently).

These changes immediately block live submission; adapters will reject non-validated live attempts when any gate is closed.

### 🛑 Panic Cancel / Operational Controls

The execution admin CLI exposes operational levers that work against the SQLite store and in-memory OMS state:

* `list-open`: Show persisted and in-memory open/pending orders to confirm exposure.
* `recent-executions`: Inspect recent plan runs and their success/error summaries.
* `cancel`: Cancel by plan, strategy, Kraken order id, local id, or `--all` (with optional filters) to target specific risk.
* `panic`: Refresh/reconcile state, then cancel **all** open orders—useful for fast stop-the-bleed responses.

Invoke via `poetry run python -m krakked.execution.admin_cli <subcommand>`; pass `--db-path` to point at a non-default portfolio database or `--allow-interactive-setup` if credential prompts are acceptable.

### ✅ Live Readiness Checklist

* Paper mode: At least one full `krakked run-once` paper cycle completed without errors.
* Data review: SQLite `execution_orders` / `execution_results` inspected (or equivalent admin CLI checks) to verify sizing, tagging, and guardrails.
* Config gates: `execution.mode="live"`, `execution.validate_only=false`, and `execution.allow_live_trading=true` intentionally set for production; revert any gate to disable.
* Risk reviewed: Portfolio and per-strategy risk limits rechecked for live exposure tolerance.
* Operator drills: Team knows how to invoke `panic` and targeted `cancel` via `execution.admin_cli` for immediate kill-switch behavior.

## 🧭 Development workflow & CI

The CI workflow is the source of truth for what must pass before packaging/deployment; see [`.github/workflows/ci.yml`](.github/workflows/ci.yml) for the definitive steps.【F:.github/workflows/ci.yml†L1-L102】 Mirror those commands locally:

* **Install (dev mode)**: `poetry install --with dev --extras tui` to match CI, or `pip install -e .[tui]` if you are not using Poetry.
* **Tests**: `poetry run pytest` from the repo root; set `KRAKEN_LIVE_TESTS=1` to opt into Kraken-backed integration tests (requires valid credentials).
* **Lint**: `poetry run flake8 src tests` (same scope as CI).
* **Static typing**: `poetry run mypy src tests` and `poetry run pyright src ui` (run `npm ci` in `ui/` first so Pyright picks up the built UI types, as in CI).【F:.github/workflows/ci.yml†L60-L95】
* **Packaging sanity**: `poetry build` if you need to mirror the release artifact validation done in CI.

## 🐳 Docker

The preferred Docker path is `docker compose`; see [`docs/docker.md`](docs/docker.md) for the complete setup flow with persistent config, data, and state directories.

You can still build and run the image directly when you need an isolated runtime or a deployable image:

```bash
docker build -t krakked .

docker run --rm \
  -p 8080:8080 \
  -e KRAKKED_CONFIG_DIR=/krakked/config \
  -e KRAKKED_DATA_DIR=/krakked/data \
  -e KRAKEN_API_KEY=... \
  -e KRAKEN_API_SECRET=... \
  -e KRAKKED_SECRET_PW=... \
  -v $(pwd)/deploy/config:/krakked/config \
  -v $(pwd)/deploy/data:/krakked/data \
  -v $(pwd)/deploy/state:/krakked/state \
  krakked
```

The runtime image exposes the FastAPI/UI service on port `8080` by default and ships with a `krakked` entrypoint plus the default `run --allow-interactive-setup false` command, so you can also invoke operational helpers such as `docker compose run --rm krakked db-backup --db-path /krakked/state/portfolio.db` without building a second image.
