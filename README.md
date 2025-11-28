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

## 🛠️ Development

### Prerequisites

*   Python 3.10+
*   Poetry

### Setup

```bash
poetry install
```

### Testing

```bash
poetry run pytest
```

### Configuration

Configuration is split into:

1.  **`config.yaml`** (User Config): Region, universe, strategy parameters, risk limits.
2.  **`secrets.enc`** (Encrypted Credentials): API keys and secrets.

See `src/kraken_bot/config.py` for schema details.
