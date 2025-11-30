"""Long-running orchestrator entrypoint for the Kraken bot."""

from __future__ import annotations

import logging
import signal
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

import uvicorn

from kraken_bot import APP_VERSION
from kraken_bot.bootstrap import bootstrap
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.logging_config import configure_logging, get_log_environment, structured_log_extra
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.metrics import SystemMetrics
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import DriftStatus
from kraken_bot.portfolio.exceptions import PortfolioSchemaError
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
    *,
    reason: str = "exit",
    signal_number: Optional[int] = None,
) -> None:
    """Signal all loops to stop, halt the UI server, and cancel open orders."""

    first_shutdown = not stop_event.is_set()
    stop_event.set()

    metrics_snapshot = None
    if isinstance(context.metrics, SystemMetrics):
        metrics_snapshot = context.metrics.snapshot()

    shutdown_extra = structured_log_extra(
        event="shutdown",
        reason=reason,
        signal_number=signal_number,
        components={
            "ui_server": bool(ui_server),
            "market_data": True,
            "execution_service": True,
            "strategy_engine": True,
        },
        portfolio_db_path=getattr(getattr(context.portfolio, "store", None), "db_path", None),
    )

    if metrics_snapshot:
        shutdown_extra.update(
            last_equity_usd=metrics_snapshot.get("last_equity_usd"),
            open_positions_count=metrics_snapshot.get("open_positions_count"),
            open_orders_count=metrics_snapshot.get("open_orders_count"),
        )

    log_message = "Initiating shutdown" if first_shutdown else "Shutdown already in progress"
    logger.info(log_message, extra=shutdown_extra)

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

    logger.info("Shutdown complete", extra=structured_log_extra(event="shutdown_complete", reason=reason))


