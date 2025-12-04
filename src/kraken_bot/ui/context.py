"""UI application context helpers."""

from dataclasses import dataclass, field
from typing import Optional

from kraken_bot.bootstrap import bootstrap
from kraken_bot.config import AppConfig
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.metrics import SystemMetrics
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.engine import StrategyEngine


@dataclass
class SessionState:
    """Runtime session state controlling the trading loop and mode."""

    active: bool = False
    mode: str = "paper"
    loop_interval_sec: float = 15.0
    profile_name: Optional[str] = None
    ml_enabled: bool = True


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
    session: SessionState = field(default_factory=SessionState)


def build_app_context(allow_interactive_setup: bool = True) -> AppContext:
    """Instantiate and initialize services for UI workflows.

    Args:
        allow_interactive_setup: Whether credential loading may prompt for setup
            when API keys are missing.

    Returns:
        A fully initialized :class:`AppContext`.
    """

    client, config, rate_limiter = bootstrap(
        allow_interactive_setup=allow_interactive_setup
    )

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

    session = SessionState(
        active=config.session.active,
        mode=config.session.mode,
        loop_interval_sec=config.session.loop_interval_sec,
        profile_name=config.session.profile_name,
        ml_enabled=config.session.ml_enabled,
    )

    return AppContext(
        config=config,
        client=client,
        market_data=market_data,
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        session=session,
    )


__all__ = ["AppContext", "build_app_context"]
