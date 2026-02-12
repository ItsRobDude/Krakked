import pytest
from unittest.mock import MagicMock
from kraken_bot.portfolio.portfolio import Portfolio
from kraken_bot.market_data.models import PairMetadata


@pytest.fixture
def mock_market_data():
    md = MagicMock()
    # Setup default metadata for a test pair
    md.get_pair_metadata.return_value = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        raw_name="XXBTZUSD",
        price_decimals=2,
        volume_decimals=8,
        lot_size=1.0,
        min_order_size=0.0001,
        status="online"
    )
    return md


def test_quantizer_caching(mock_market_data):
    # Setup dependencies
    config = MagicMock()
    store = MagicMock()

    # Initialize Portfolio
    portfolio = Portfolio(config, mock_market_data, store)

    # Ensure cache is clear before starting (since it's a static method on the class)
    Portfolio._get_quantizer.cache_clear()

    # Check initial cache info (should be empty/cold)
    initial_info = Portfolio._get_quantizer.cache_info()
    assert initial_info.hits == 0
    assert initial_info.misses == 0

    # Call _round_vol (uses volume_decimals=8)
    vol_result = portfolio._round_vol("XBTUSD", 1.234567891)
    assert vol_result == 1.23456789  # Rounded down to 8 decimals

    # Expect 1 miss (first call for 8 decimals)
    info_after_first = Portfolio._get_quantizer.cache_info()
    assert info_after_first.misses == 1
    assert info_after_first.hits == 0

    # Call again
    vol_result_2 = portfolio._round_vol("XBTUSD", 1.111111119)
    assert vol_result_2 == 1.11111111

    # Expect 1 hit
    info_after_second = Portfolio._get_quantizer.cache_info()
    assert info_after_second.hits == 1
    assert info_after_second.misses == 1

    # Call _round_price (uses price_decimals=2)
    price_result = portfolio._round_price("XBTUSD", 50000.126)
    assert price_result == 50000.13  # Rounded half up to 2 decimals

    # Expect another miss (new decimal count 2)
    info_after_price = Portfolio._get_quantizer.cache_info()
    assert info_after_price.hits == 1
    assert info_after_price.misses == 2

    # Call _round_price again
    price_result_2 = portfolio._round_price("XBTUSD", 50000.124)
    assert price_result_2 == 50000.12

    # Expect another hit
    info_final = Portfolio._get_quantizer.cache_info()
    assert info_final.hits == 2
    assert info_final.misses == 2
