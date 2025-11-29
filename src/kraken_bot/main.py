"""Long-running orchestrator entrypoint for the Kraken bot."""

from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import uvicorn

from kraken_bot.bootstrap import bootstrap
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.logging_config import configure_logging, structured_log_extra
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext
from kraken_bot.strategy.engine import StrategyEngine

logger = logging.getLogger(__name__)


def _coerce_interval(value: Optional[int], default: int, name: str) -> int:
    """Return a positive interval value with a safe default."""

    if isinstance(value, (int, float)) and value > 0:
        return int(value)

    logger.warning("Invalid %s; using default %ss", name, default)
    return default


def _start_ui_server(context: AppContext) -> Tuple[Optional[uvicorn.Server], Optional[threading.Thread]]:
    """Launch the FastAPI UI server in a background thread when enabled."""

    if not context.config.ui.enabled:
        logger.info("UI disabled by configuration; skipping API startup", extra=structured_log_extra(event="ui_disabled"))
        return None, None

    app = create_api(context)
    config = uvicorn.Config(app, host=context.config.ui.host, port=context.config.ui.port, log_level="info", log_config=None)
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    logger.info(
        "UI server started",
        extra=structured_log_extra(
            event="ui_started",
            host=context.config.ui.host,
            port=context.config.ui.port,
        ),
    )
    return server, thread


def _shutdown(
    context: AppContext,
    stop_event: threading.Event,
    ui_server: Optional[uvicorn.Server],
    ui_thread: Optional[threading.Thread],
) -> None:
    """Signal all loops to stop, halt the UI server, and cancel open orders."""

    if stop_event.is_set():
        logger.info("Shutdown already in progress")
    else:
        stop_event.set()

    if ui_server:
        ui_server.should_exit = True
    if ui_thread and ui_thread.is_alive():
        ui_thread.join(timeout=5)

    try:
        context.execution_service.cancel_all()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Error canceling open orders during shutdown: %s", exc)

    try:
        context.market_data.shutdown()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Error shutting down market data: %s", exc)

    logger.info("Shutdown complete")


def run(allow_interactive_setup: bool = True) -> int:
    """Bootstrap services, run scheduler loops, and host the UI API."""

    configure_logging(level=logging.INFO)
    stop_event = threading.Event()

    client, config, rate_limiter = bootstrap(allow_interactive_setup=allow_interactive_setup)

    market_data = MarketDataAPI(config, rest_client=client, rate_limiter=rate_limiter)
    market_data.initialize()

    portfolio = PortfolioService(config, market_data, rest_client=client, rate_limiter=rate_limiter)
    portfolio.initialize()

    strategy_engine = StrategyEngine(config, market_data, portfolio)
    strategy_engine.initialize()

    execution_service = ExecutionService(
        client=client,
        config=config.execution,
        market_data=market_data,
        store=portfolio.store,
        rate_limiter=rate_limiter,
    )

    context = AppContext(
        config=config,
        client=client,
        market_data=market_data,
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
    )

    ui_server, ui_thread = _start_ui_server(context)

    strategy_interval = _coerce_interval(getattr(config.strategies, "loop_interval_seconds", None), 60, "strategy interval")
    portfolio_interval = _coerce_interval(
        getattr(config.portfolio, "sync_interval_seconds", None), 300, "portfolio sync interval"
    )
    loop_interval = min(strategy_interval, portfolio_interval, 5)

    last_strategy_cycle = datetime.now(timezone.utc) - timedelta(seconds=strategy_interval)
    last_portfolio_sync = datetime.now(timezone.utc) - timedelta(seconds=portfolio_interval)

    def _signal_handler(signum, _frame) -> None:  # pragma: no cover - signal driven
        logger.info("Received signal %s; shutting down", signum, extra=structured_log_extra(event="shutdown_signal"))
        _shutdown(context, stop_event, ui_server, ui_thread)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        while not stop_event.is_set():
            now = datetime.now(timezone.utc)

            if (now - last_portfolio_sync).total_seconds() >= portfolio_interval:
                try:
                    portfolio.sync()
                    last_portfolio_sync = now
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.error("Portfolio sync failed: %s", exc)

            if (now - last_strategy_cycle).total_seconds() >= strategy_interval:
                try:
                    plan = strategy_engine.run_cycle(now)
                    last_strategy_cycle = now
                    if plan.actions:
                        execution_service.execute_plan(plan)
                    else:
                        logger.info(
                            "No actions generated for plan %s; skipping execution",
                            plan.plan_id,
                            extra=structured_log_extra(event="plan_skipped", plan_id=plan.plan_id),
                        )
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.error("Strategy cycle failed: %s", exc)

            stop_event.wait(loop_interval)
    finally:
        _shutdown(context, stop_event, ui_server, ui_thread)

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    run()
