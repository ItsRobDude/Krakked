from datetime import datetime
from unittest.mock import MagicMock

import pytest

from kraken_bot.execution.exceptions import ExecutionError
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


class RecordingAdapter:
    def __init__(self, exception: Exception | None = None):
        self.submitted = []
        self.exception = exception

    def submit_order(self, order: LocalOrder) -> LocalOrder:
        if self.exception:
            raise self.exception
        self.submitted.append(order)
        order.status = "validated"
        return order


@pytest.fixture
def plan_metadata():
    return {"order_type": "limit", "requested_price": 100.0}


def make_action(target: float, current: float, blocked: bool = False, action_type: str = "open"):
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


def make_plan(actions, metadata):
    return ExecutionPlan(plan_id="plan-123", generated_at=datetime.utcnow(), actions=actions, metadata=metadata)


def test_execute_plan_skips_blocked_noop_and_zero_delta(plan_metadata):
    adapter = MagicMock()
    service = ExecutionService(adapter)
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


def test_execute_plan_creates_buy_and_sell_orders(plan_metadata):
    adapter = RecordingAdapter()
    service = ExecutionService(adapter)
    actions = [make_action(target=3.0, current=1.0), make_action(target=1.0, current=3.0)]
    plan = make_plan(actions, plan_metadata)

    result = service.execute_plan(plan)

    assert len(adapter.submitted) == 2
    buy_order, sell_order = adapter.submitted
    assert buy_order.side == "buy"
    assert buy_order.requested_base_size == 2.0
    assert sell_order.side == "sell"
    assert sell_order.requested_base_size == 2.0
    assert result.success


def test_execute_plan_records_errors_from_adapter(plan_metadata):
    adapter = RecordingAdapter(exception=ExecutionError("adapter failure"))
    service = ExecutionService(adapter)
    plan = make_plan([make_action(target=2.0, current=0.0)], plan_metadata)

    result = service.execute_plan(plan)

    assert result.errors == ["adapter failure"]
    assert not result.success
    assert result.orders[0].last_error == "adapter failure"


def test_execute_plan_accepts_validated_orders_without_txid(plan_metadata):
    adapter = RecordingAdapter()
    service = ExecutionService(adapter)
    plan = make_plan([make_action(target=1.0, current=0.0)], plan_metadata)

    result = service.execute_plan(plan)

    order = result.orders[0]
    assert order.status == "validated"
    assert order.kraken_order_id is None
    assert result.success
