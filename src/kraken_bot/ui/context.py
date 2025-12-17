"""UI application context helpers."""

import threading
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
    emergency_flatten: bool = False


@dataclass
class AppContext:
    """Bundled services and configuration for UI consumers."""

    config: AppConfig
    client: Optional[KrakenRESTClient]
    market_data: Optional[MarketDataAPI]
    portfolio_service: Optional[PortfolioService]
    portfolio: Optional[PortfolioService]
    strategy_engine: Optional[StrategyEngine]
    execution_service: Optional[ExecutionService]
    metrics: Optional[SystemMetrics]
    session: SessionState = field(default_factory=SessionState)
    is_setup_mode: bool = False
    reinitialize_event: threading.Event = field(default_factory=threading.Event)


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

    auto_migrate = config.portfolio.auto_migrate_schema
    execution_mode = getattr(config.execution, "mode", "unknown")

    if auto_migrate and execution_mode in {"paper", "live"}:
        raise RuntimeError(
            "auto_migrate_schema must be False in paper/live mode. "
            "Run `krakked portfolio-migrate` to upgrade the DB schema first."
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
        emergency_flatten=config.session.emergency_flatten,
    )

    return AppContext(
        config=config,
        client=client,
        market_data=market_data,
        portfolio_service=portfolio,
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        session=session,
    )


__all__ = ["AppContext", "build_app_context"]
