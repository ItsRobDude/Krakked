# Kraken Trading Bot

A modular, robust Kraken trading bot designed for spot trading (CA/USA) with a focus on safety, testing, and clean architecture.

## 🚀 Current Status

This repository includes working, test-covered implementations for the early phases, but it is still a backend-only project. Phase 1 connection and credential handling are fully implemented; execution wiring and any user interface remain pending. Phase 4 now runs multi-timeframe scheduling, per-strategy/portfolio caps, liquidity gating, and stale-data handling in the strategy/risk engine.

| Module | Status | Notes |
| :--- | :--- | :--- |
| **Phase 1: Connection** | ✅ Implemented | REST client with signed private calls, configurable rate limiting, nonce handling, and encrypted credential storage/validation. |
| **Phase 2: Market Data** | ✅ Implemented | Pair-universe discovery, OHLC backfill to a pluggable store, and WebSocket v2 streaming with staleness checks. |
| **Phase 3: Portfolio** | ✅ Implemented | Portfolio service with SQLite persistence, weighted-average cost PnL, fee tracking, and cashflow detection. |
| **Phase 4: Strategy & Risk** | ✅ Implemented with known follow-ups | Strategy loader with multi-timeframe scheduling, per-strategy/portfolio caps, liquidity gating, and staleness handling; order tagging/OMS wiring will land in Phase 5. |
| **Phase 5: Execution** | ⏳ Not started | Order Management System (OMS), trade execution, order lifecycle management. |
| **Phase 6: UI/Control** | ⏳ Not started | CLI/web interface for monitoring and manual control. |

See the consolidated phase contract in [`docs/contract.md`](docs/contract.md) for the full design scope across Phases 1–7. Individual phase files remain available for historical reference.

## 🏗️ Architecture

The bot is organized into distinct modules:

*   **`connection`**: Low-level API interaction (REST only today). Handles auth, signing, retries, and rate limits, plus encrypted credential setup.
*   **`market_data`**: Abstracted data access. Builds the tradable universe, backfills OHLC, and exposes WebSocket v2 streaming caches with stale-data protection.
*   **`portfolio`**: Accounting engine. Tracks balances, positions, WAC PnL, and cashflows in memory with SQLite persistence.
*   **`strategy`**: Decision-making layer. Strategies emit intents that flow through the risk engine for limit enforcement; execution wiring remains TODO.

## 📦 Installation & Setup

### Prerequisites

*   **Python 3.10+**: Ensure Python is installed and added to your PATH.
*   **Poetry**: The dependency manager used for this project.

### 🐧 Linux / macOS

1.  **Install Poetry** (if not already installed):
    ```bash
    curl -sSL https://install.python-poetry.org | python3 -
    ```

2.  **Clone and Install**:
    ```bash
    git clone <repo-url>
    cd kraken-bot
    poetry install
    ```

### 🪟 Windows 10 / 11

