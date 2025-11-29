import pytest
from unittest.mock import MagicMock

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.adapter import KrakenExecutionAdapter
from kraken_bot.execution.exceptions import ExecutionError, OrderRejectedError
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.router import build_order_payload


@pytest.fixture
def sample_order():
    return LocalOrder(
        local_id="local-1",
        plan_id="plan-1",
        strategy_id="strategy-1",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        requested_base_size=1.5,
        requested_price=30000.0,
        userref=42,
    )


def test_build_order_payload_validate_and_userref(sample_order):
    config = ExecutionConfig(validate_only=True, mode="paper", max_slippage_bps=0)

    payload = build_order_payload(sample_order, config)

    assert payload["validate"] == 1
    assert payload["userref"] == 42
    assert payload["price"] == sample_order.requested_price


def test_build_order_payload_live_market_excludes_validate_and_price(sample_order):
    config = ExecutionConfig(mode="live", validate_only=False)
    sample_order.order_type = "market"

    payload = build_order_payload(sample_order, config)

    assert "validate" not in payload
    assert "price" not in payload


def test_submit_order_validate_only_sets_validated_status(sample_order):
    client = MagicMock()
    client.add_order.return_value = {"error": []}
    adapter = KrakenExecutionAdapter(client, ExecutionConfig(validate_only=True, mode="paper"))

    order = adapter.submit_order(sample_order)

    assert order.status == "validated"
    assert order.kraken_order_id is None
    assert order.raw_request["validate"] == 1
    assert order.raw_response == {"error": []}


def test_submit_order_live_success_sets_txid(sample_order):
    client = MagicMock()
    client.add_order.return_value = {"error": [], "txid": ["ABC123"]}
    adapter = KrakenExecutionAdapter(
        client, ExecutionConfig(mode="live", validate_only=False, allow_live_trading=True)
    )

    order = adapter.submit_order(sample_order)

    assert order.status == "open"
    assert order.kraken_order_id == "ABC123"
    assert order.raw_response["txid"] == ["ABC123"]


def test_submit_order_sets_dead_man_switch(sample_order):
    client = MagicMock()
    client.add_order.return_value = {"error": [], "txid": ["ABC123"]}
    adapter = KrakenExecutionAdapter(
        client,
        ExecutionConfig(
            mode="live",
            validate_only=False,
            allow_live_trading=True,
            dead_man_switch_seconds=15,
        ),
    )

    order = adapter.submit_order(sample_order)

    assert order.raw_request["expiretm"] == "+15"
    client.cancel_all_orders_after.assert_called_once_with(15)


def test_submit_order_with_zero_dead_man_switch_leaves_payload(sample_order):
    client = MagicMock()
    client.add_order.return_value = {"error": [], "txid": ["ABC123"]}
    adapter = KrakenExecutionAdapter(
        client,
        ExecutionConfig(
            mode="live",
            validate_only=False,
            allow_live_trading=True,
            dead_man_switch_seconds=0,
        ),
    )

    order = adapter.submit_order(sample_order)

    assert "expiretm" not in order.raw_request
    client.cancel_all_orders_after.assert_not_called()


def test_submit_order_rejected_raises(sample_order):
    client = MagicMock()
    client.add_order.return_value = {"error": ["EGeneral:failure"]}
    adapter = KrakenExecutionAdapter(
        client, ExecutionConfig(mode="live", validate_only=False, allow_live_trading=True)
    )

    with pytest.raises(OrderRejectedError, match="EGeneral:failure"):
        adapter.submit_order(sample_order)

    assert sample_order.status == "rejected"
    assert sample_order.last_error == "EGeneral:failure"


def test_submit_order_client_exception_maps_to_execution_error(sample_order):
    client = MagicMock()
    client.add_order.side_effect = RuntimeError("network down")
    adapter = KrakenExecutionAdapter(client, ExecutionConfig())

    with pytest.raises(ExecutionError, match="network down"):
        adapter.submit_order(sample_order)

    assert sample_order.status == "error"
    assert sample_order.last_error == "network down"
