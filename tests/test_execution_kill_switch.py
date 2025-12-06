from datetime import UTC, datetime
from types import SimpleNamespace
from typing import List

import pytest

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


def _build_action(
    pair: str,
    *,
    target_base_size: float = 1.0,
    current_base_size: float = 0.0,
    blocked: bool = False,
) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair=pair,
        strategy_id="test_strategy",
        action_type="open",
        target_base_size=target_base_size,
        target_notional_usd=abs(target_base_size) * 100.0,
        current_base_size=current_base_size,
        reason="",
        blocked=blocked,
        blocked_reasons=[] if not blocked else ["blocked"],
        strategy_tag="test_strategy",
        risk_limits_snapshot={},
    )


class FakeAdapter:
    def __init__(self, config: ExecutionConfig | None = None):
        self.config = config or ExecutionConfig()
        self.submit_order_calls: List[LocalOrder] = []
        self.cancel_order_calls: List[LocalOrder] = []
        self.cancel_all_calls: int = 0
        self.client = SimpleNamespace(
            get_open_orders=lambda params=None: {},
            get_closed_orders=lambda: {},
        )

    def submit_order(self, order: LocalOrder) -> LocalOrder:
        self.submit_order_calls.append(order)
        return order

    def cancel_order(self, order: LocalOrder) -> None:
        self.cancel_order_calls.append(order)

    def cancel_all_orders(self) -> None:
        self.cancel_all_calls += 1


def _kill_switch_provider():
    return SimpleNamespace(kill_switch_active=True)


class ToggleableRiskEngine:
    def __init__(self):
        self.manual_kill_switch_active = False

    def set_manual_kill_switch(self, active: bool) -> None:
        self.manual_kill_switch_active = active

    def get_status(self) -> SimpleNamespace:
        return SimpleNamespace(kill_switch_active=self.manual_kill_switch_active)


def test_execute_plan_blocked_by_kill_switch():
    adapter = FakeAdapter()
    service = ExecutionService(
        adapter=adapter, risk_status_provider=_kill_switch_provider
    )

    plan = ExecutionPlan(
        plan_id="plan_kill_switch",
        generated_at=datetime.now(UTC),
        actions=[
            _build_action("XBTUSD"),
            _build_action("ETHUSD", target_base_size=2.0),
        ],
    )

    result = service.execute_plan(plan)

    assert not result.success
    assert any("kill switch" in msg.lower() for msg in result.errors)
    assert not adapter.submit_order_calls
    assert len(result.orders) == 2
    assert all(order.status == "rejected" for order in result.orders)
    assert all("kill_switch" in (order.last_error or "") for order in result.orders)


def test_execute_plan_blocks_when_risk_provider_missing():
    adapter = FakeAdapter()
    market_data = SimpleNamespace(
        get_best_bid_ask=lambda pair: {"bid": 100.0, "ask": 100.0}
    )
    service = ExecutionService(adapter=adapter, market_data=market_data)

    plan = ExecutionPlan(
        plan_id="plan_missing_risk_provider",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD")],
        metadata={"order_type": "market"},
    )

    result = service.execute_plan(plan)

    assert result.success
    assert adapter.submit_order_calls
    assert not result.errors


def test_cancel_operations_allowed_with_kill_switch():
    adapter = FakeAdapter()
    service = ExecutionService(
        adapter=adapter, risk_status_provider=_kill_switch_provider
    )

    order = LocalOrder(
        local_id="local-1",
        plan_id="plan_cancel",
        strategy_id="test_strategy",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
    )

    service.cancel_order(order)
    assert len(adapter.cancel_order_calls) == 1

    service.cancel_all()
    assert adapter.cancel_all_calls == 1


def test_kill_switch_blocks_all_eligible_actions_with_truncation_config():
    adapter = FakeAdapter(config=ExecutionConfig(max_concurrent_orders=1))
    service = ExecutionService(
        adapter=adapter, risk_status_provider=_kill_switch_provider
    )

    actions = [
        _build_action("XBTUSD", target_base_size=1.0),
        _build_action("ETHUSD", target_base_size=-1.5),
        _build_action("SOLUSD", target_base_size=0.0),
        _build_action("ADAUSD", target_base_size=2.0, blocked=True),
    ]
    plan = ExecutionPlan(
        plan_id="plan_truncated_kill_switch",
        generated_at=datetime.now(UTC),
        actions=actions,
    )

    result = service.execute_plan(plan)

    eligible_orders = [
        a
        for a in actions
        if not a.blocked and (a.target_base_size - a.current_base_size) != 0
    ]

    assert len(result.orders) == len(eligible_orders)
    assert all(order.status == "rejected" for order in result.orders)
    assert all("kill_switch" in (order.last_error or "") for order in result.orders)
    assert any("kill switch" in msg.lower() for msg in result.errors)
    assert not adapter.submit_order_calls


def test_risk_provider_follows_strategy_engine_kill_switch_state():
    adapter = FakeAdapter()
    risk_engine = ToggleableRiskEngine()
    service = ExecutionService(
        adapter=adapter, risk_status_provider=risk_engine.get_status
    )

    plan = ExecutionPlan(
        plan_id="plan_manual_kill_switch",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD")],
    )

    risk_engine.set_manual_kill_switch(True)
    result = service.execute_plan(plan)

    assert not result.success
    assert adapter.submit_order_calls == []
    assert all("kill_switch" in (order.last_error or "") for order in result.orders)


def test_live_mode_requires_risk_provider():
    adapter = FakeAdapter(config=ExecutionConfig(mode="live", allow_live_trading=True))

    with pytest.raises(ValueError, match="risk_status_provider"):
        ExecutionService(adapter=adapter)


def test_live_mode_provider_error_blocks_execution():
    adapter = FakeAdapter(config=ExecutionConfig(mode="live", allow_live_trading=True))

    def _erroring_provider() -> SimpleNamespace:
        raise RuntimeError("risk down")

    service = ExecutionService(
        adapter=adapter, risk_status_provider=_erroring_provider
    )

    plan = ExecutionPlan(
        plan_id="plan_live_error",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD")],
    )

    result = service.execute_plan(plan)

    assert not result.success
    assert adapter.submit_order_calls == []
    assert all("kill_switch" in (order.last_error or "") for order in result.orders)


def test_non_live_provider_error_allows_execution():
    adapter = FakeAdapter(config=ExecutionConfig(mode="paper"))

    def _erroring_provider() -> SimpleNamespace:
        raise RuntimeError("risk down")

    market_data = SimpleNamespace(
        get_best_bid_ask=lambda pair: {"bid": 100.0, "ask": 100.0}
    )

    service = ExecutionService(
        adapter=adapter,
        risk_status_provider=_erroring_provider,
        market_data=market_data,
    )

    plan = ExecutionPlan(
        plan_id="plan_non_live_error",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD")],
        metadata={"order_type": "market"},
    )

    result = service.execute_plan(plan)

    assert result.success
    assert adapter.submit_order_calls
