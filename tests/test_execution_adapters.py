from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from krakked.config import ExecutionConfig
from krakked.connection.exceptions import RateLimitError, ServiceUnavailableError
from krakked.execution.adapter import DryRunExecutionAdapter, KrakenExecutionAdapter
from krakked.execution.exceptions import ExecutionError, OrderRejectedError
from krakked.execution.models import LocalOrder
from krakked.execution.oms import ExecutionService
from krakked.market_data.models import PairMetadata
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction


def _action(
    pair: str, base_size: float = 1.0, price: float = 30.0
) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair=pair,
        strategy_id="test_strategy",
        action_type="open",
        target_base_size=base_size,
        target_notional_usd=base_size * price,
        current_base_size=0.0,
        reason="",
        blocked=False,
        blocked_reasons=[],
        strategy_tag="test_strategy",
        risk_limits_snapshot={},
    )


def _plan(action: RiskAdjustedAction) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan",
        generated_at=datetime.now(UTC),
        actions=[action],
        metadata={"order_type": "limit", "requested_price": 30.0},
    )


def _pair_metadata(pair: str = "XBTUSD") -> PairMetadata:
    base, quote = pair[:3], pair[3:]
    rest_symbol = f"{base}/{quote}"
    return PairMetadata(
        canonical=pair,
        base=base,
        quote=quote,
        rest_symbol=rest_symbol,
        ws_symbol=rest_symbol,
        raw_name=pair,
        price_decimals=1,
        volume_decimals=8,
        lot_size=0.00000001,
        min_order_size=0.0001,
        status="online",
    )


def test_execution_service_uses_kraken_adapter_for_paper_mode(inactive_risk_status):
    config = ExecutionConfig(mode="paper", validate_only=False)
    client = MagicMock()
    # Paper mode validates the order (returns no error) but no txid if validate=1
    client.add_order.return_value = {"error": []}
    market_data = MagicMock()
    market_data.get_best_bid_ask.return_value = {"bid": 30.0, "ask": 30.0}
    market_data.get_pair_metadata_or_raise.side_effect = lambda pair: _pair_metadata(
        pair
    )

    service = ExecutionService(
        config=config,
        client=client,
        market_data=market_data,
        risk_status_provider=inactive_risk_status,
    )

    assert isinstance(service.adapter, KrakenExecutionAdapter)

    plan = _plan(_action("XBTUSD"))
    result = service.execute_plan(plan)

    assert result.success
    # In validate-only mode (implied by paper), status is "validated"
    assert result.orders[0].status == "validated"
    client.add_order.assert_called_once()
    assert result.orders[0].raw_request["validate"] == 1


def test_execution_service_uses_dry_run_adapter_for_dry_run_mode(inactive_risk_status):
    config = ExecutionConfig(mode="dry_run", validate_only=False)
    client = MagicMock()
    market_data = MagicMock()
    market_data.get_best_bid_ask.return_value = {"bid": 30.0, "ask": 30.0}
    market_data.get_pair_metadata_or_raise.side_effect = lambda pair: _pair_metadata(
        pair
    )

    service = ExecutionService(
        config=config,
        client=client,
        market_data=market_data,
        risk_status_provider=inactive_risk_status,
    )

    assert isinstance(service.adapter, DryRunExecutionAdapter)

    plan = _plan(_action("XBTUSD"))
    result = service.execute_plan(plan)

    assert result.success
    assert result.orders[0].status == "filled"
    assert result.orders[0].cumulative_base_filled == pytest.approx(1.0)
    client.add_order.assert_not_called()


def test_execution_service_uses_kraken_adapter_for_live_mode(inactive_risk_status):
    config = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
    )
    client = MagicMock()
    client.add_order.return_value = {"txid": ["ABC123"], "error": []}
    market_data = MagicMock()
    market_data.get_best_bid_ask.return_value = {"bid": 25.0, "ask": 25.0}
    market_data.get_pair_metadata_or_raise.side_effect = lambda pair: _pair_metadata(
        pair
    )

    service = ExecutionService(
        config=config,
        client=client,
        market_data=market_data,
        risk_status_provider=inactive_risk_status,
    )

    assert isinstance(service.adapter, KrakenExecutionAdapter)

    plan = _plan(_action("XBTUSD", price=25.0))
    result = service.execute_plan(plan)

    assert result.orders[0].status == "open"
    client.add_order.assert_called_once()


