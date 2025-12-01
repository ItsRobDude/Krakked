from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from starlette.testclient import TestClient

from kraken_bot.execution.adapter import ExecutionAdapter
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.main import _run_loop_iteration, _shutdown, run
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.metrics import SystemMetrics
from kraken_bot.portfolio.exceptions import PortfolioSchemaError
from kraken_bot.portfolio.manager import PortfolioService
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext
from tests.ui.conftest import build_test_context


class _FakeAdapter:
    def __init__(self) -> None:
        self.submit_order_calls: list[LocalOrder] = []

    def submit_order(self, order: LocalOrder) -> LocalOrder:
        self.submit_order_calls.append(order)
        return order


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


def test_kill_switch_block_logs_warning_with_event(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING, logger="kraken_bot.execution.oms")

    def _kill_switch_status() -> Any:
        return SimpleNamespace(kill_switch_active=True)

    adapter = cast(ExecutionAdapter, _FakeAdapter())
    service = ExecutionService(
        adapter=adapter,
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
    caplog.set_level(logging.WARNING, logger="kraken_bot.main")

    market_data = SimpleNamespace(
        get_health_status=lambda: SimpleNamespace(
            health="stale", reason="feed_stale", max_staleness=12.5
        )
    )

    class _Metrics:
        def update_market_data_status(self, **_kwargs):
            ...

        def record_market_data_error(self, message: str) -> None:
            self.last_error = message

        def record_drift(self, *_args, **_kwargs):
            ...

    class _Portfolio:
        def sync(self) -> None:  # pragma: no cover - not expected to run
            ...

        def get_drift_status(self):
            return None

    refresh_metrics = lambda: None
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
    )

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_data_unavailable"
    ]

    assert records, "Expected a market_data_unavailable log entry"
    assert all(record.levelno == logging.WARNING for record in records)
    assert any(getattr(record, "reason", None) == "feed_stale" for record in records)


def test_schema_mismatch_logs_critical_event(monkeypatch, caplog):
    caplog.set_level(logging.CRITICAL, logger="kraken_bot.main")

    monkeypatch.setattr("kraken_bot.main.configure_logging", lambda *_, **__: None)
    monkeypatch.setattr(
        "kraken_bot.main.bootstrap",
        lambda allow_interactive_setup: (_ for _ in ()).throw(
            PortfolioSchemaError(found=2, expected=3)
        ),
    )

    exit_code = run(allow_interactive_setup=False)

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "schema_mismatch"
    ]

    assert exit_code == 1
    assert records, "Expected a schema_mismatch log entry"
    assert all(record.levelno == logging.CRITICAL for record in records)
    assert any(getattr(record, "expected_schema", None) == 3 for record in records)
    assert any(getattr(record, "found_schema", None) == 2 for record in records)


def test_shutdown_logs_include_event(caplog):
    caplog.set_level(logging.INFO, logger="kraken_bot.main")

    context = AppContext(
        config=MagicMock(),
        client=MagicMock(),
        market_data=MagicMock(),
        portfolio=MagicMock(),
        strategy_engine=MagicMock(),
        execution_service=MagicMock(),
        metrics=MagicMock(),
    )

    stop_event = MagicMock()
    stop_event.is_set.return_value = False

    _shutdown(context, stop_event, ui_server=None, ui_thread=None, reason="test")

    records = [
        record for record in caplog.records if getattr(record, "event", None) == "shutdown"
    ]

    assert records, "Expected a shutdown log entry"
    assert any(getattr(record, "reason", None) == "test" for record in records)


def test_ui_route_logs_request_event(caplog):
    caplog.set_level(logging.WARNING, logger="kraken_bot.ui.routes.execution")

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

