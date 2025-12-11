"""Long-running orchestrator entrypoint for the Kraken bot."""

from __future__ import annotations

import logging
import signal
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

import uvicorn

from kraken_bot import APP_VERSION
from kraken_bot.bootstrap import CredentialBootstrapError, bootstrap
from kraken_bot.config_loader import load_config
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.logging_config import (
    configure_logging,
    get_log_environment,
    structured_log_extra,
)
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.metrics import SystemMetrics
from kraken_bot.portfolio.exceptions import PortfolioSchemaError
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.portfolio.models import DriftStatus
from kraken_bot.portfolio.store import (
    CURRENT_SCHEMA_VERSION,
    assert_portfolio_schema,
    ensure_portfolio_schema,
    ensure_portfolio_tables,
)
from kraken_bot.strategy.engine import StrategyEngine
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext, SessionState

logger = logging.getLogger(__name__)


def _coerce_interval(value: Optional[int], default: int, name: str) -> int:
    """Return a positive interval value with a safe default."""

    if isinstance(value, (int, float)) and value > 0:
        return int(value)

    logger.warning("Invalid %s; using default %ss", name, default)
    return default


def _start_ui_server(
    context: AppContext,
) -> Tuple[Optional[uvicorn.Server], Optional[threading.Thread]]:
    """Launch the FastAPI UI server in a background thread when enabled."""

    if not context.config.ui.enabled:
        logger.info(
            "UI disabled by configuration; skipping API startup",
            extra=structured_log_extra(event="ui_disabled"),
        )
        return None, None

    app = create_api(context)
    config = uvicorn.Config(
        app,
        host=context.config.ui.host,
        port=context.config.ui.port,
        log_level="info",
        log_config=None,
    )
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
    context: Optional[AppContext],
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
    if context and isinstance(context.metrics, SystemMetrics):
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
        portfolio_db_path=getattr(
            getattr(context.portfolio if context else None, "store", None),
            "db_path",
            None,
        ),
    )

    if metrics_snapshot:
        shutdown_extra.update(
            last_equity_usd=metrics_snapshot.get("last_equity_usd"),
            open_positions_count=metrics_snapshot.get("open_positions_count"),
            open_orders_count=metrics_snapshot.get("open_orders_count"),
        )

    log_message = (
        "Initiating shutdown" if first_shutdown else "Shutdown already in progress"
    )
    logger.info(log_message, extra=shutdown_extra)

    if ui_server:
        ui_server.should_exit = True
    if ui_thread and ui_thread.is_alive():
        ui_thread.join(timeout=5)

    if context and context.execution_service:
        try:
            context.execution_service.cancel_all()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Error canceling open orders during shutdown: %s", exc)

    if context and context.market_data:
        try:
            context.market_data.shutdown()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Error shutting down market data: %s", exc)

    logger.info(
        "Shutdown complete",
        extra=structured_log_extra(event="shutdown_complete", reason=reason),
    )


