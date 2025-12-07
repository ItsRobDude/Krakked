from datetime import datetime, timezone

import pytest

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.router import build_order_from_plan_action
from kraken_bot.market_data.models import PairMetadata
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


def test_local_order_preserves_strategy_id_and_userref():
    pair_metadata = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XBT/USD",
        ws_symbol="XBT/USD",
        raw_name="XBTUSD",
        price_decimals=1,
        volume_decimals=4,
        lot_size=0.0001,
        min_order_size=0.0001,
        status="online",
    )
    action = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="trend_core",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="test",
        blocked=False,
        blocked_reasons=[],
        risk_limits_snapshot={},
        strategy_tag="trend_core",
        userref=42,
    )

    plan = ExecutionPlan(
        plan_id="plan_1",
        generated_at=datetime.now(timezone.utc),
        actions=[action],
        metadata={"order_type": "market"},
    )

    order, warning = build_order_from_plan_action(
        action, plan, pair_metadata, config=ExecutionConfig()
    )

    assert warning is None
    assert order.strategy_id == "trend_core"
    assert order.userref == 42


def test_rounding_and_min_size_enforced():
    pair_metadata = PairMetadata(
        canonical="ETHUSD",
        base="ETH",
        quote="USD",
        rest_symbol="ETH/USD",
        ws_symbol="ETH/USD",
        raw_name="ETHUSD",
        price_decimals=2,
        volume_decimals=5,
        lot_size=0.00001,
        min_order_size=0.001,
        status="online",
    )

    action = RiskAdjustedAction(
        pair="ETHUSD",
        strategy_id="alpha",
        action_type="close",
        target_base_size=0.0,
        target_notional_usd=0.0,
        current_base_size=0.00095,
        reason="flatten",
        blocked=False,
        blocked_reasons=[],
        risk_limits_snapshot={},
        strategy_tag="alpha",
        userref=None,
    )
    plan = ExecutionPlan(
        plan_id="plan_rounding",
        generated_at=datetime.now(timezone.utc),
        actions=[action],
        metadata={"order_type": "limit", "requested_price": 123.456},
    )

    with pytest.raises(ValueError):
        build_order_from_plan_action(action, plan, pair_metadata, ExecutionConfig())

    action.current_base_size = 0.00123456
    order, warning = build_order_from_plan_action(
        action, plan, pair_metadata, ExecutionConfig()
    )

    assert warning is None
    assert order.requested_base_size == pytest.approx(0.00123)
    assert order.requested_price == pytest.approx(123.46)
