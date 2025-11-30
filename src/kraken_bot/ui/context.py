"""UI application context helpers."""

from dataclasses import dataclass

from kraken_bot.bootstrap import bootstrap
from kraken_bot.config import AppConfig
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.metrics import SystemMetrics
from kraken_bot.strategy.engine import StrategyEngine


@dataclass
class AppContext:
    """Bundled services and configuration for UI consumers."""

    config: AppConfig
    client: KrakenRESTClient
    market_data: MarketDataAPI
    portfolio: PortfolioService
    strategy_engine: StrategyEngine
    execution_service: ExecutionService
    metrics: SystemMetrics


def build_app_context(allow_interactive_setup: bool = True) -> AppContext:
    """Instantiate and initialize services for UI workflows.

    Args:
        allow_interactive_setup: Whether credential loading may prompt for setup
            when API keys are missing.

    Returns:
        A fully initialized :class:`AppContext`.
    """

    client, config, rate_limiter = bootstrap(allow_interactive_setup=allow_interactive_setup)

    market_data = MarketDataAPI(config, rate_limiter=rate_limiter)
    market_data.refresh_universe()

    portfolio = PortfolioService(
        config, market_data, rest_client=client, rate_limiter=rate_limiter
    )
    portfolio.initialize()

    strategy_engine = StrategyEngine(config, market_data, portfolio)
    strategy_engine.initialize()

    execution_service = ExecutionService(
        client=client,
        config=config.execution,
        market_data=market_data,
        store=portfolio.store,
        rate_limiter=rate_limiter,
        risk_status_provider=strategy_engine.get_risk_status,
    )

    metrics = SystemMetrics()

    return AppContext(
        config=config,
        client=client,
        market_data=market_data,
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
    )


__all__ = ["AppContext", "build_app_context"]