def _refresh_metrics_state(
    portfolio: PortfolioService,
    execution_service: ExecutionService,
    metrics: SystemMetrics,
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
    session_active: bool,
) -> Tuple[datetime, datetime]:
    """Execute a single scheduling iteration and return updated timestamps."""

    updated_portfolio_sync = last_portfolio_sync
    updated_strategy_cycle = last_strategy_cycle

    if (now - last_portfolio_sync).total_seconds() >= portfolio_interval:
        try:
            portfolio.sync()
            if portfolio.last_sync_ok:
                updated_portfolio_sync = now
                refresh_metrics_state()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Portfolio sync failed: %s", exc)
            metrics.record_error(f"Portfolio sync failed: {exc}")

    if not portfolio.last_sync_ok:
        logger.error(
            "Skipping loop iteration: portfolio sync failed",
            extra=structured_log_extra(event="portfolio_sync_failed"),
        )
        metrics.record_error("Portfolio sync failed")
        refresh_metrics_state()
        return updated_portfolio_sync, updated_strategy_cycle

    try:
        data_status = market_data.get_health_status()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to evaluate market data health: %s", exc)
        metrics.record_market_data_error(
            f"Failed to evaluate market data health: {exc}"
        )
        data_status = None

    market_data_ok = bool(
        data_status and getattr(data_status, "health", "") == "healthy"
    )
    market_data_stale = bool(
        data_status and getattr(data_status, "health", "") == "stale"
    )
    metrics.update_market_data_status(
        ok=market_data_ok,
        stale=market_data_stale,
        reason=getattr(
            data_status, "reason", "unknown" if data_status is None else None
        ),
        max_staleness=getattr(data_status, "max_staleness", None),
    )

    drift_status = _get_portfolio_drift(portfolio)
    if drift_status:
        drift_message = None
        if drift_status.drift_flag:
            drift_message = (
                "Portfolio drift detected: expected position value %.2f vs balances %.2f"
                % (
                    drift_status.expected_position_value_base,
                    drift_status.actual_balance_value_base,
                )
            )
            logger.warning(
                drift_message,
                extra=structured_log_extra(
                    event="portfolio_drift_detected",
                    expected_position_value_base=drift_status.expected_position_value_base,
                    actual_balance_value_base=drift_status.actual_balance_value_base,
                    tolerance_base=drift_status.tolerance_base,
                    mismatched_assets=[
                        asdict(asset) for asset in drift_status.mismatched_assets
                    ],
                ),
            )

            risk_config = getattr(
                getattr(strategy_engine, "config", None), "risk", None
            )
            if getattr(risk_config, "kill_switch_on_drift", False):
                activate_kill_switch = getattr(
                    strategy_engine, "set_manual_kill_switch", None
                )
                if callable(activate_kill_switch):
                    try:
                        activate_kill_switch(True)
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.error("Failed to activate kill switch on drift: %s", exc)
        metrics.record_drift(drift_status.drift_flag, drift_message)
    else:
        metrics.record_drift(False)

    if (now - last_strategy_cycle).total_seconds() >= strategy_interval:
        if not session_active:
            logger.debug("Skipping strategy cycle: session not active")
            updated_strategy_cycle = now
        elif not market_data_ok:
            reason = (
                getattr(data_status, "reason", "unknown") if data_status else "unknown"
            )
            max_staleness = (
                getattr(data_status, "max_staleness", None) if data_status else None
            )
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
                blocked_actions = len(
                    [a for a in plan.actions if getattr(a, "blocked", False)]
                )
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
                        extra=structured_log_extra(
                            event="plan_skipped", plan_id=plan.plan_id
                        ),
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
    db_path = "portfolio.db"
    schema_status = None

    # Context components (mutable references for reloading)
    context_ref: list[Optional[AppContext]] = [None]
    ui_server_ref: list[Optional[uvicorn.Server]] = [None]
    ui_thread_ref: list[Optional[threading.Thread]] = [None]

    # Flags to control the main loop state
    is_setup_mode = False

    def _bootstrap_and_create_context() -> AppContext:
        """
        Attempts to bootstrap the application.
        If it fails due to credentials/config, returns a setup-mode context.
        """
        nonlocal is_setup_mode
        try:
            # We disable interactive setup in headless/daemon mode to force
            # CredentialBootstrapError if keys are missing.
            client, config, rate_limiter = bootstrap(
                allow_interactive_setup=allow_interactive_setup
            )

            db_path = getattr(
                getattr(config, "portfolio", None), "db_path", "portfolio.db"
            )
            auto_migrate = config.portfolio.auto_migrate_schema
            execution_mode = getattr(config.execution, "mode", "unknown")

            if auto_migrate and execution_mode in {"paper", "live"}:
                raise RuntimeError(
                    "auto_migrate_schema must be False in paper/live mode. "
                    "Run `krakked portfolio-migrate` to upgrade the DB schema first."
                )

            if auto_migrate:
                schema_status_local = ensure_portfolio_schema(
                    db_path, CURRENT_SCHEMA_VERSION, migrate=True
                )
                with sqlite3.connect(db_path) as conn:
                    ensure_portfolio_tables(conn)
                    conn.commit()
            else:
                schema_status_local = assert_portfolio_schema(db_path)
                # Assign to outer scope if needed, though mostly for logging
                nonlocal schema_status
                schema_status = schema_status_local

            market_data = MarketDataAPI(
                config, rest_client=client, rate_limiter=rate_limiter
            )
            market_data.initialize()

            portfolio = PortfolioService(
                config,
                market_data,
                db_path=db_path,
                rest_client=client,
                rate_limiter=rate_limiter,
            )
            portfolio.initialize()

            logger.info(
                "Portfolio schema ready",
                extra=structured_log_extra(
                    event="schema_status",
                    env=get_log_environment(),
                    schema_version=portfolio.store.get_schema_version(),
                ),
            )

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

            session_state = SessionState(
                active=config.session.active,
                mode=config.session.mode,
                loop_interval_sec=config.session.loop_interval_sec,
                profile_name=config.session.profile_name,
                ml_enabled=config.session.ml_enabled,
            )

            is_setup_mode = False
            return AppContext(
                config=config,
                client=client,
                market_data=market_data,
                portfolio_service=portfolio,
                portfolio=portfolio,
                strategy_engine=strategy_engine,
                execution_service=execution_service,
                metrics=metrics,
                session=session_state,
                is_setup_mode=False,
            )

        except (CredentialBootstrapError, FileNotFoundError) as exc:
            logger.warning(
                "Bootstrap failed (%s); entering setup mode",
                exc,
                extra=structured_log_extra(event="enter_setup_mode"),
            )
            # Load basic config just for UI settings if available, else default
            config = load_config()
            is_setup_mode = True
            return AppContext(
                config=config,
                client=None,
                market_data=None,
                portfolio_service=None,
                portfolio=None,
                strategy_engine=None,
                execution_service=None,
                metrics=None,
                is_setup_mode=True,
            )

    try:
        # Initial Bootstrap
        context = _bootstrap_and_create_context()
        context_ref[0] = context

        logger.info(
            "Starting Kraken bot",
            extra=structured_log_extra(
                event="startup",
                env=get_log_environment(),
                app_version=APP_VERSION,
                setup_mode=is_setup_mode,
            ),
        )

        ui_server, ui_thread = _start_ui_server(context)
        ui_server_ref[0] = ui_server
        ui_thread_ref[0] = ui_thread

        def _signal_handler(signum, _frame) -> None:
            _shutdown(
                context_ref[0],
                stop_event,
                ui_server_ref[0],
                ui_thread_ref[0],
                reason="signal",
                signal_number=signum,
            )

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        while not stop_event.is_set():
            if is_setup_mode:
                # Wait for UI to signal re-initialization
                logger.info("Waiting for setup completion...")
                context.reinitialize_event.wait(timeout=1.0)

                if context.reinitialize_event.is_set():
                    logger.info("Re-initialization signal received. reloading...")
                    # Stop old services if any (unlikely in setup mode, but safe)
                    if context.market_data:
                        context.market_data.shutdown()

                    # Re-run bootstrap
                    try:
                        new_context = _bootstrap_and_create_context()
                        # Update the global context reference in place if possible,
                        # or update the app.state.context reference if we can reach it.
                        # The UI holds a reference to 'context'. We should update its internals.
                        # But AppContext is immutable dataclass mostly.
                        # Actually, we can just replace the object in app.state
                        # BUT the UI server is running in a thread with the old app instance.
                        # We must update the attributes of the existing context object?
                        # Dataclasses are mutable by default unless frozen=True.
                        # AppContext is not frozen.

                        context.config = new_context.config
                        context.client = new_context.client
                        context.market_data = new_context.market_data
                        context.portfolio = new_context.portfolio
                        context.portfolio_service = new_context.portfolio_service
                        context.strategy_engine = new_context.strategy_engine
                        context.execution_service = new_context.execution_service
                        context.metrics = new_context.metrics
                        context.session = new_context.session
                        context.is_setup_mode = new_context.is_setup_mode
                        context.reinitialize_event.clear()

                        # Re-evaluate setup mode flag
                        is_setup_mode = new_context.is_setup_mode

                        if not is_setup_mode:
                            logger.info(
                                "System successfully initialized from setup mode",
                                extra=structured_log_extra(event="setup_complete"),
                            )
                        else:
                            logger.warning(
                                "Re-initialization failed to clear setup mode",
                                extra=structured_log_extra(event="setup_failed_retry"),
                            )

                    except Exception as e:
                        logger.exception(
                            "Critical error during re-initialization",
                            extra=structured_log_extra(event="reinit_error"),
                        )
                        # Keep waiting in setup mode
                        context.reinitialize_event.clear()

                continue

            # --- Normal Operation Loop ---
            strategy_interval = _coerce_interval(
                getattr(context.config.strategies, "loop_interval_seconds", None),
                60,
                "strategy interval",
            )
            portfolio_interval = _coerce_interval(
                getattr(context.config.portfolio, "sync_interval_seconds", None),
                300,
                "portfolio sync interval",
            )

            # Initialize timers if fresh from setup
            if "last_strategy_cycle" not in locals():
                last_strategy_cycle = datetime.now(timezone.utc) - timedelta(
                    seconds=strategy_interval
                )
            if "last_portfolio_sync" not in locals():
                last_portfolio_sync = datetime.now(timezone.utc) - timedelta(
                    seconds=portfolio_interval
                )

            # Define refresh closure locally to capture current context
            def refresh_metrics_state() -> None:
                if context.portfolio and context.execution_service and context.metrics:
                    _refresh_metrics_state(
                        context.portfolio, context.execution_service, context.metrics
                    )

            now = datetime.now(timezone.utc)

            if (
                context.portfolio
                and context.market_data
                and context.strategy_engine
                and context.execution_service
                and context.metrics
            ):
                last_portfolio_sync, last_strategy_cycle = _run_loop_iteration(
                    now=now,
                    strategy_interval=strategy_interval,
                    portfolio_interval=portfolio_interval,
                    last_strategy_cycle=last_strategy_cycle,
                    last_portfolio_sync=last_portfolio_sync,
                    portfolio=context.portfolio,
                    market_data=context.market_data,
                    strategy_engine=context.strategy_engine,
                    execution_service=context.execution_service,
                    metrics=context.metrics,
                    refresh_metrics_state=refresh_metrics_state,
                    session_active=context.session.active,
                )
            else:
                logger.error(
                    "Invalid state: not in setup mode but services are missing; forcing setup mode"
                )
                is_setup_mode = True
                continue

            session_interval = getattr(context.session, "loop_interval_sec", None)
            loop_interval = _coerce_interval(
                int(session_interval) if session_interval is not None else None,
                min(strategy_interval, portfolio_interval, 5),
                "session loop interval",
            )

            stop_event.wait(loop_interval)

    except PortfolioSchemaError as exc:
        logger.critical(
            "Portfolio schema mismatch: %s",
            exc,
            extra=structured_log_extra(event="schema_mismatch"),
        )
        return 1
    except RuntimeError as exc:
        logger.critical(
            "%s", exc, extra=structured_log_extra(event="schema_guard_failed")
        )
        return 1
    finally:
        _shutdown(
            context_ref[0],
            stop_event,
            ui_server_ref[0],
            ui_thread_ref[0],
            reason="loop_exit",
        )

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    run()
