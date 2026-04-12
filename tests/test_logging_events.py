from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from krakked.config import ExecutionConfig
from krakked.connection.rest_client import KrakenRESTClient
from krakked.execution.adapter import ExecutionAdapter
from krakked.execution.models import LocalOrder
from krakked.execution.oms import ExecutionService
from krakked.main import BotController, _run_loop_iteration
from krakked.market_data.api import MarketDataAPI
from krakked.market_data.models import PairMetadata
from krakked.metrics import SystemMetrics
from krakked.portfolio.exceptions import PortfolioSchemaError
from krakked.portfolio.manager import PortfolioService
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction
from krakked.ui.api import create_api
from krakked.ui.context import AppContext
from tests.ui.conftest import build_test_context


class _FakeAdapter(ExecutionAdapter):
    def __init__(self) -> None:
        # Minimal stub that satisfies the ExecutionAdapter Protocol
        self.client = cast(KrakenRESTClient, MagicMock())
        self.config = ExecutionConfig()
        self.submit_order_calls: list[LocalOrder] = []

    def submit_order(
        self,
        order: LocalOrder,
        pair_metadata: PairMetadata,
        latest_price: float | None = None,
    ) -> LocalOrder:
        self.submit_order_calls.append(order)
        return order

    def cancel_order(
        self, order: LocalOrder
    ) -> None:  # pragma: no cover - not used here
        return None

    def cancel_all_orders(self) -> None:  # pragma: no cover - not used here
        return None


def _build_action(pair: str) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair=pair,
        strategy_id="strategy-1",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="",
        blocked=False,
        blocked_reasons=[],
        strategy_tag="strategy-1",
        risk_limits_snapshot={},
    )


def _market_data_mock():
    market_data = MagicMock()
    market_data.get_pair_metadata_or_raise.return_value = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XBT/USD",
        ws_symbol="XBT/USD",
        raw_name="XBTUSD",
        price_decimals=1,
        volume_decimals=8,
        lot_size=0.00000001,
        min_order_size=0.0001,
        status="online",
    )
    market_data.get_best_bid_ask.return_value = None
    return market_data


def test_kill_switch_block_logs_warning_with_event(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING, logger="krakked.execution.oms")

    def _kill_switch_status() -> Any:
        return SimpleNamespace(kill_switch_active=True)

    service = ExecutionService(
        adapter=_FakeAdapter(),
        market_data=_market_data_mock(),
        risk_status_provider=_kill_switch_status,
    )

    plan = ExecutionPlan(
        plan_id="plan-123",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD")],
    )

    service.execute_plan(plan)

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "kill_switch_block"
    ]

    assert records, "Expected a kill_switch_block log entry"
    assert all(record.levelno == logging.WARNING for record in records)
    assert any(getattr(record, "plan_id", None) == plan.plan_id for record in records)


def test_market_data_warning_emits_structured_event(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING, logger="krakked.main")

    market_data = SimpleNamespace(
        get_health_status=lambda: SimpleNamespace(
            health="stale", reason="feed_stale", max_staleness=12.5
        )
    )

    class _Metrics:
        def update_market_data_status(self, **_kwargs): ...

        def record_market_data_error(self, message: str) -> None:
            self.last_error = message

        def record_drift(self, *_args, **_kwargs): ...

        def record_plan(self, *args, **kwargs): ...
        def record_plan_execution(self, *args, **kwargs): ...
        def record_blocked_actions(self, *args, **kwargs): ...
        def record_error(self, *args, **kwargs): ...

    class _Portfolio:
        last_sync_ok = True

        def sync(self) -> None:  # pragma: no cover - not expected to run
            ...

        def get_drift_status(self):
            return None

    def refresh_metrics() -> None:
        return None

    portfolio = cast(PortfolioService, _Portfolio())
    md = cast(MarketDataAPI, market_data)
    metrics_obj = cast(SystemMetrics, _Metrics())

    _run_loop_iteration(
        now=datetime.now(timezone.utc),
        strategy_interval=1,
        portfolio_interval=60,
        last_strategy_cycle=datetime.now(timezone.utc) - timedelta(seconds=2),
        last_portfolio_sync=datetime.now(timezone.utc),
        portfolio=portfolio,
        market_data=md,
        strategy_engine=MagicMock(),
        execution_service=MagicMock(),
        metrics=metrics_obj,
        refresh_metrics_state=refresh_metrics,
        session_active=True,
    )

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_data_degraded"
    ]

    assert records, "Expected a market_data_degraded log entry"
    assert all(record.levelno == logging.WARNING for record in records)
    assert any(getattr(record, "reason", None) == "feed_stale" for record in records)