def _refresh_metrics_state(
    portfolio: PortfolioService, execution_service: ExecutionService, metrics: SystemMetrics
) -> None:
    try:
        equity = portfolio.get_equity()
        positions = portfolio.get_positions()
        open_orders = execution_service.get_open_orders()
        metrics.update_portfolio_state(
            equity_usd=equity.equity_base,
            realized_pnl_usd=equity.realized_pnl_base_total,
            unrealized_pnl_usd=equity.unrealized_pnl_base_total,
            open_orders_count=len(open_orders),
            open_positions_count=len(positions),
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to refresh metrics state: %s", exc)
        metrics.record_error(f"Failed to refresh metrics state: {exc}")


def _get_portfolio_drift(portfolio: PortfolioService) -> Optional[DriftStatus]:
    """Safely retrieve the current drift status from the portfolio service."""

    try:
        drift_status = portfolio.get_drift_status()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to fetch portfolio drift status: %s", exc)
        return None

    if isinstance(drift_status, DriftStatus):
        return drift_status

    return None


def _run_loop_iteration(
    *,
    now: datetime,
    strategy_interval: int,
    portfolio_interval: int,
    last_strategy_cycle: datetime,
    last_portfolio_sync: datetime,
    portfolio: PortfolioService,
    market_data: MarketDataAPI,
    strategy_engine: StrategyEngine,
    execution_service: ExecutionService,
    metrics: SystemMetrics,
    refresh_metrics_state: Callable[[], None],
) -> Tuple[datetime, datetime]:
    """Execute a single scheduling iteration and return updated timestamps."""

    updated_portfolio_sync = last_portfolio_sync
    updated_strategy_cycle = last_strategy_cycle

    try:
        data_status = market_data.get_health_status()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to evaluate market data health: %s", exc)
        metrics.record_market_data_error(f"Failed to evaluate market data health: {exc}")
        data_status = None

    market_data_ok = bool(data_status and getattr(data_status, "health", "") == "healthy")
    market_data_stale = bool(data_status and getattr(data_status, "health", "") == "stale")
    metrics.update_market_data_status(
        ok=market_data_ok,
        stale=market_data_stale,
        reason=getattr(data_status, "reason", "unknown" if data_status is None else None),
        max_staleness=getattr(data_status, "max_staleness", None),
    )

    if (now - last_portfolio_sync).total_seconds() >= portfolio_interval:
        try:
            portfolio.sync()
            updated_portfolio_sync = now
            refresh_metrics_state()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Portfolio sync failed: %s", exc)
            metrics.record_error(f"Portfolio sync failed: {exc}")

    drift_status = _get_portfolio_drift(portfolio)
    if drift_status:
        drift_message = None
        if drift_status.drift_flag:
            drift_message = (
                "Portfolio drift detected: expected position value %.2f vs balances %.2f"
                % (drift_status.expected_position_value_base, drift_status.actual_balance_value_base)
            )
            logger.warning(
                drift_message,
                extra=structured_log_extra(
                    event="portfolio_drift_detected",
                    expected_position_value_base=drift_status.expected_position_value_base,
                    actual_balance_value_base=drift_status.actual_balance_value_base,
                    tolerance_base=drift_status.tolerance_base,
                    mismatched_assets=[asdict(asset) for asset in drift_status.mismatched_assets],
                ),
            )

            risk_config = getattr(getattr(strategy_engine, "config", None), "risk", None)
            if getattr(risk_config, "kill_switch_on_drift", False):
                activate_kill_switch = getattr(strategy_engine, "set_manual_kill_switch", None)
                if callable(activate_kill_switch):
                    try:
                        activate_kill_switch(True)
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.error("Failed to activate kill switch on drift: %s", exc)
        metrics.record_drift(drift_status.drift_flag, drift_message)
    else:
        metrics.record_drift(False)

    if (now - last_strategy_cycle).total_seconds() >= strategy_interval:
        if not market_data_ok:
            reason = getattr(data_status, "reason", "unknown") if data_status else "unknown"
            max_staleness = getattr(data_status, "max_staleness", None) if data_status else None
            log_extra = {"event": "market_data_unavailable", "reason": reason}
            if max_staleness is not None:
                log_extra["max_staleness"] = max_staleness
            logger.warning(
                "Skipping strategy cycle due to market data health: %s",
                getattr(data_status, "health", "unknown"),
                extra=structured_log_extra(**log_extra),
            )
            message = f"Market data unavailable ({reason})"
            if max_staleness is not None:
                message = f"{message}; stale for {max_staleness:.2f}s"
            metrics.record_market_data_error(message)
            refresh_metrics_state()
        else:
            try:
                plan = strategy_engine.run_cycle(now)
                blocked_actions = len([a for a in plan.actions if getattr(a, "blocked", False)])
                metrics.record_plan(blocked_actions)
                updated_strategy_cycle = now
                result = None
                if plan.actions:
                    result = execution_service.execute_plan(plan)
                    kill_switch_rejections = [
                        order
                        for order in getattr(result, "orders", [])
                        if order.status == "rejected"
                        and isinstance(order.last_error, str)
                        and "kill_switch_active" in order.last_error
                    ]
                    if kill_switch_rejections:
                        metrics.record_blocked_actions(len(kill_switch_rejections))
                    metrics.record_plan_execution(result.errors)
                else:
                    logger.info(
                        "No actions generated for plan %s; skipping execution",
                        plan.plan_id,
                        extra=structured_log_extra(event="plan_skipped", plan_id=plan.plan_id),
                    )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error("Strategy cycle failed: %s", exc)
                metrics.record_error(f"Strategy cycle failed: {exc}")
            else:
                refresh_metrics_state()

    return updated_portfolio_sync, updated_strategy_cycle


def run(allow_interactive_setup: bool = True) -> int:
    """Bootstrap services, run scheduler loops, and host the UI API."""

    configure_logging(level=logging.INFO)
    stop_event = threading.Event()

    try:
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
            risk_status_provider=strategy_engine.get_risk_status,
        )

        metrics = SystemMetrics()

        context = AppContext(
            config=config,
            client=client,
            market_data=market_data,
            portfolio=portfolio,
            strategy_engine=strategy_engine,
            execution_service=execution_service,
            metrics=metrics,
        )
    except PortfolioSchemaError as exc:
        logger.error("Portfolio store schema mismatch: %s", exc)
        return 1

    logger.info(
        "Starting Kraken bot",
        extra=structured_log_extra(
            event="startup",
            env=get_log_environment(),
            app_version=APP_VERSION,
            execution_mode=getattr(config.execution, "mode", "unknown"),
            portfolio_db_path=getattr(portfolio.store, "db_path", None),
            schema_version=getattr(portfolio.store, "get_schema_version", lambda: None)(),
        ),
    )

    ui_server, ui_thread = _start_ui_server(context)

    strategy_interval = _coerce_interval(getattr(config.strategies, "loop_interval_seconds", None), 60, "strategy interval")
    portfolio_interval = _coerce_interval(
        getattr(config.portfolio, "sync_interval_seconds", None), 300, "portfolio sync interval"
    )
    loop_interval = min(strategy_interval, portfolio_interval, 5)

    refresh_metrics_state = lambda: _refresh_metrics_state(portfolio, execution_service, metrics)

    last_strategy_cycle = datetime.now(timezone.utc) - timedelta(seconds=strategy_interval)
    last_portfolio_sync = datetime.now(timezone.utc) - timedelta(seconds=portfolio_interval)

    def _signal_handler(signum, _frame) -> None:  # pragma: no cover - signal driven
        _shutdown(context, stop_event, ui_server, ui_thread, reason="signal", signal_number=signum)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        while not stop_event.is_set():
            now = datetime.now(timezone.utc)

            last_portfolio_sync, last_strategy_cycle = _run_loop_iteration(
                now=now,
                strategy_interval=strategy_interval,
                portfolio_interval=portfolio_interval,
                last_strategy_cycle=last_strategy_cycle,
                last_portfolio_sync=last_portfolio_sync,
                portfolio=portfolio,
                market_data=market_data,
                strategy_engine=strategy_engine,
                execution_service=execution_service,
                metrics=metrics,
                refresh_metrics_state=refresh_metrics_state,
            )

            stop_event.wait(loop_interval)
    finally:
        _shutdown(context, stop_event, ui_server, ui_thread, reason="loop_exit")

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    run()
