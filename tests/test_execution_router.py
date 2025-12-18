
import pytest
from unittest.mock import MagicMock
from decimal import Decimal

from kraken_bot.execution.router import build_order_from_plan_action, round_order_size, round_order_price
from kraken_bot.execution.models import LocalOrder
from kraken_bot.strategy.models import RiskAdjustedAction, ExecutionPlan
from kraken_bot.market_data.models import PairMetadata
from kraken_bot.config import ExecutionConfig

@pytest.fixture
def pair_metadata():
    return PairMetadata(
        canonical="XBTUSD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        base="XBT",
        quote="USD",
        raw_name="XXBTZUSD",
        min_order_size=0.0001,
        volume_decimals=8,
        price_decimals=1,
        lot_size=1,
        status="online",
        liquidity_24h_usd=1000000.0
    )

@pytest.fixture
def exec_config():
    return ExecutionConfig(
        mode="paper",
        default_order_type="limit"
    )

def test_risk_reducing_detection(pair_metadata, exec_config):
    """Test that risk_reducing flag is correctly set on LocalOrder."""

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
        blocked_reasons=[]
    )

    plan = ExecutionPlan(plan_id="p1", generated_at=MagicMock(), actions=[action_reduce])

    order, warning = build_order_from_plan_action(
        action=action_reduce,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock()
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
        blocked_reasons=[]
    )

    order_inc, _ = build_order_from_plan_action(
        action=action_increase,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock()
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
        blocked_reasons=[]
    )

    order_close, _ = build_order_from_plan_action(
        action=action_close,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock()
    )

    assert order_close is not None
    assert order_close.risk_reducing is True

def test_risk_reducing_open(pair_metadata, exec_config):
    """Test that opening a new position is NOT risk reducing."""
    action_open = RiskAdjustedAction(
        pair="XBTUSD",
        strategy_id="test",
        action_type="open",
        target_base_size=0.1,
        target_notional_usd=5000,
        current_base_size=0.0,
        reason="Test Open",
        blocked=False,
        blocked_reasons=[]
    )

    plan = ExecutionPlan(plan_id="p1", generated_at=MagicMock(), actions=[action_open])

    order, _ = build_order_from_plan_action(
        action=action_open,
        plan=plan,
        pair_metadata=pair_metadata,
        config=exec_config,
        market_data=MagicMock()
    )

    assert order.risk_reducing is False
