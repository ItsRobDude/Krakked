
import pytest
from unittest.mock import MagicMock
from decimal import Decimal

from kraken_bot.execution.adapter import KrakenExecutionAdapter
from kraken_bot.execution.models import LocalOrder
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

def test_adapter_min_notional_risk_reducing_bypass(pair_metadata):
    """Test that a risk-reducing order bypasses min notional check."""
    client = MagicMock()
    # Mock add_order to return valid response so submission succeeds if checks pass
    client.add_order.return_value = {"txid": ["TESTID"], "error": []}

    config = ExecutionConfig(
        mode="paper",
        min_order_notional_usd=1000.0, # High min notional
        validate_only=True
    )

    adapter = KrakenExecutionAdapter(client=client, config=config)

    order = LocalOrder(
        local_id="test1",
        plan_id="p1",
        strategy_id="s1",
        pair="XBTUSD",
        side="sell",
        order_type="market",
        requested_base_size=0.0002,
        risk_reducing=True # Key flag
    )

    # We pass None for price, which would fail if notional check was enforced
    result_order = adapter.submit_order(order, pair_metadata, latest_price=None)

    assert result_order.status == "validated" # or filled/open depending on mode logic, validated for paper
    assert result_order.last_error is None
    # Verify add_order WAS called
    client.add_order.assert_called_once()

def test_adapter_min_notional_risk_increasing_rejected(pair_metadata):
    """Test that a risk-increasing order still fails min notional check."""
    client = MagicMock()

    config = ExecutionConfig(
        mode="paper",
        min_order_notional_usd=1000.0,
        validate_only=True
    )

    adapter = KrakenExecutionAdapter(client=client, config=config)

    order = LocalOrder(
        local_id="test2",
        plan_id="p1",
        strategy_id="s1",
        pair="XBTUSD",
        side="buy",
        order_type="limit",
        requested_base_size=0.0002,
        requested_price=50000.0, # 0.0002 * 50000 = $10 (<< 1000)
        risk_reducing=False
    )

    result_order = adapter.submit_order(order, pair_metadata, latest_price=50000.0)

    assert result_order.status == "rejected"
    assert "below minimum" in result_order.last_error
    client.add_order.assert_not_called()

def test_adapter_missing_price_risk_increasing_rejected(pair_metadata):
    """Test that missing price rejects risk-increasing orders when notional floor set."""
    client = MagicMock()

    config = ExecutionConfig(
        mode="paper",
        min_order_notional_usd=10.0,
        validate_only=True
    )

    adapter = KrakenExecutionAdapter(client=client, config=config)

    order = LocalOrder(
        local_id="test3",
        plan_id="p1",
        strategy_id="s1",
        pair="XBTUSD",
        side="buy",
        order_type="market",
        requested_base_size=0.1,
        risk_reducing=False # Increasing risk
    )

    # Missing price
    result_order = adapter.submit_order(order, pair_metadata, latest_price=None)

    assert result_order.status == "rejected"
    assert "price unavailable" in result_order.last_error
