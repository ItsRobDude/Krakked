from unittest.mock import MagicMock

import pytest

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.adapter import KrakenExecutionAdapter
from kraken_bot.execution.exceptions import ExecutionError, OrderRejectedError
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.router import build_order_payload
from kraken_bot.market_data.models import PairMetadata


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


@pytest.fixture
def pair_metadata():
    return PairMetadata(
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


def test_build_order_payload_validate_and_userref(sample_order, pair_metadata):
    config = ExecutionConfig(validate_only=True, mode="paper", max_slippage_bps=0)

    payload = build_order_payload(sample_order, config, pair_metadata)

    assert payload["validate"] == 1
    assert payload["userref"] == 42
    assert payload["price"] == "30000.0"
    assert payload["volume"] == "1.5"


def test_build_order_payload_live_market_excludes_validate_and_price(
    sample_order, pair_metadata
):
    config = ExecutionConfig(mode="live", validate_only=False)
    sample_order.order_type = "market"

    payload = build_order_payload(sample_order, config, pair_metadata)

    assert "validate" not in payload
    assert "price" not in payload


def test_submit_order_validate_only_sets_validated_status(sample_order, pair_metadata):
    client = MagicMock()
    client.add_order.return_value = {"error": []}
    adapter = KrakenExecutionAdapter(
        client, ExecutionConfig(validate_only=True, mode="paper")
    )

    order = adapter.submit_order(sample_order, pair_metadata)

    assert order.status == "validated"
    assert order.kraken_order_id is None
    assert order.raw_request["validate"] == 1
    assert order.raw_response == {"error": []}


def test_submit_order_live_success_sets_txid(sample_order, pair_metadata):
    client = MagicMock()
    client.add_order.return_value = {"error": [], "txid": ["ABC123"]}
    adapter = KrakenExecutionAdapter(
        client,
        ExecutionConfig(mode="live", validate_only=False, allow_live_trading=True),
    )

    order = adapter.submit_order(sample_order, pair_metadata)

    assert order.status == "open"
    assert order.kraken_order_id == "ABC123"
    assert order.raw_response["txid"] == ["ABC123"]


def test_submit_order_sets_dead_man_switch(sample_order, pair_metadata):
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

    order = adapter.submit_order(sample_order, pair_metadata)

    assert order.raw_request["expiretm"] == "+15"
    client.cancel_all_orders_after.assert_called_once_with(15)


def test_submit_order_with_zero_dead_man_switch_leaves_payload(
    sample_order, pair_metadata
):
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

    order = adapter.submit_order(sample_order, pair_metadata)

    assert "expiretm" not in order.raw_request
    client.cancel_all_orders_after.assert_not_called()


def test_submit_order_rejected_raises(sample_order, pair_metadata):
    client = MagicMock()
    client.add_order.return_value = {"error": ["EGeneral:failure"]}
    adapter = KrakenExecutionAdapter(
        client,
        ExecutionConfig(mode="live", validate_only=False, allow_live_trading=True),
    )

    with pytest.raises(OrderRejectedError, match="EGeneral:failure"):
        adapter.submit_order(sample_order, pair_metadata)

    assert sample_order.status == "rejected"
    assert sample_order.last_error == "EGeneral:failure"


def test_submit_order_client_exception_maps_to_execution_error(
    sample_order, pair_metadata
):
    client = MagicMock()
    client.add_order.side_effect = RuntimeError("network down")
    adapter = KrakenExecutionAdapter(client, ExecutionConfig())

    with pytest.raises(ExecutionError, match="network down"):
        adapter.submit_order(sample_order, pair_metadata)

    assert sample_order.status == "error"
    assert sample_order.last_error == "network down"


def test_submit_order_rejects_below_min_volume(pair_metadata):
    client = MagicMock()
    client.add_order.return_value = {"error": []}
    adapter = KrakenExecutionAdapter(client, ExecutionConfig())

    small_order = LocalOrder(
        local_id="local-2",
        plan_id="plan-1",
        strategy_id="strategy-1",
        pair=pair_metadata.canonical,
        side="buy",
        order_type="market",
        requested_base_size=pair_metadata.min_order_size / 2,
        requested_price=None,
    )

    order = adapter.submit_order(small_order, pair_metadata)

    assert order.status == "rejected"
    assert "below minimum" in (order.last_error or "")
    client.add_order.assert_not_called()


def test_submit_order_uses_latest_price_for_notional_check(sample_order, pair_metadata):
    client = MagicMock()
    client.add_order.return_value = {"error": []}
    adapter = KrakenExecutionAdapter(
        client, ExecutionConfig(min_order_notional_usd=100)
    )

    sample_order.order_type = "market"
    sample_order.requested_price = None
    sample_order.requested_base_size = 1

    order = adapter.submit_order(sample_order, pair_metadata, latest_price=50)

    assert order.status == "rejected"
    assert "below minimum" in (order.last_error or "")
    client.add_order.assert_not_called()
