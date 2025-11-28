# Kraken Trading Bot

A modular, robust Kraken trading bot designed for spot trading (CA/USA) with a focus on safety, testing, and clean architecture.

## 🚀 Current Status

This repository includes working, test-covered implementations for the early phases, but it is still a backend-only project. No execution layer or user interface has been built yet.

| Module | Status | Notes |
| :--- | :--- | :--- |
| **Phase 1: Connection** | ✅ Implemented | REST client with signed private calls, configurable rate limiting, nonce handling, and encrypted credential storage/validation. |
| **Phase 2: Market Data** | ✅ Implemented | Pair-universe discovery, OHLC backfill to a pluggable store, and WebSocket v2 streaming with staleness checks. |
| **Phase 3: Portfolio** | ✅ Implemented | Portfolio service with SQLite persistence, weighted-average cost PnL, fee tracking, and cashflow detection. |
| **Phase 4: Strategy & Risk** | ✅ Implemented | Strategy loader, intent/risk engine, and scenario simulations; no live execution wiring. |
| **Phase 5: Execution** | ⏳ Not started | Order Management System (OMS), trade execution, order lifecycle management. |
| **Phase 6: UI/Control** | ⏳ Not started | CLI/web interface for monitoring and manual control. |

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
