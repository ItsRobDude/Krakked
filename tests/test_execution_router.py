from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from kraken_bot.config import ExecutionConfig
from kraken_bot.execution.router import (
    build_order_from_plan_action,
    classify_volume,
    dust_reason,
)
from kraken_bot.market_data.models import PairMetadata
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction


def test_classify_volume_logic():
    meta = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        raw_name="XXBTZUSD",
        min_order_size=0.0001,
        volume_decimals=4,
        price_decimals=1,
        lot_size=1,
        status="online",
    )

    # 1. OK case
    rounded, ok = classify_volume(meta, 0.0002)
    assert rounded == 0.0002
    assert ok is True

    # 2. Dust case
    rounded, ok = classify_volume(meta, 0.00009)
    # round_order_size(0.00009, decimals=4) -> 0.0000
    assert rounded == 0.0
    assert ok is False

    # 3. Exact boundary
    rounded, ok = classify_volume(meta, 0.0001)
    assert rounded == 0.0001
    assert ok is True


def test_dust_reason_format():
    meta = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        raw_name="XXBTZUSD",
        min_order_size=0.0001,
        volume_decimals=4,
        price_decimals=1,
        lot_size=1,
        status="online",
    )
    msg = dust_reason(meta, 0.00009, 0.0)
    assert "Dust:" in msg
    assert "rounded sell volume 0.0" in msg
    assert "min_order_size 0.0001" in msg
    assert "XBTUSD" in msg


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
        userref="42",
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


def test_rounding_and_min_size_enforced_via_classify_volume():
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

    # Dust volume
    action = RiskAdjustedAction(
        pair="ETHUSD",
        strategy_id="alpha",
        action_type="close",
        target_base_size=0.0,
        target_notional_usd=0.0,
        current_base_size=0.00095,  # < min 0.001
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

    # Should raise ValueError from helper
    with pytest.raises(ValueError) as exc:
        build_order_from_plan_action(action, plan, pair_metadata, ExecutionConfig())
    assert "Dust:" in str(exc.value)

    # Valid volume
    action.current_base_size = 0.00123456
    order, warning = build_order_from_plan_action(
        action, plan, pair_metadata, ExecutionConfig()
    )

    assert warning is None
    assert order.requested_base_size == pytest.approx(0.00123)
    assert order.requested_price == pytest.approx(123.46)


def test_risk_reducing_detection():
    """Test that risk_reducing flag is correctly set on LocalOrder."""
    pair_metadata = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        raw_name="XXBTZUSD",
        min_order_size=0.0001,
        volume_decimals=8,
        price_decimals=1,
        lot_size=1,
        status="online",
        liquidity_24h_usd=1000000.0,
    )
    exec_config = ExecutionConfig(mode="paper", default_order_type="limit")

    # Case 1: Reduce (Long -> Less Long)
    action_reduce = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="test",
        action_type="reduce",  # Explicit reduce
        target_base_size=0.5,
        target_notional_usd=25000,
        current_base_size=1.0,
        reason="Test Reduce",
        blocked=False,
        blocked_reasons=[],
    )

    plan = ExecutionPlan(
        plan_id="p1", generated_at=MagicMock(), actions=[action_reduce]
    )

    order, warning = build_order_from_plan_action(
        action=action_reduce,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock(),
    )

    assert order is not None
    assert order.risk_reducing is True

    # Case 2: Increase (Long -> More Long)
    action_increase = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="test",
        action_type="increase",
        target_base_size=1.5,
        target_notional_usd=75000,
        current_base_size=1.0,
        reason="Test Increase",
        blocked=False,
        blocked_reasons=[],
    )

    order_inc, _ = build_order_from_plan_action(
        action=action_increase,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock(),
    )

    assert order_inc is not None
    assert order_inc.risk_reducing is False

    # Case 3: Close (Long -> Flat)
    action_close = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="test",
        action_type="close",
        target_base_size=0.0,
        target_notional_usd=0,
        current_base_size=1.0,
        reason="Test Close",
        blocked=False,
        blocked_reasons=[],
    )

    order_close, _ = build_order_from_plan_action(
        action=action_close,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock(),
    )

    assert order_close is not None
    assert order_close.risk_reducing is True


def test_risk_reducing_open():
    """Test that opening a new position is NOT risk reducing."""
    pair_metadata = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        raw_name="XXBTZUSD",
        min_order_size=0.0001,
        volume_decimals=8,
        price_decimals=1,
        lot_size=1,
        status="online",
        liquidity_24h_usd=1000000.0,
    )
    exec_config = ExecutionConfig(mode="paper", default_order_type="limit")

    action_open = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="test",
        action_type="open",
        target_base_size=0.1,
        target_notional_usd=5000,
        current_base_size=0.0,
        reason="Test Open",
        blocked=False,
        blocked_reasons=[],
    )

    plan = ExecutionPlan(plan_id="p1", generated_at=MagicMock(), actions=[action_open])

    order, _ = build_order_from_plan_action(
        action=action_open,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock(),
    )

    assert order.risk_reducing is False
