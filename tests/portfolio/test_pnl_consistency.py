from unittest.mock import MagicMock

from krakked.portfolio.models import AssetBalance
from krakked.portfolio.portfolio import Portfolio


def test_pnl_consistency_buy_fees():
    """
    Verify that fees paid on a BUY trade are immediately reflected in Realized PnL,
    maintaining the invariant: Change in Equity == Realized PnL + Unrealized PnL.
    """
    # Mock Config
    mock_config = MagicMock()
    mock_config.valuation_pairs = {}
    mock_config.base_currency = "USD"
    mock_config.include_assets = []
    mock_config.exclude_assets = []
    mock_config.track_manual_trades = True

    # Mock MarketData
    mock_market_data = MagicMock()
    meta = MagicMock()
    meta.canonical = "XBTUSD"
    meta.base = "XBT"
    meta.quote = "USD"
    meta.volume_decimals = 8
    meta.price_decimals = 2
    mock_market_data.get_pair_metadata.return_value = meta

    # Price is constant to isolate Fee impact
    mock_market_data.get_latest_price.return_value = 100.0

    def _norm(asset):
        asset = str(asset)
        return {"XXBT": "XBT", "XBT": "XBT", "ZUSD": "USD", "USD": "USD"}.get(
            asset, asset
        )

    mock_market_data.normalize_asset.side_effect = _norm
    mock_market_data.get_valuation_pair.side_effect = lambda asset: (
        "XBTUSD" if _norm(asset) == "XBT" else None
    )

    # Mock Store
    mock_store = MagicMock()

    portfolio = Portfolio(
        config=mock_config, market_data=mock_market_data, store=mock_store
    )

    # Initial State: 1000 USD Cash
    # Use real AssetBalance objects
    portfolio.balances = {
        "USD": AssetBalance(asset="USD", free=1000.0, reserved=0.0, total=1000.0)
    }

    initial_equity = portfolio.equity_view().equity_base
    assert initial_equity == 1000.0

    # Action: Buy 1 XBT @ 100 USD. Fee = 1 USD.
    # Cost = Price * Vol = 100 * 1 = 100.
    # Total spent = 101.
    buy_trade = {
        "pair": "XBTUSD",
        "type": "buy",
        "price": "100.0",
        "vol": "1.0",
        "cost": "100.0",
        "fee": "1.0",
        "time": 1000,
        "ordertxid": "ord1",
    }

    # Update balances manually to simulate the trade effect on cash/asset
    portfolio.balances["USD"].total -= 101.0  # 100 cost + 1 fee
    portfolio.balances["USD"].free -= 101.0
    portfolio.balances["XBT"] = AssetBalance(
        asset="XBT", free=1.0, reserved=0.0, total=1.0
    )

    portfolio._process_trade(buy_trade)

    # Check Equity
    # Cash: 899.0
    # XBT Value: 1.0 * 100.0 = 100.0
    # Total Equity: 999.0
    final_equity = portfolio.equity_view().equity_base
    assert final_equity == 999.0

    equity_delta = final_equity - initial_equity  # -1.0
    # Check PnL
    # Unrealized: (Current Price 100 - Avg Entry 100) * 1 = 0.0
    # Realized: Should be -1.0 (the fee)

    view = portfolio.equity_view()
    realized = view.realized_pnl_base_total
    unrealized = view.unrealized_pnl_base_total

    assert unrealized == 0.0

    # This assertion is expected to fail if Buy Fees are ignored
    assert realized == -1.0, f"Realized PnL should be -1.0 (fee), got {realized}"
    assert equity_delta == realized + unrealized
