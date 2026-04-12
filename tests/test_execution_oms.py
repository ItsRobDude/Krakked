from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from krakked.config import ExecutionConfig
from krakked.execution.exceptions import ExecutionError
from krakked.execution.models import LocalOrder
from krakked.execution.oms import ExecutionService
from krakked.market_data.models import PairMetadata
from krakked.strategy.models import ExecutionPlan, RiskAdjustedAction


class RecordingAdapter:
    submitted: list[LocalOrder]
    exception: Exception | None

    def __init__(
        self,
        exception: Exception | None = None,
        config: ExecutionConfig | None = None,
    ):
        self.submitted = []
        self.exception = exception
        self.config = config or ExecutionConfig()

    def submit_order(
        self,
        order: LocalOrder,
        pair_metadata: PairMetadata,
        latest_price: float | None = None,
    ) -> LocalOrder:
        if self.exception:
            raise self.exception
        self.submitted.append(order)
        order.status = "validated"
        return order


@pytest.fixture
def plan_metadata():
    return {"order_type": "limit", "requested_price": 100.0}


def make_action(
    target: float, current: float, blocked: bool = False, action_type: str = "open"
):
    return RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="strat-1",
        action_type=action_type,
        target_base_size=target,
        target_notional_usd=0.0,
        current_base_size=current,
        reason="test",
        blocked=blocked,
        blocked_reasons=[],
    )


def make_plan(actions, metadata, generated_at: datetime | None = None):
    return ExecutionPlan(
        plan_id="plan-123",
        generated_at=generated_at or datetime.now(UTC),
        actions=actions,
        metadata=metadata,
    )


def _pair_metadata() -> PairMetadata:
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


def _market_data_mock():
    md = MagicMock()
    md.get_pair_metadata_or_raise.return_value = _pair_metadata()
    md.get_best_bid_ask.return_value = None
    return md


def test_execute_plan_skips_blocked_noop_and_zero_delta(
    plan_metadata, inactive_risk_status
):
    adapter = MagicMock()
    service = ExecutionService(
        adapter,
        market_data=_market_data_mock(),
        risk_status_provider=inactive_risk_status,
    )
    actions = [
        make_action(target=1.0, current=0.0, blocked=True),
        make_action(target=1.0, current=1.0),
        make_action(target=0.0, current=0.0, action_type="none"),
    ]
    plan = make_plan(actions, plan_metadata)

    result = service.execute_plan(plan)

    adapter.submit_order.assert_not_called()
    assert result.orders == []
    assert result.success


def test_execute_plan_creates_buy_and_sell_orders(plan_metadata, inactive_risk_status):
    adapter = RecordingAdapter()
    service = ExecutionService(
        adapter,
        market_data=_market_data_mock(),
        risk_status_provider=inactive_risk_status,
    )
    actions = [
        make_action(target=3.0, current=1.0),
        make_action(target=1.0, current=3.0),
    ]
    plan = make_plan(actions, plan_metadata)

    result = service.execute_plan(plan)

    assert len(adapter.submitted) == 2
    buy_order, sell_order = adapter.submitted
    assert buy_order.side == "buy"
    assert buy_order.requested_base_size == 2.0
    assert sell_order.side == "sell"
    assert sell_order.requested_base_size == 2.0
    assert result.success


def test_execute_plan_records_errors_from_adapter(plan_metadata, inactive_risk_status):
    adapter = RecordingAdapter(exception=ExecutionError("adapter failure"))
    service = ExecutionService(
        adapter,
        market_data=_market_data_mock(),
        risk_status_provider=inactive_risk_status,
    )
    plan = make_plan([make_action(target=2.0, current=0.0)], plan_metadata)

    result = service.execute_plan(plan)

    assert result.errors == ["adapter failure"]
    assert not result.success
    assert result.orders[0].last_error == "adapter failure"


def test_execute_plan_accepts_validated_orders_without_txid(
    plan_metadata, inactive_risk_status
):
    adapter = RecordingAdapter()
    service = ExecutionService(
        adapter,
        market_data=_market_data_mock(),
        risk_status_provider=inactive_risk_status,
    )
    plan = make_plan([make_action(target=1.0, current=0.0)], plan_metadata)

    result = service.execute_plan(plan)

    order = result.orders[0]
    assert order.status == "validated"
    assert order.kraken_order_id is None
    assert result.success