def _local_order(
    pair: str = "XBTUSD", side: str = "buy", price: float = 25.0, volume: float = 1.0
):
    return LocalOrder(
        local_id="local",
        plan_id="plan",
        strategy_id="strategy",
        pair=pair,
        side=side,
        order_type="limit",
        requested_base_size=volume,
        requested_price=price,
        userref=99,
    )


def test_kraken_execution_adapter_validate_only_success():
    client = MagicMock()
    client.add_order.return_value = {"error": []}
    config = ExecutionConfig(mode="live", validate_only=True)
    adapter = KrakenExecutionAdapter(client=client, config=config)

    local_order = adapter.submit_order(_local_order(), _pair_metadata())

    assert local_order.status == "validated"
    client.add_order.assert_called_once()


def test_kraken_execution_adapter_live_success_sets_txid():
    client = MagicMock()
    client.add_order.return_value = {"error": [], "txid": ["ABC123"]}
    config = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
    )
    adapter = KrakenExecutionAdapter(client=client, config=config)

    local_order = adapter.submit_order(
        _local_order(price=30.0, volume=1.0), _pair_metadata()
    )

    assert local_order.status == "open"
    assert local_order.kraken_order_id == "ABC123"


def test_kraken_execution_adapter_handles_kraken_errors():
    client = MagicMock()
    client.add_order.return_value = {"error": ["EOrder:Invalid"]}
    config = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
    )
    adapter = KrakenExecutionAdapter(client=client, config=config)

    with pytest.raises(OrderRejectedError):
        adapter.submit_order(_local_order(price=30.0, volume=1.0), _pair_metadata())


def test_kraken_execution_adapter_client_exception():
    client = MagicMock()
    client.add_order.side_effect = RuntimeError("network down")
    config = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
    )
    adapter = KrakenExecutionAdapter(client=client, config=config)

    with pytest.raises(ExecutionError):
        adapter.submit_order(_local_order(price=30.0, volume=1.0), _pair_metadata())


def test_kraken_execution_adapter_retries_on_transient_error_then_succeeds(monkeypatch):
    client = MagicMock()
    client.add_order.side_effect = [
        RateLimitError("throttle"),
        {"error": [], "txid": ["ABC123"]},
    ]
    config = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
        max_retries=2,
        retry_backoff_seconds=0,
        retry_backoff_factor=2.0,
    )
    adapter = KrakenExecutionAdapter(client=client, config=config)

    monkeypatch.setattr("time.sleep", lambda _: None)

    order = adapter.submit_order(_local_order(price=30.0, volume=1.0), _pair_metadata())

    assert order.status == "open"
    assert order.kraken_order_id == "ABC123"
    assert client.add_order.call_count == 2


def test_kraken_execution_adapter_retries_exhausted_sets_error(monkeypatch):
    client = MagicMock()
    client.add_order.side_effect = ServiceUnavailableError("down")
    config = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=True,
        max_retries=1,
        retry_backoff_seconds=0,
        retry_backoff_factor=2.0,
    )
    adapter = KrakenExecutionAdapter(client=client, config=config)

    monkeypatch.setattr("time.sleep", lambda _: None)

    order = _local_order(price=30.0, volume=1.0)

    with pytest.raises(ExecutionError):
        adapter.submit_order(order, _pair_metadata())

    assert client.add_order.call_count == 2
    assert order.status == "error"
    assert order.last_error == "down"


def test_kraken_execution_adapter_blocks_live_trading_without_paper_tests():
    client = MagicMock()
    config = ExecutionConfig(
        mode="live",
        validate_only=False,
        allow_live_trading=True,
        paper_tests_completed=False,  # Explicitly False
    )
    adapter = KrakenExecutionAdapter(client=client, config=config)

    # Should return a rejected order immediately (not raise)
    order = adapter.submit_order(_local_order(), _pair_metadata())

    assert order.status == "rejected"
    assert "paper_tests_completed is False" in order.last_error
    client.add_order.assert_not_called()
