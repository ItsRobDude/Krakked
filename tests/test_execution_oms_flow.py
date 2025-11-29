from datetime import datetime
from unittest.mock import MagicMock

import pytest

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.adapter import PaperExecutionAdapter
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


def _action(**overrides) -> RiskAdjustedAction:
    base = dict(
        pair="XBTUSD",
        strategy_id="strat",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=50.0,
        current_base_size=0.0,
        reason="",
        blocked=False,
        blocked_reasons=[],
        strategy_tag="tag",
        userref=99,
        risk_limits_snapshot={},
    )
    base.update(overrides)
    return RiskAdjustedAction(**base)


def _plan(actions):
    return ExecutionPlan(
        plan_id="plan",
        generated_at=datetime.utcnow(),
        actions=list(actions),
        metadata={"order_type": "limit", "requested_price": 25.0},
    )


def test_execute_plan_skips_blocked_and_none_actions():
    adapter = MagicMock()
    adapter.config = ExecutionConfig(validate_only=True)
    service = ExecutionService(adapter=adapter)

    actions = [
        _action(blocked=True),
        _action(action_type="none"),
    ]
    plan = _plan(actions)

    result = service.execute_plan(plan)

    assert result.orders == []
    adapter.submit_order.assert_not_called()


def test_execute_plan_builds_buy_and_sell_from_deltas():
    adapter = MagicMock()
    adapter.config = ExecutionConfig(validate_only=True)
    adapter.submit_order.side_effect = lambda order: order
    service = ExecutionService(adapter=adapter)

    actions = [
        _action(target_base_size=2.0, current_base_size=1.0),  # buy 1
        _action(target_base_size=1.0, current_base_size=3.0),  # sell 2
    ]
    plan = _plan(actions)

    result = service.execute_plan(plan)

    assert len(result.orders) == 2
    buy_order, sell_order = result.orders
    assert buy_order.side == "buy"
    assert buy_order.requested_base_size == pytest.approx(1.0)
    assert sell_order.side == "sell"
    assert sell_order.requested_base_size == pytest.approx(2.0)
    assert adapter.submit_order.call_count == 2


def test_execute_plan_guardrail_blocks_order():
    adapter = MagicMock()
    adapter.config = ExecutionConfig(max_pair_notional_usd=10.0)
    service = ExecutionService(adapter=adapter)

    plan = _plan([_action(target_notional_usd=100.0)])

    result = service.execute_plan(plan)

    assert len(result.orders) == 1
    assert result.orders[0].status == "rejected"
    assert "max_pair_notional_usd" in (result.orders[0].last_error or "")
    adapter.submit_order.assert_not_called()


def test_refresh_open_orders_updates_tracked_orders():
    client = MagicMock()
    adapter = PaperExecutionAdapter()
    adapter.client = client
    service = ExecutionService(adapter=adapter)

    order = LocalOrder(
        local_id="1",
        plan_id="plan",
        strategy_id="strategy",
        pair="ETHUSD",
        side="buy",
        order_type="limit",
        userref=7,
        requested_base_size=1.0,
        requested_price=20.0,
    )
    service.register_order(order)

    client.get_open_orders.return_value = {
        "open": {
            "OID123": {"userref": 7, "status": "open", "vol_exec": "0.5", "price": "21.0"}
        }
    }

    service.refresh_open_orders()

    assert order.kraken_order_id == "OID123"
    assert order.status == "open"
    assert order.cumulative_base_filled == pytest.approx(0.5)
    assert order.avg_fill_price == pytest.approx(21.0)


def test_reconcile_orders_closes_and_updates_local_order():
    client = MagicMock()
    adapter = PaperExecutionAdapter()
    adapter.client = client
    service = ExecutionService(adapter=adapter)

    order = LocalOrder(
        local_id="2",
        plan_id="plan",
        strategy_id="strategy",
        pair="ETHUSD",
        side="buy",
        order_type="limit",
        userref=8,
        kraken_order_id="OIDCLOSE",
        requested_base_size=1.0,
        requested_price=20.0,
    )
    service.register_order(order)

    client.get_closed_orders.return_value = {
        "closed": {
            "OIDCLOSE": {"userref": 8, "status": "closed", "vol_exec": "1.0", "price_avg": "22.0"}
        }
    }

    service.reconcile_orders()

    assert order.status == "closed"
    assert order.cumulative_base_filled == pytest.approx(1.0)
    assert order.avg_fill_price == pytest.approx(22.0)
    assert order.local_id not in service.open_orders
