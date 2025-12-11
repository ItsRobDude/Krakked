
from unittest.mock import MagicMock
from kraken_bot.portfolio.portfolio import Portfolio

def test_portfolio_rounding_handles_dust():
    """
    Test that the portfolio logic correctly rounds floating point dust
    when closing a position completely.
    """
    # Mock Config
    mock_config = MagicMock()
    mock_config.valuation_pairs = {}
    mock_config.base_currency = "USD"

    # Mock MarketData
    mock_market_data = MagicMock()
    # Setup BTCUSD metadata with 8 decimal places for volume
    meta = MagicMock()
    meta.canonical = "XBTUSD"
    meta.base = "XBT"
    meta.quote = "USD"
    meta.volume_decimals = 8
    meta.price_decimals = 2
    mock_market_data.get_pair_metadata.return_value = meta

    # Setup price for valuation (not strictly needed for size logic but used in flow)
    mock_market_data.get_latest_price.return_value = 50000.0

    # Mock Store
    mock_store = MagicMock()

    portfolio = Portfolio(
        config=mock_config,
        market_data=mock_market_data,
        store=mock_store
    )

    # 1. Buy 1.00000001 BTC
    # Using a float that might introduce representation error if we weren't careful,
    # though 1.00000001 is usually fine. Let's try to construct a case or just trust the logic.
    # The key test is: does it round?

    buy_vol = 1.00000001
    buy_trade = {
        "pair": "XBTUSD",
        "type": "buy",
        "price": "50000.0",
        "vol": str(buy_vol),
        "cost": str(buy_vol * 50000.0),
        "time": 1000,
        "ordertxid": "ord1"
    }
    portfolio._process_trade(buy_trade)

    pos = portfolio.get_position("XBTUSD")
    assert pos.base_size == 1.00000001

    # 2. Sell exactly that amount.
    # In pure float math, 1.00000001 - 1.00000001 is usually 0.0, but let's try a case that drifts.
    # E.g. 0.3 can be tricky.

    sell_trade = {
        "pair": "XBTUSD",
        "type": "sell",
        "price": "55000.0",
        "vol": str(buy_vol),
        "cost": str(buy_vol * 55000.0),
        "time": 2000,
        "ordertxid": "ord2"
    }

    # We expect _process_trade to subtract and then ROUND.
    # If we manually injected dust, say:
    portfolio.positions["XBTUSD"].base_size = 1.0000000100000002  # tiny dust

    portfolio._process_trade(sell_trade)

    pos = portfolio.get_position("XBTUSD")
    # With rounding (8 decimals), 0.0000000000000002 should become 0.0
    assert pos.base_size == 0.0
