"""One-shot orchestrator for running a full decision + execution cycle.

This helper intentionally avoids long-running background processes and forces
execution into a safe paper-validation mode. It:

1. Loads configuration and authenticated REST client via :func:`bootstrap`.
2. Refreshes the market-data universe and backfills OHLC needed for strategies.
3. Syncs the portfolio to capture a current snapshot.
4. Builds a single execution plan via :class:`StrategyEngine` and executes it
   through :class:`ExecutionService`.
"""

from __future__ import annotations

import copy
import logging
from typing import Iterable

from kraken_bot.bootstrap import bootstrap
from kraken_bot.config import AppConfig
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.engine import StrategyEngine

logger = logging.getLogger(__name__)


class _WsStub:
    """Minimal stand-in to satisfy connectivity checks without starting a loop."""

    is_connected: bool = True
    last_ticker_update_ts: dict = {}
    ohlc_cache: dict = {}
    last_ohlc_update_ts: dict = {}



def _ensure_safe_execution(config: AppConfig) -> AppConfig:
    """Force execution settings into paper/validation mode for safety."""

    safe_config = copy.deepcopy(config)
    safe_config.execution.mode = "paper"
    safe_config.execution.validate_only = True
    safe_config.execution.allow_live_trading = False
    logger.info(
        "Overriding execution config to paper mode with validation-only and live-trading guard"
    )
    return safe_config



def _backfill_pairs(market_data: MarketDataAPI, pairs: Iterable[str], timeframes: Iterable[str]) -> None:
    for pair in pairs:
        for timeframe in timeframes:
            try:
                market_data.backfill_ohlc(pair, timeframe)
            except Exception as exc:  # pragma: no cover - defensive logging only
                logger.warning("Failed to backfill %s %s: %s", pair, timeframe, exc)



def run_strategy_once() -> None:
    """Run a synchronous strategy + execution cycle in a safe, non-live mode."""

    client, config = bootstrap(allow_interactive_setup=False)
    safe_config = _ensure_safe_execution(config)

    market_data = MarketDataAPI(safe_config)
    market_data.refresh_universe()
    _backfill_pairs(
        market_data,
        market_data.get_universe(),
        safe_config.market_data.backfill_timeframes,
    )

    # Avoid spinning up the websocket loop while still satisfying connectivity checks
    market_data._ws_client = _WsStub()  # type: ignore[attr-defined]

    portfolio = PortfolioService(safe_config, market_data)
    portfolio.rest_client = client
    portfolio.initialize()

    strategy_engine = StrategyEngine(safe_config, market_data, portfolio)
    strategy_engine.initialize()
    plan = strategy_engine.run_cycle()

    execution_service = ExecutionService(client=client, config=safe_config.execution)
    result = execution_service.execute_plan(plan)

    logger.info("Plan %s executed. success=%s errors=%s", plan.plan_id, result.success, result.errors)


if __name__ == "__main__":
    run_strategy_once()
