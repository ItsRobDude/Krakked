import logging
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from krakked.execution.models import ExecutionResult, LocalOrder
from krakked.portfolio.models import SpotPosition
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction


@pytest.fixture
def exec_context(client: TestClient):
    return client.context  # type: ignore[attr-defined]


def _sample_order(local_id: str) -> LocalOrder:
    return LocalOrder(
        local_id=local_id,
        plan_id="plan-1",
        strategy_id="alpha",
        pair="BTC/USD",
        side="buy",
        order_type="limit",
        kraken_order_id="kid",
        userref=1,
        requested_base_size=0.1,
        requested_price=100.0,
        status="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        cumulative_base_filled=0.0,
        avg_fill_price=None,
        last_error=None,
        raw_request={"foo": "bar"},
        raw_response=None,
    )


def test_get_open_orders_enveloped(client, exec_context):
    exec_context.execution_service.get_open_orders.return_value = [_sample_order("1")]

    response = client.get("/api/execution/open_orders")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"][0]["local_id"] == "1"
    exec_context.execution_service.get_open_orders.assert_called_once()


def test_get_recent_executions_enveloped(client, exec_context):
    exec_context.execution_service.get_recent_executions.return_value = [
        ExecutionResult(
            plan_id="p1",
            started_at=datetime.now(UTC),
            orders=[_sample_order("2")],
            success=True,
        )
    ]

    response = client.get("/api/execution/recent_executions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"][0]["orders"][0]["local_id"] == "2"


@pytest.mark.parametrize("ui_read_only", [False])
def test_cancel_all_triggers_service(client, exec_context):
    response = client.post(
        "/api/execution/cancel_all", json={"confirmation": "CANCEL ALL"}
    )

    assert response.status_code == 200
    payload = response.json()
    exec_context.execution_service.cancel_all.assert_called_once()
    assert payload == {"data": True, "error": None}


@pytest.mark.parametrize("ui_read_only", [False])
def test_cancel_all_requires_confirmation(client, exec_context):
    response = client.post("/api/execution/cancel_all", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert "Field required" in payload["error"]
    exec_context.execution_service.cancel_all.assert_not_called()


@pytest.mark.parametrize("ui_read_only", [True])
def test_cancel_all_blocked_read_only(client, exec_context):
    response = client.post(
        "/api/execution/cancel_all", json={"confirmation": "CANCEL ALL"}
    )

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    exec_context.execution_service.cancel_all.assert_not_called()


@pytest.mark.parametrize("ui_read_only", [False])
def test_cancel_order_happy_path(client, exec_context):
    order = _sample_order("123")
    exec_context.execution_service.open_orders = {"123": order}

    response = client.post("/api/execution/cancel/123")

    assert response.status_code == 200
    payload = response.json()
    exec_context.execution_service.cancel_order.assert_called_once_with(order)
    assert payload == {"data": True, "error": None}


@pytest.mark.parametrize("ui_read_only", [True])
def test_cancel_order_blocked(client, exec_context):
    order = _sample_order("123")
    exec_context.execution_service.open_orders = {"123": order}

    response = client.post("/api/execution/cancel/123")

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    exec_context.execution_service.cancel_order.assert_not_called()


@pytest.mark.parametrize("ui_read_only", [False])
def test_flatten_all_executes_plan(client, exec_context):
    # Add an action to ensure execution is called
    action = RiskAdjustedAction(
        pair="BTC/USD",
        strategy_id="manual",
        action_type="close",
        target_base_size=0.0,
        target_notional_usd=0.0,
        current_base_size=1.0,
        reason="flatten",
        blocked=False,
        blocked_reasons=[],
    )
    plan = ExecutionPlan(
        plan_id="flatten_1",
        generated_at=datetime.now(UTC),
        actions=[action],
        emergency_reduce_only=True,
    )
    exec_context.portfolio.get_positions.return_value = [
        SpotPosition(
            pair="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            base_size=1.0,
            avg_entry_price=10.0,
            realized_pnl_base=0.0,
            fees_paid_base=0.0,
            strategy_tag="alpha",
        )
    ]
    exec_context.strategy_engine.build_emergency_flatten_plan.return_value = plan
    exec_context.execution_service.execute_plan.return_value = ExecutionResult(
        plan_id="flatten_1", started_at=datetime.now(UTC), success=True
    )
    # Satisfy safety gates
    exec_context.execution_service.cancel_all.return_value = None
    exec_context.execution_service.get_open_orders.return_value = []
    exec_context.portfolio.last_sync_ok = True

    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )

        assert response.status_code == 200
        payload = response.json()
        exec_context.strategy_engine.build_emergency_flatten_plan.assert_called_once_with(
            exec_context.portfolio.get_positions.return_value
        )
        exec_context.execution_service.execute_plan.assert_called_once_with(plan)
        assert payload["error"] is None
        assert payload["data"]["plan_id"].startswith("flatten_")
        mock_dump.assert_called_once()


