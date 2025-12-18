"""Long-running orchestrator entrypoint for the Kraken bot."""

from __future__ import annotations

import logging
import signal
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Tuple

import uvicorn

from kraken_bot import APP_VERSION
from kraken_bot.bootstrap import CredentialBootstrapError, bootstrap
from kraken_bot.config_loader import (
    dump_runtime_overrides,
    get_config_dir,
    load_config,
    write_initial_config,
)
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
    session: Optional[Any] = None,
) -> Tuple[datetime, datetime]:
    """Execute a single scheduling iteration and return updated timestamps."""

    updated_portfolio_sync = last_portfolio_sync
    updated_strategy_cycle = last_strategy_cycle

    # 1. Emergency Flatten Priority Loop
    emergency_flatten = bool(getattr(session, "emergency_flatten", False))
    if emergency_flatten:
        logger.warning(
            "Emergency flatten active; attempting to close all positions",
            extra=structured_log_extra(event="emergency_flatten_active"),
        )
        try:
            portfolio.sync()
        except Exception as exc:  # pragma: no cover
            metrics.record_error(f"Emergency flatten portfolio sync failed: {exc}")

        cancel_ok = True
        try:
            execution_service.cancel_all()
        except Exception as exc:  # pragma: no cover
            cancel_ok = False
            metrics.record_error(f"Emergency flatten cancel_all failed: {exc}")

        try:
            execution_service.refresh_open_orders()
            execution_service.reconcile_orders()
        except Exception as exc:  # pragma: no cover
            metrics.record_error(f"Emergency flatten reconcile failed: {exc}")

        positions = []
        try:
            positions = portfolio.get_positions()
        except Exception:  # pragma: no cover
            positions = []

        open_orders: Optional[List[LocalOrder]] = None
        try:
            open_orders = execution_service.get_open_orders()
        except Exception:  # pragma: no cover
            open_orders = None

        # Only execute flatten plan if it is safe to do so:
        # 1. cancel_all succeeded
        # 2. Open orders were successfully fetched AND are empty
        # 3. Portfolio sync was successful (last_sync_ok)
        if cancel_ok and open_orders is not None and not open_orders and portfolio.last_sync_ok and positions:
            plan = strategy_engine.build_emergency_flatten_plan(positions)
            try:
                updated_strategy_cycle = now
                metrics.record_plan(blocked_actions=0)
                flatten_result = execution_service.execute_plan(plan)
                metrics.record_plan_execution(getattr(flatten_result, "errors", []))
            except Exception as exc:  # pragma: no cover
                metrics.record_error(f"Emergency flatten execution failed: {exc}")
            finally:
                refresh_metrics_state()
            return updated_portfolio_sync, updated_strategy_cycle
        elif positions:
            # We have positions but unsafe to flatten (open orders or cancel failed)
            logger.warning(
                "Emergency flatten deferred: waiting for clear state",
                extra=structured_log_extra(
                    event="emergency_flatten_deferred",
                    cancel_ok=cancel_ok,
                    open_orders=len(open_orders),
                    last_sync_ok=portfolio.last_sync_ok,
                ),
            )
            refresh_metrics_state()
            return updated_portfolio_sync, updated_strategy_cycle

        if not open_orders and session is not None:
            try:
                setattr(session, "emergency_flatten", False)
                if hasattr(strategy_engine, "config") and hasattr(
                    strategy_engine.config, "session"
                ):
                    setattr(strategy_engine.config.session, "emergency_flatten", False)
                dump_runtime_overrides(
                    strategy_engine.config, session=session, sections={"session"}
                )
                logger.info(
                    "Emergency flatten cleared (portfolio flat)",
                    extra=structured_log_extra(event="emergency_flatten_cleared"),
                )
            except Exception as exc:  # pragma: no cover
                metrics.record_error(f"Failed to clear emergency flatten: {exc}")
            finally:
                refresh_metrics_state()
        return updated_portfolio_sync, updated_strategy_cycle

    if (now - last_portfolio_sync).total_seconds() >= portfolio_interval:
        try:
            portfolio.sync()
            if portfolio.last_sync_ok:
                # Keep local order state in sync with Kraken after each successful portfolio sync.
                # This prevents stale "zombie" orders after crashes/restarts from skewing risk calculations.
                try:
                    execution_service.refresh_open_orders()
                    execution_service.reconcile_orders()
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.warning(
                        "Order reconciliation failed: %s",
                        exc,
                        extra=structured_log_extra(
                            event="order_reconcile_failed",
                            error=str(exc),
                        ),
                    )
                    metrics.record_error(f"Order reconciliation failed: {exc}")

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

    md_health = (
        getattr(data_status, "health", None) if data_status is not None else None
    )
    market_data_ok = bool(md_health == "healthy")
    market_data_stale = bool(md_health == "stale")
    market_data_unavailable = bool(data_status is None or md_health == "unavailable")

    if market_data_stale:
        logger.warning(
            "Market data degraded (stale); continuing with healthy pairs only",
            extra=structured_log_extra(
                event="market_data_degraded",
                reason=getattr(data_status, "reason", None),
                max_staleness=getattr(data_status, "max_staleness", None),
                stale_pairs=getattr(data_status, "stale_pairs", None),
            ),
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
            diff = (
                drift_status.expected_position_value_base
                - drift_status.actual_balance_value_base
            )
            drift_message = (
                "Portfolio drift detected: expected=%.6f balances=%.6f diff=%.6f tolerance=%.6f"
                % (
                    drift_status.expected_position_value_base,
                    drift_status.actual_balance_value_base,
                    diff,
                    drift_status.tolerance_base,
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
        elif market_data_unavailable:
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
                stale_pairs = set(getattr(data_status, "stale_pairs", []) or [])
                if stale_pairs and getattr(plan, "actions", None):
                    filtered = [
                        a
                        for a in plan.actions
                        if getattr(a, "pair", None) not in stale_pairs
                    ]
                    if len(filtered) != len(plan.actions):
                        logger.warning(
                            "Skipping actions for stale pairs",
                            extra=structured_log_extra(
                                event="market_data_action_skip",
                                stale_pairs=sorted(stale_pairs),
                                skipped_actions=len(plan.actions) - len(filtered),
                            ),
                        )
                        plan.actions = filtered

                blocked_actions = len(
                    [a for a in plan.actions if getattr(a, "blocked", False)]
                )
                metrics.record_plan(blocked_actions)
                updated_strategy_cycle = now
                # Explicitly type result for mypy
                from kraken_bot.execution.models import ExecutionResult, LocalOrder

                result: Optional[ExecutionResult] = None
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


class BotController:
    """
    Encapsulates the application lifecycle, state management, and hot-reloading.
    Replaces the procedural closure-based state management with a robust class.
    """

    def __init__(self, allow_interactive_setup: bool = True):
        self.allow_interactive_setup = allow_interactive_setup
        self.stop_event = threading.Event()
        self.context: Optional[AppContext] = None

        # Service handles
        self.ui_server: Optional[uvicorn.Server] = None
        self.ui_thread: Optional[threading.Thread] = None

        # State flags
        self.is_setup_mode = False

    def bootstrap_context(self) -> AppContext:
        """
        Loads config/creds and initializes all services.
        Returns a fresh AppContext or a setup-mode context if credentials fail.
        """
        try:
            # We disable interactive setup in headless/daemon mode to force
            # CredentialBootstrapError if keys are missing.
            client, config, rate_limiter = bootstrap(
                allow_interactive_setup=self.allow_interactive_setup
            )

            db_path = getattr(config.portfolio, "db_path", "portfolio.db")
            auto_migrate = config.portfolio.auto_migrate_schema
            execution_mode = getattr(config.execution, "mode", "unknown")

            if auto_migrate and execution_mode in {"paper", "live"}:
                raise RuntimeError(
                    "auto_migrate_schema must be False in paper/live mode. "
                    "Run `krakked portfolio-migrate` to upgrade the DB schema first."
                )

            if auto_migrate:
                ensure_portfolio_schema(db_path, CURRENT_SCHEMA_VERSION, migrate=True)
                with sqlite3.connect(db_path) as conn:
                    ensure_portfolio_tables(conn)
                    conn.commit()
            else:
                assert_portfolio_schema(db_path)

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

            # Bootstrap: recover and reconcile any persisted open orders before the first strategy cycle.
            try:
                execution_service.load_open_orders_from_store()
                execution_service.refresh_open_orders()
                execution_service.reconcile_orders()
            except Exception as exc:
                logger.warning(
                    "Order reconciliation during bootstrap failed: %s",
                    str(exc),
                    extra=structured_log_extra(
                        event="bootstrap_order_reconcile_failed",
                        error=str(exc),
                    ),
                )

            metrics = SystemMetrics()
            session_state = SessionState(
                active=config.session.active,
                mode=config.session.mode,
                loop_interval_sec=config.session.loop_interval_sec,
                profile_name=config.session.profile_name,
                ml_enabled=config.session.ml_enabled,
                emergency_flatten=getattr(config.session, "emergency_flatten", False),
            )

            self.is_setup_mode = False

            return AppContext(
                config=config,
                client=client,
                market_data=market_data,
                portfolio_service=portfolio,
                portfolio=portfolio,  # Alias
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

            # Ensure a config file exists so the UI can come up on first run.
            config_dir = get_config_dir()
            config_path = config_dir / "config.yaml"
            if not config_path.exists():
                # Minimal safe defaults to boot the API/UI.
                write_initial_config(
                    {
                        "region": {"code": "US_CA", "default_quote": "USD"},
                        "universe": {
                            "include_pairs": [],
                            "exclude_pairs": [],
                            "min_24h_volume_usd": 0.0,
                        },
                        "execution": {
                            "mode": "paper",
                            "validate_only": True,
                            "allow_live_trading": False,
                        },
                        "ui": {"enabled": True, "host": "0.0.0.0", "port": 8000},
                        "session": {
                            "active": False,
                            "mode": "paper",
                            "loop_interval_sec": 15.0,
                            "profile_name": None,
                            "ml_enabled": True,
                        },
                    },
                    config_dir=config_dir,
                )

            # If credentials are missing/locked we still want to load config and expose setup endpoints.
            config = load_config(config_path=config_path)
            self.is_setup_mode = True

            # Return a minimal context for UI/setup flows.
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

    def _hot_swap_context(self, new_context: AppContext) -> None:
        """
        Updates the existing context object in-place so references held by
        other threads (UI) see the new services immediately.
        """
        if not self.context:
            self.context = new_context
            return

        logger.info("Hot-swapping application context...")

        # Shutdown old services to release resources/threads
        if self.context.market_data:
            try:
                self.context.market_data.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down old market data: {e}")

        # Patch attributes
        self.context.config = new_context.config
        self.context.client = new_context.client
        self.context.market_data = new_context.market_data
        self.context.portfolio = new_context.portfolio
        self.context.portfolio_service = new_context.portfolio_service
        self.context.strategy_engine = new_context.strategy_engine
        self.context.execution_service = new_context.execution_service
        self.context.metrics = new_context.metrics
        self.context.session = new_context.session

        # Crucial: Update the setup flag so UI knows we are ready
        self.context.is_setup_mode = new_context.is_setup_mode
        self.is_setup_mode = new_context.is_setup_mode

        # Clear the event so we don't loop
        self.context.reinitialize_event.clear()

    def start_ui(self) -> None:
        """Launch the FastAPI UI server in a background thread."""
        if not self.context or not self.context.config.ui.enabled:
            logger.info(
                "UI disabled by configuration; skipping API startup",
                extra=structured_log_extra(event="ui_disabled"),
            )
            return

        app = create_api(self.context)
        config = uvicorn.Config(
            app,
            host=self.context.config.ui.host,
            port=self.context.config.ui.port,
            log_level="info",
            log_config=None,
            install_signal_handlers=False,  # type: ignore[call-arg]
        )
        self.ui_server = uvicorn.Server(config)
        self.ui_thread = threading.Thread(target=self.ui_server.run, daemon=True)
        self.ui_thread.start()

        logger.info(
            "UI server started",
            extra=structured_log_extra(
                event="ui_started",
                host=self.context.config.ui.host,
                port=self.context.config.ui.port,
            ),
        )

    def shutdown(
        self, reason: str = "exit", signal_number: Optional[int] = None
    ) -> None:
        """Gracefully shut down all subsystems with rich structured logging."""
        first_shutdown = not self.stop_event.is_set()
        self.stop_event.set()

        metrics_snapshot = None
        if (
            self.context
            and self.context.metrics
            and isinstance(self.context.metrics, SystemMetrics)
        ):
            metrics_snapshot = self.context.metrics.snapshot()

        # Construct components status for observability
        components = {
            "ui_server": bool(self.ui_server),
            "market_data": bool(self.context and self.context.market_data),
            "execution_service": bool(self.context and self.context.execution_service),
            "strategy_engine": bool(self.context and self.context.strategy_engine),
        }

        # Safely extract DB path if available
        db_path = None
        if (
            self.context
            and self.context.portfolio
            and hasattr(self.context.portfolio, "store")
        ):
            db_path = getattr(self.context.portfolio.store, "db_path", None)

        shutdown_extra = structured_log_extra(
            event="shutdown",
            reason=reason,
            signal_number=signal_number,
            components=components,
            portfolio_db_path=db_path,
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

        if self.ui_server:
            self.ui_server.should_exit = True

        # Attempt to join UI thread briefly, but don't block forever
        if self.ui_thread and self.ui_thread.is_alive():
            self.ui_thread.join(timeout=2.0)

        if self.context:
            if self.context.execution_service:
                try:
                    self.context.execution_service.cancel_all()
                except Exception as e:
                    logger.error(f"Error canceling orders during shutdown: {e}")

            if self.context.market_data:
                try:
                    self.context.market_data.shutdown()
                except Exception as e:
                    logger.error(f"Error shutting down market data: {e}")

        logger.info(
            "Shutdown complete",
            extra=structured_log_extra(event="shutdown_complete", reason=reason),
        )

    def run(self) -> int:
        """Main entry point."""
        configure_logging(level=logging.INFO)

        # 1. Initial Bootstrap
        try:
            initial_context = self.bootstrap_context()
            self.context = initial_context  # Initialize the main reference
        except PortfolioSchemaError as e:
            logger.critical(
                "Portfolio schema mismatch: %s",
                e,
                extra=structured_log_extra(
                    event="schema_mismatch",
                    found_schema=e.found,
                    expected_schema=e.expected,
                ),
            )
            return 1
        except Exception as e:
            logger.critical(
                "Fatal startup error: %s",
                e,
                extra=structured_log_extra(event="startup_failed"),
            )
            return 1

        logger.info(
            "Starting Kraken bot",
            extra=structured_log_extra(
                event="startup",
                env=get_log_environment(),
                app_version=APP_VERSION,
                setup_mode=self.is_setup_mode,
            ),
        )

        # 2. Register Signal Handlers (Now they just call self.shutdown)
        signal.signal(
            signal.SIGINT,
            lambda s, f: self.shutdown(reason="signal", signal_number=s),
        )
        signal.signal(
            signal.SIGTERM,
            lambda s, f: self.shutdown(reason="signal", signal_number=s),
        )

        # 3. Start UI
        self.start_ui()

        # 4. Main Loop
        # We define a helper for metrics updates to keep the loop clean
        def refresh_metrics() -> None:
            if (
                self.context
                and self.context.portfolio
                and self.context.execution_service
                and self.context.metrics
            ):
                _refresh_metrics_state(
                    self.context.portfolio,
                    self.context.execution_service,
                    self.context.metrics,
                )

        # Initialize timing vars
        strategy_interval = 60
        portfolio_interval = 300
        last_strategy_cycle = datetime.now(timezone.utc) - timedelta(
            seconds=strategy_interval
        )
        last_portfolio_sync = datetime.now(timezone.utc) - timedelta(
            seconds=portfolio_interval
        )

        while not self.stop_event.is_set():
            if not self.context:
                # Should not happen given init, but safety first
                self.is_setup_mode = True

            # --- SETUP MODE HANDLING ---
            if self.is_setup_mode:
                # Check if UI requested a reset/reload via the event
                if self.context and self.context.reinitialize_event.wait(timeout=1.0):
                    logger.info("Re-initialization signal received. reloading...")
                    try:
                        new_ctx = self.bootstrap_context()
                        self._hot_swap_context(new_ctx)
                        if not self.is_setup_mode:
                            logger.info(
                                "System successfully initialized from setup mode",
                                extra=structured_log_extra(event="setup_complete"),
                            )
                        else:
                            logger.warning(
                                "Re-initialization failed to clear setup mode",
                                extra=structured_log_extra(event="setup_failed_retry"),
                            )
                    except Exception:
                        logger.exception(
                            "Critical error during re-initialization",
                            extra=structured_log_extra(event="reinit_error"),
                        )
                        if self.context:
                            self.context.reinitialize_event.clear()
                continue

            # --- RUNTIME RELOAD CHECK ---
            # Even in run mode, the UI might request a reset (e.g. "Lock" button clicked)
            if self.context and self.context.is_setup_mode and not self.is_setup_mode:
                logger.info(
                    "Reset detected (context.is_setup_mode=True). Entering setup mode...",
                    extra=structured_log_extra(event="enter_setup_mode_runtime"),
                )
                self.is_setup_mode = True
                if self.context.market_data:
                    self.context.market_data.shutdown()
                # Clear services to prevent usage
                self.context.market_data = None
                self.context.execution_service = None
                continue

            # Check for hot-reload requests during normal runtime (e.g. Config Apply)
            if self.context and self.context.reinitialize_event.is_set():
                logger.info(
                    "Runtime re-initialization signal received. Hot-swapping context..."
                )
                try:
                    new_ctx = self.bootstrap_context()
                    self._hot_swap_context(new_ctx)
                    logger.info(
                        "Context hot-swap complete",
                        extra=structured_log_extra(event="hot_swap_complete"),
                    )
                except Exception:
                    logger.exception(
                        "Critical error during runtime re-initialization",
                        extra=structured_log_extra(event="reinit_runtime_error"),
                    )
                    # Clear event to prevent tight loop
                    if self.context:
                        self.context.reinitialize_event.clear()
                # Skip the rest of the loop to ensure clean state
                continue

            # --- MAIN TRADING LOOP ---
            now = datetime.now(timezone.utc)

            # Safely extract config intervals
            strategy_interval = _coerce_interval(
                getattr(self.context.config.strategies, "loop_interval_seconds", 60),
                60,
                "strategy interval",
            )
            portfolio_interval = _coerce_interval(
                getattr(self.context.config.portfolio, "sync_interval_seconds", 300),
                300,
                "portfolio sync interval",
            )

            if (
                self.context.portfolio
                and self.context.market_data
                and self.context.strategy_engine
                and self.context.execution_service
                and self.context.metrics
                and self.context.session
            ):
                last_portfolio_sync, last_strategy_cycle = _run_loop_iteration(
                    now=now,
                    strategy_interval=strategy_interval,
                    portfolio_interval=portfolio_interval,
                    last_strategy_cycle=last_strategy_cycle,
                    last_portfolio_sync=last_portfolio_sync,
                    portfolio=self.context.portfolio,
                    market_data=self.context.market_data,
                    strategy_engine=self.context.strategy_engine,
                    execution_service=self.context.execution_service,
                    metrics=self.context.metrics,
                    refresh_metrics_state=refresh_metrics,
                    session_active=self.context.session.active,
                    session=self.context.session,
                )
            else:
                logger.error(
                    "Invalid state: not in setup mode but services are missing; forcing setup mode"
                )
                self.is_setup_mode = True
                continue

            # Dynamic sleep based on config
            session_interval = getattr(self.context.session, "loop_interval_sec", None)
            loop_interval = _coerce_interval(
                int(session_interval) if session_interval is not None else None,
                min(strategy_interval, portfolio_interval, 5),
                "session loop interval",
            )
            self.stop_event.wait(loop_interval)

        # Final cleanup pass on exit
        self.shutdown(reason="loop_exit")
        return 0


def run(allow_interactive_setup: bool = True) -> int:
    """Wrapper to maintain CLI compatibility."""
    controller = BotController(allow_interactive_setup=allow_interactive_setup)
    return controller.run()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    exit(run())
