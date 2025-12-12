from unittest.mock import MagicMock

from kraken_bot.portfolio.portfolio import Portfolio


def test_portfolio_rounding_handles_dust_and_flooring():
    """
    Test that the portfolio logic correctly handles:
    1. ROUND_FLOOR for volumes (avoiding overestimation).
    2. Snap-to-zero for phantom dust via Decimal quantization.
    3. Negative dust drift is clamped to zero (e.g. 1.0 - 1.0 = -1e-17).
    """
    mock_config = MagicMock()
    mock_config.valuation_pairs = {}
    mock_config.base_currency = "USD"

    mock_market_data = MagicMock()
    meta = MagicMock()
    meta.canonical = "XBTUSD"
    meta.volume_decimals = 8  # BTC Standard
    meta.price_decimals = 2
    mock_market_data.get_pair_metadata.return_value = meta
    mock_market_data.get_latest_price.return_value = 50000.0

    portfolio = Portfolio(
        config=mock_config, market_data=mock_market_data, store=MagicMock()
    )

    # 1. Test ROUND_FLOOR
    # If we buy 1.000000019, it should truncate to 1.00000001 (8 decimals)
    # Standard round() would make this 1.00000002
    buy_vol = 1.000000019
    portfolio._process_trade(
        {
            "pair": "XBTUSD",
            "type": "buy",
            "price": "50000.0",
            "vol": str(buy_vol),
            "cost": "50000.0",
            "time": 1000,
            "ordertxid": "ord1",
        }
    )

    pos = portfolio.get_position("XBTUSD")
    # Verify flooring behavior
    assert pos.base_size == 1.00000001

    # 2. Test Phantom Dust / Snap-to-Zero (Positive Drift)
    # We hold 1.00000001. We sell 1.00000001.
    # In float math, this might leave positive dust.
    sell_vol = 1.00000001

    # Manually inject a tiny positive float error into the position before selling
    pos.base_size = 1.0000000100000005

    portfolio._process_trade(
        {
            "pair": "XBTUSD",
            "type": "sell",
            "price": "55000.0",
            "vol": str(sell_vol),
            "cost": "55000.0",
            "time": 2000,
            "ordertxid": "ord2",
        }
    )

    # Verify snap-to-zero
    assert pos.base_size == 0.0

    # 3. Test Negative Dust / Safety Clamp
    # Simulate a case where floating point subtraction yields a negative tiny number.
    # e.g. 1.0 - 1.0000000000000002 -> -2e-16
    # We want max(0.0, ...) to catch this before flooring.

    # Reset position to exactly 1.0
    pos.base_size = 1.0

    # Sell exactly 1.0, but we'll simulate the position being ever-so-slightly less than 1.0 internally
    # due to previous float math, or we sell slightly more than we think we have (e.g. rounding diffs).
    # Let's just simulate the internal state being slightly "under" what we are selling.
    pos.base_size = 0.9999999999999999  # Effectively 1.0 but float < 1.0

    portfolio._process_trade(
        {
            "pair": "XBTUSD",
            "type": "sell",
            "price": "55000.0",
            "vol": "1.0",
            "cost": "55000.0",
            "time": 3000,
            "ordertxid": "ord3",
        }
    )

    assert pos.base_size == 0.0
