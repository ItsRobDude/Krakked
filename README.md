# Kraken Trading Bot

A modular, robust Kraken trading bot designed for spot trading (CA/USA) with a focus on safety, testing, and clean architecture.

## 🚀 Current Status

| Module | Status | Description |
| :--- | :--- | :--- |
| **Phase 1: Connection** | ✅ Completed | `KrakenRESTClient` (public/private), rate limiting, authentication, secrets management. |
| **Phase 2: Market Data** | ✅ Completed | `MarketDataAPI`, WebSocket V2 integration, historical OHLC backfilling, universe management. |
| **Phase 3: Portfolio** | ✅ Completed | `PortfolioService`, local SQLite persistence, PnL tracking (WAC), cash flow detection, snapshotting. |
| **Phase 4: Strategy & Risk** | ✅ Completed | `StrategyRiskEngine`, pluggable strategy framework, centralized risk limits (drawdown, exposure), decision persistence. |
| **Phase 5: Execution** | ⏳ Pending | Order Management System (OMS), trade execution, order lifecycle management. |
| **Phase 6: UI/Control** | ⏳ Pending | Web/CLI interface for monitoring and manual control. |

## 🏗️ Architecture

The bot is organized into distinct modules:

*   **`connection`**: Low-level API interaction (REST & WebSocket). Handles auth, signing, retries, and rate limits.
*   **`market_data`**: Abstracted data access. Provides a unified interface for real-time prices and historical candles.
*   **`portfolio`**: Accounting engine. Tracks balances, positions, and performance metrics locally to ensure a "source of truth" independent of exchange lag.
*   **`strategy`**: Decision-making brain.
    *   **Strategies** emit "intents" (e.g., "I want to be long XBT").
    *   **Risk Engine** aggregates intents, applies hard limits (max drawdown, per-asset cap), and produces a sanitized `ExecutionPlan`.

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

2.  **`secrets.enc`** (Encrypted Credentials):
    *   Stores your Kraken API Key and Secret securely.
    *   **Setup**: The bot includes a setup utility (CLI) to prompt for keys and create this file. (Usage instructions coming in Phase 6).

## 🧪 Testing

To run the test suite:

```bash
poetry run pytest
```

*   **Live Tests**: Integration tests that hit the real Kraken API are skipped by default. To run them, set the environment variable `KRAKEN_LIVE_TESTS=1`.
    *   *Note*: Requires valid credentials configured.