1.  **Install Python**:
    Download and install Python 3.10+ from [python.org](https://www.python.org/downloads/).
    *   *Important*: Check the box **"Add Python to PATH"** during installation.

2.  **Install Poetry** (via PowerShell):
    ```powershell
    (Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
    ```
    *   You may need to add `%APPDATA%\Python\Scripts` to your PATH if warned.

3.  **Clone and Install**:
    ```powershell
    git clone <repo-url>
    cd kraken-bot
    poetry install
    ```

## ⚙️ Configuration

The bot uses two configuration files stored in your OS-specific user configuration directory (handled via `appdirs`).

*   **Linux**: `~/.config/kraken_bot/`
*   **macOS**: `~/Library/Application Support/kraken_bot/`
*   **Windows**: `C:\Users\<User>\AppData\Local\kraken_bot\`

### Files

1.  **`config.yaml`** (User Config):
    *   Contains region settings, universe selection, strategy parameters, and risk limits.
    *   *Example schema available in `src/kraken_bot/config.py`*.

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
      min_order_notional_usd: 20.0    # Floor to avoid dust orders
    ```

2.  **`secrets.enc`** (Encrypted Credentials):
    *   Stores your Kraken API Key and Secret securely.
    *   **Setup**: The bot includes a setup utility (CLI) to prompt for keys and create this file. (Usage instructions coming in Phase 6).

### Bootstrap helper

Most modules can start with a ready REST client and parsed configuration using:

```python
from kraken_bot.bootstrap import bootstrap

client, app_config = bootstrap()
```

The helper calls `load_config()` and `secrets.load_api_keys(allow_interactive_setup=True)`, raising a `CredentialBootstrapError` when credentials are missing, cannot be decrypted (e.g., wrong `KRAKEN_BOT_SECRET_PW`), or fail validation. Interactive setups are only triggered when keys are missing, so non-interactive environments should supply `KRAKEN_API_KEY`/`KRAKEN_API_SECRET` or the decryption password to avoid errors.

### Strategy & Risk essentials (Phase 4)

To run the implemented Phase 4 features, set these keys in `config.yaml`:

*   **Strategy scheduling**: Provide `strategies.enabled` plus per-strategy `timeframes` (or `timeframe`) arrays to run multi-timeframe cycles. 【F:src/kraken_bot/strategy/engine.py†L80-L119】
*   **Per-strategy caps**: Configure `risk.max_per_strategy_pct` to clamp exposure across strategies and `strategies.configs.<name>.userref` if you need consistent attribution. 【F:src/kraken_bot/config.py†L48-L72】【F:src/kraken_bot/strategy/risk.py†L263-L349】
*   **Portfolio caps**: Use `risk.max_portfolio_risk_pct`, `risk.max_open_positions`, and `risk.max_per_asset_pct` to enforce total exposure limits. 【F:src/kraken_bot/config.py†L40-L72】【F:src/kraken_bot/strategy/risk.py†L263-L349】
*   **Liquidity gating**: Set `risk.min_liquidity_24h_usd` to block new exposure when recent volume is too low. 【F:src/kraken_bot/config.py†L60-L72】【F:src/kraken_bot/strategy/risk.py†L203-L249】
*   **Staleness handling**: Market data staleness and connection checks are enforced before intent generation; strategies surface `DataStaleError` to skip a timeframe when needed. 【F:src/kraken_bot/strategy/engine.py†L33-L120】

### Phase 5 handoffs

Phase 4 produces risk-adjusted actions but still relies on Phase 5 to wire order tagging and OMS submission. Execution hooks and tag propagation will arrive with the Phase 5 implementation.

#### Strategy ID propagation and tagging

`strategy_id` is carried end-to-end: strategies emit `StrategyIntent`, the risk engine normalizes that into `RiskAdjustedAction`, and the resulting `DecisionRecord` snapshots the same identifier for audit/history. Each strategy config supports an optional `userref` field in `StrategyConfig` to give the strategy a stable numeric tag; configure it now so Phase 5’s OMS can reuse it for Kraken order tagging and PnL attribution. OMS/userref plumbing itself is intentionally deferred to Phase 5, but providing `userref` early guarantees consistent attribution once execution wiring lands.

### 🧪 Paper / Validate Quickstart

`krakked run-once` is pinned to the safest defaults: `execution.mode="paper"`, `validate_only=True`, and `allow_live_trading=False`, even if your config requests otherwise. That means Kraken only validates orders without touching funds.

To run a single synchronous cycle:

```bash
poetry run krakked run-once
```

After the run, inspect orders/results in the default SQLite store (`portfolio.db` unless overridden) or via the admin helper:

```bash
# Review open/pending orders and execution summaries
poetry run python -m kraken_bot.execution.admin_cli list-open
poetry run python -m kraken_bot.execution.admin_cli recent-executions

# Targeted or global cancels (remains safe in paper/validate-only mode)
poetry run python -m kraken_bot.execution.admin_cli cancel --plan-id <id>
poetry run python -m kraken_bot.execution.admin_cli panic
```

To query SQLite directly:

```bash
sqlite3 portfolio.db \
  "SELECT plan_id, local_id, kraken_order_id, status, volume, price FROM execution_orders ORDER BY created_at DESC LIMIT 5;"
sqlite3 portfolio.db \
  "SELECT id, started_at, completed_at, status, summary FROM execution_results ORDER BY started_at DESC LIMIT 3;"
```

Key tables to review are `execution_orders` (every `LocalOrder` snapshot), `execution_order_events` (state transitions), and `execution_results` (per-plan outcome summaries). The admin CLI mirrors that data without requiring SQL.

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

Invoke via `poetry run python -m kraken_bot.execution.admin_cli <subcommand>`; pass `--db-path` to point at a non-default portfolio database or `--allow-interactive-setup` if credential prompts are acceptable.

### ✅ Live Readiness Checklist

* Paper mode: At least one full `krakked run-once` paper cycle completed without errors.
* Data review: SQLite `execution_orders` / `execution_results` inspected (or equivalent admin CLI checks) to verify sizing, tagging, and guardrails.
* Config gates: `execution.mode="live"`, `execution.validate_only=false`, and `execution.allow_live_trading=true` intentionally set for production; revert any gate to disable.
* Risk reviewed: Portfolio and per-strategy risk limits rechecked for live exposure tolerance.
* Operator drills: Team knows how to invoke `panic` and targeted `cancel` via `execution.admin_cli` for immediate kill-switch behavior.

## 🧪 Testing

To run the test suite:

```bash
poetry run pytest
```

*   **Live Tests**: Integration tests that hit the real Kraken API are skipped by default. To run them, set the environment variable `KRAKEN_LIVE_TESTS=1`.
    *   *Note*: Requires valid credentials configured.
