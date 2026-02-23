from decimal import Decimal
from unittest.mock import MagicMock

from kraken_bot.portfolio.portfolio import Portfolio


def test_get_quantizer_caching():
    """Verify that _get_quantizer caches its results and returns correct Decimals."""

    # Clear cache first to ensure a clean state
    Portfolio._get_quantizer.cache_clear()

    # 1. Call for 8 decimals
    q1 = Portfolio._get_quantizer(8)
    # Expected: "1." + "0"*8 = "1.00000000"
    assert q1 == Decimal("1.00000000")
    assert q1.as_tuple().exponent == -8

    info1 = Portfolio._get_quantizer.cache_info()
    assert info1.misses == 1
    assert info1.hits == 0
    assert info1.currsize == 1

    # 2. Call again for 8 decimals
    q2 = Portfolio._get_quantizer(8)
    assert q2 == q1

    info2 = Portfolio._get_quantizer.cache_info()
    assert info2.misses == 1
    assert info2.hits == 1

    # 3. Call for 2 decimals
    q3 = Portfolio._get_quantizer(2)
    # Expected: "1." + "0"*2 = "1.00"
    assert q3 == Decimal("1.00")
    assert q3.as_tuple().exponent == -2

    info3 = Portfolio._get_quantizer.cache_info()
    assert info3.misses == 2
    assert info3.hits == 1
    assert info3.currsize == 2


def test_round_vol_uses_quantizer():
    """Verify _round_vol uses the cached quantizer correctly."""
    mock_config = MagicMock()
    mock_market_data = MagicMock()

    meta = MagicMock()
    meta.volume_decimals = 8
    mock_market_data.get_pair_metadata.return_value = meta

    portfolio = Portfolio(
        config=mock_config, market_data=mock_market_data, store=MagicMock()
    )

    # Ensure cache is primed or empty
    Portfolio._get_quantizer.cache_clear()

    vol = 1.000000019
    rounded = portfolio._round_vol("XBTUSD", vol)

    # Should be floored to 8 decimals
    assert rounded == 1.00000001

    # Check that cache was populated
    assert Portfolio._get_quantizer.cache_info().misses >= 1
    assert Portfolio._get_quantizer.cache_info().currsize >= 1


def test_round_price_uses_quantizer():
    """Verify _round_price uses the cached quantizer correctly."""
    mock_config = MagicMock()
    mock_market_data = MagicMock()

    meta = MagicMock()
    meta.price_decimals = 2
    mock_market_data.get_pair_metadata.return_value = meta

    portfolio = Portfolio(
        config=mock_config, market_data=mock_market_data, store=MagicMock()
    )

    price = 50000.126
    rounded = portfolio._round_price("XBTUSD", price)

    # Should be rounded half up to 2 decimals -> 50000.13
    assert rounded == 50000.13
