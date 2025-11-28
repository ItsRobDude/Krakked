# Kraken Trading Bot

A robust, modular automated trading bot for Kraken (Spot, CA/USA compliant), built with Python.

## Project Status

| Phase | Description | Status |
| :--- | :--- | :--- |
| **Phase 1** | Connection, Authentication & Region Safety | ✅ Completed |
| **Phase 2** | Market Data (REST & WebSocket) | ✅ Completed |
| **Phase 3** | Portfolio & PnL Engine | ✅ Completed |
| **Phase 4** | Risk & Strategy Engine | ⏳ Upcoming |
| **Phase 5** | Execution & OMS | ⏳ Upcoming |

## Architecture

The project is structured into modular components within `src/kraken_bot/`:

*   **`connection/`**: Handles REST/WebSocket connectivity, authentication, rate limiting, and robust error handling.
*   **`market_data/`**: Manages the pair universe, real-time WebSocket data, and historical OHLC data storage.
*   **`portfolio/`**: Tracks positions, balances, and PnL using a local SQLite store (`portfolio.db`). Supports strategy tagging and audit trails.
*   **`config.py`**: Centralized configuration management.

## Setup

1.  **Install Dependencies**:
    ```bash
    poetry install
    ```

2.  **Configuration**:
    *   The bot uses `config.yaml` (in your user config directory, e.g., `~/.config/kraken_bot/` or `%LOCALAPPDATA%\kraken_bot\`).
    *   API Credentials are encrypted in `secrets.enc`.

3.  **Testing**:
    ```bash
    poetry run pytest
    ```

## Phase 3 Features (Portfolio)

*   **Local Persistence**: Trades and orders are stored in `portfolio.db` (SQLite) for fast access and state reconstruction.
*   **Strategy Tagging**: Trades are tagged based on their parent order's `userref` (mapped via config) to distinguish between manual trades and different bot strategies.
*   **PnL Calculation**: Real-time equity and realized/unrealized PnL tracking using Weighted Average Cost (WAC).
*   **Safety**: Strict schema management and drift detection against live Kraken balances.