@pytest.mark.parametrize("ui_read_only", [True])
def test_flatten_all_blocked(client, exec_context):
    response = client.post(
        "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
    )

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    exec_context.execution_service.execute_plan.assert_not_called()


def test_flatten_all_fails_if_cancel_fails(client, exec_context):
    """Test that flatten execution is blocked if cancel_all raises exception."""
    exec_context.execution_service.cancel_all.side_effect = Exception("Cancel Failed")
    exec_context.execution_service.get_open_orders.return_value = (
        []
    )  # Even if empty list returned later

    # Mock dump_runtime_overrides to prevent file I/O
    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is not None
        assert "Flatten armed but waiting" in data["error"]
        assert "cancel_all failed" in data["error"]

        # Verify execute_plan was NOT called
        exec_context.execution_service.execute_plan.assert_not_called()
        # Verify emergency flag was set
        assert exec_context.session.emergency_flatten is True
        mock_dump.assert_called_once()


def test_flatten_all_fails_if_open_orders_remain(client, exec_context):
    """Test that flatten execution is blocked if open orders remain."""
    exec_context.execution_service.cancel_all.return_value = None  # Success
    # Mock open orders remaining
    exec_context.execution_service.get_open_orders.return_value = [_sample_order("1")]

    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is not None
        assert "waiting for open orders" in data["error"]

        # Verify execute_plan was NOT called
        exec_context.execution_service.execute_plan.assert_not_called()
        assert exec_context.session.emergency_flatten is True
        mock_dump.assert_called_once()


@pytest.mark.parametrize("ui_read_only", [False])
def test_flatten_all_handles_dust_only(client, exec_context):
    """Verify flatten_all returns success and warnings without arming emergency mode if only dust remains."""
    # Plan with no actions (dust/untradeable only)
    plan = ExecutionPlan(
        plan_id="flatten_dust",
        generated_at=datetime.now(UTC),
        actions=[],
        emergency_reduce_only=True,
        metadata={"dust_count_total": 5, "untradeable_count_total": 2},
    )

    # Setup mocks
    exec_context.portfolio.get_positions.return_value = (
        []
    )  # Content doesn't matter as engine is mocked
    exec_context.strategy_engine.build_emergency_flatten_plan.return_value = plan
    exec_context.execution_service.cancel_all.return_value = None
    exec_context.execution_service.get_open_orders.return_value = []
    exec_context.portfolio.last_sync_ok = True

    # Ensure emergency flag starts False
    exec_context.session.emergency_flatten = False

    with patch("krakked.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = client.post(
            "/api/execution/flatten_all", json={"confirmation": "FLATTEN ALL"}
        )

        assert response.status_code == 200
        data = response.json()

        # Verify success envelope
        assert data["error"] is None
        result = data["data"]
        assert result["success"] is True
        assert result["plan_id"] == "flatten_dust"
        assert len(result["orders"]) == 0

        # Verify warning text
        assert len(result["warnings"]) > 0
        warning = result["warnings"][0]
        assert "No sellable positions" in warning
        assert "dust=5" in warning
        assert "untradeable=2" in warning

        # Verify side effects: NO execution, NO emergency arming
        exec_context.execution_service.execute_plan.assert_not_called()
        assert exec_context.session.emergency_flatten is False
        mock_dump.assert_not_called()


@pytest.mark.parametrize("ui_read_only", [False])
def test_flatten_all_requires_confirmation(client, exec_context):
    response = client.post("/api/execution/flatten_all", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert "Field required" in payload["error"]
    exec_context.execution_service.execute_plan.assert_not_called()


@pytest.mark.parametrize("ui_read_only", [False])
def test_cancel_all_logs_client_context(
    client, exec_context, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="krakked.ui.routes.execution")

    response = client.post(
        "/api/execution/cancel_all",
        json={"confirmation": "CANCEL ALL"},
        headers={"X-Forwarded-For": "203.0.113.5"},
    )

    assert response.status_code == 200
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "cancel_all_triggered"
    ]
    assert records
    assert any(getattr(record, "account_id", None) == "default" for record in records)
    assert any(getattr(record, "client_ip", None) for record in records)
    assert any(getattr(record, "forwarded_for", None) == "203.0.113.5" for record in records)
