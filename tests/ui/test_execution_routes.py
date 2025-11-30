from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from kraken_bot.execution.models import ExecutionResult, LocalOrder
from kraken_bot.portfolio.models import SpotPosition


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
    response = client.post("/api/execution/cancel_all")

    assert response.status_code == 200
    payload = response.json()
    exec_context.execution_service.cancel_all.assert_called_once()
    assert payload == {"data": True, "error": None}


@pytest.mark.parametrize("ui_read_only", [True])
def test_cancel_all_blocked_read_only(client, exec_context):
    response = client.post("/api/execution/cancel_all")

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
    exec_context.execution_service.execute_plan.return_value = ExecutionResult(
        plan_id="flatten_1", started_at=datetime.now(UTC), success=True
    )

    response = client.post("/api/execution/flatten_all")

    assert response.status_code == 200
    payload = response.json()
    exec_context.execution_service.execute_plan.assert_called_once()
    assert payload["error"] is None
    assert payload["data"]["plan_id"].startswith("flatten_")


@pytest.mark.parametrize("ui_read_only", [True])
def test_flatten_all_blocked(client, exec_context):
    response = client.post("/api/execution/flatten_all")

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    exec_context.execution_service.execute_plan.assert_not_called()