def test_execute_plan_rejects_stale_plan(plan_metadata, inactive_risk_status):
    adapter = MagicMock()
    market_data = _market_data_mock()
    config = ExecutionConfig(max_plan_age_seconds=1)
    adapter.config = config

    service = ExecutionService(
        adapter,
        config=config,
        market_data=market_data,
        risk_status_provider=inactive_risk_status,
    )

    stale_time = datetime.now(UTC) - timedelta(seconds=5)
    plan = make_plan(
        [make_action(target=1.0, current=0.0)], plan_metadata, generated_at=stale_time
    )

    result = service.execute_plan(plan)

    adapter.submit_order.assert_not_called()
    market_data.get_pair_metadata_or_raise.assert_not_called()
    assert not result.success
    assert any("max_plan_age_seconds" in reason for reason in result.errors)


def test_execute_plan_treats_routing_failure_as_error(inactive_risk_status):
    adapter = MagicMock()
    market_data = _market_data_mock()
    plan_metadata = {"order_type": "limit"}

    service = ExecutionService(
        adapter, market_data=market_data, risk_status_provider=inactive_risk_status
    )

    plan = make_plan([make_action(target=1.0, current=0.0)], plan_metadata)

    result = service.execute_plan(plan)

    adapter.submit_order.assert_not_called()
    assert not result.orders
    assert not result.success
    assert result.warnings
    assert result.errors
    assert "Invalid bid/ask data" in result.errors[0]


def test_execute_plan_fetches_price_for_buy_only(inactive_risk_status):
    """Verify that only risk-increasing BUY orders trigger a price fetch when notional checks are active."""
    config = ExecutionConfig(
        min_order_notional_usd=20.0, validate_only=True, mode="paper"
    )
    adapter = RecordingAdapter(config=config)
    market_data = _market_data_mock()
    market_data.get_latest_price.return_value = 123.0

    service = ExecutionService(
        adapter=adapter,
        config=config,
        market_data=market_data,
        risk_status_provider=inactive_risk_status,
    )

    market_metadata = {"order_type": "market"}

    # BUY Case: Risk increasing, price fetch expected
    buy_plan = make_plan(
        [
            make_action(
                target=2.0, current=1.0, action_type="open"
            )  # delta +1, risk-increasing
        ],
        market_metadata,
    )
    service.execute_plan(buy_plan)

    market_data.get_latest_price.assert_called_once()
    assert len(adapter.submitted) == 1

    # Reset mock for SELL case
    market_data.get_latest_price.reset_mock()

    # SELL Case: Risk increasing (short open), price fetch NOT expected
    sell_plan = make_plan(
        [
            make_action(
                target=0.0, current=1.0, action_type="open"
            )  # delta -1, risk-increasing
        ],
        market_metadata,
    )
    service.execute_plan(sell_plan)

    market_data.get_latest_price.assert_not_called()
    assert len(adapter.submitted) == 2  # Total submitted (1 buy + 1 sell)


def test_execute_plan_live_mode_fail_closed_logic(inactive_risk_status):
    """Verify that live mode fails closed on missing price ONLY for risk-increasing BUYs."""
    config = ExecutionConfig(
        mode="live", min_order_notional_usd=20.0, validate_only=True
    )
    adapter = RecordingAdapter(config=config)
    market_data = _market_data_mock()
    market_data.get_latest_price.side_effect = Exception("boom")

    service = ExecutionService(
        adapter=adapter,
        config=config,
        market_data=market_data,
        risk_status_provider=inactive_risk_status,
    )

    market_metadata = {"order_type": "market"}

    # BUY Case: Risk increasing, fails closed due to price error
    buy_plan = make_plan(
        [make_action(target=2.0, current=1.0, action_type="open")], market_metadata
    )
    result_buy = service.execute_plan(buy_plan)

    assert not result_buy.success
    assert len(adapter.submitted) == 0
    assert "Latest price unavailable in live mode" in str(result_buy.errors)

    # Reset mock (though side_effect remains)
    market_data.get_latest_price.reset_mock()

    # SELL Case: Risk increasing, proceeds despite missing price (never calls fetch)
    sell_plan = make_plan(
        [make_action(target=0.0, current=1.0, action_type="open")], market_metadata
    )
    result_sell = service.execute_plan(sell_plan)

    assert result_sell.success
    assert len(adapter.submitted) == 1
    market_data.get_latest_price.assert_not_called()