def test_schema_mismatch_logs_critical_event(monkeypatch, caplog):
    """
    Checks that the re-initialization loop logs a CRITICAL event on schema mismatch.
    """
    caplog.set_level(logging.CRITICAL, logger="krakked.main")

    monkeypatch.setattr("krakked.main.configure_logging", lambda *_, **__: None)

    # Mock bootstrap to raise SchemaError
    monkeypatch.setattr(
        "krakked.main.bootstrap",
        lambda allow_interactive_setup: (_ for _ in ()).throw(
            PortfolioSchemaError(found=2, expected=3)
        ),
    )

    controller = BotController(allow_interactive_setup=False)

    # Setup context to simulate locked mode
    controller.context = MagicMock()
    controller.context.config.ui.enabled = False
    controller.context.is_setup_mode = True
    controller.context.reinitialize_event.wait.side_effect = [
        True,
        False,
    ]  # Trigger once then stop wait loop
    controller.context.reinitialize_event.is_set.return_value = True

    # We mock bootstrap_locked_context to return our prepared mock context
    # so run() enters the loop correctly.
    controller.bootstrap_locked_context = MagicMock(return_value=controller.context)

    # We need to break the main loop after one iteration or handling the event
    # The loop runs while not stop_event.is_set().
    # We can set stop_event in a side effect or just let it run one cycle.
    # The 'wait' call above controls the "setup mode" blocking loop.
    # If wait returns True, it tries to bootstrap_context (which fails).
    # Then it loops again. We need to stop it.

    def stop_controller(*args, **kwargs):
        controller.stop_event.set()
        return True  # Simulate wait success

    controller.context.reinitialize_event.wait.side_effect = stop_controller

    # Prevent UI start
    monkeypatch.setattr(BotController, "start_ui", lambda self: None)

    exit_code = controller.run()

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "schema_mismatch"
    ]

    # exit_code should be 0 because the loop exited gracefully via stop_event
    assert exit_code == 0

    assert records, "Expected a schema_mismatch log entry from re-init loop"
    assert all(record.levelno == logging.CRITICAL for record in records)
    assert any(getattr(record, "expected_schema", None) == 3 for record in records)
    assert any(getattr(record, "found_schema", None) == 2 for record in records)


def test_shutdown_logs_include_event(caplog):
    caplog.set_level(logging.INFO, logger="krakked.main")

    controller = BotController(allow_interactive_setup=False)
    controller.context = AppContext(
        config=MagicMock(),
        client=MagicMock(),
        market_data=MagicMock(),
        portfolio_service=MagicMock(),
        portfolio=MagicMock(),
        strategy_engine=MagicMock(),
        execution_service=MagicMock(),
        metrics=MagicMock(),
    )

    controller.shutdown(reason="test")

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "shutdown"
    ]

    assert records, "Expected a shutdown log entry"
    assert any(getattr(record, "reason", None) == "test" for record in records)


def test_ui_route_logs_request_event(caplog):
    caplog.set_level(logging.WARNING, logger="krakked.ui.routes.execution")

    context = build_test_context(auth_enabled=False, auth_token="token", read_only=True)
    app = create_api(context)
    client = TestClient(app)

    response = client.post("/api/execution/cancel/local-123")

    assert response.status_code == 200
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "cancel_order_blocked"
    ]

    assert records, "Expected cancel_order_blocked log entry"
    assert all(record.levelno == logging.WARNING for record in records)
    assert any(getattr(record, "local_id", None) == "local-123" for record in records)
