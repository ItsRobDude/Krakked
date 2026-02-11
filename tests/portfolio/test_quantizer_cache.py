from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from kraken_bot.config import PortfolioConfig
from kraken_bot.portfolio.portfolio import Portfolio


@pytest.fixture
def portfolio():
    config = PortfolioConfig()
    market_data = MagicMock()
    store = MagicMock()
    return Portfolio(config, market_data, store)


def test_get_quantizer_correctness(portfolio):
    """Verify that _get_quantizer returns the correct Decimal string."""
    q = Portfolio._get_quantizer(2)
    assert q == Decimal("1.00")

    q = Portfolio._get_quantizer(8)
    assert q == Decimal("1.00000000")

    q = Portfolio._get_quantizer(0)
    assert q == Decimal("1.")


def test_get_quantizer_caching(portfolio):
    """Verify that _get_quantizer results are cached."""
    # Clear cache first to ensure clean state
    Portfolio._get_quantizer.cache_clear()

    # First call
    q1 = Portfolio._get_quantizer(3)
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 1
    assert info.hits == 0

    # Second call with same arg
    q2 = Portfolio._get_quantizer(3)
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 1
    assert info.hits == 1
    assert q1 is q2  # Should be the exact same object

    # Different arg
    Portfolio._get_quantizer(4)
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 2
    assert info.hits == 1


def test_round_vol_uses_cache(portfolio):
    """Verify _round_vol uses the cached quantizer."""
    # Setup mock
    pair = "XBTUSD"
    meta = MagicMock()
    meta.volume_decimals = 5
    portfolio.market_data.get_pair_metadata.return_value = meta

    # Clear cache
    Portfolio._get_quantizer.cache_clear()

    # Call _round_vol
    vol = 1.12345678
    rounded = portfolio._round_vol(pair, vol)

    assert rounded == 1.12345

    # Check cache hits/misses
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 1  # 5 decimals was not in cache

    # Call again
    portfolio._round_vol(pair, vol)
    info = Portfolio._get_quantizer.cache_info()
    assert info.hits == 1  # Should hit cache now


def test_round_price_uses_cache(portfolio):
    """Verify _round_price uses the cached quantizer."""
    # Setup mock
    pair = "XBTUSD"
    meta = MagicMock()
    meta.price_decimals = 2
    portfolio.market_data.get_pair_metadata.return_value = meta

    # Clear cache
    Portfolio._get_quantizer.cache_clear()

    # Call _round_price
    price = 50000.12345
    rounded = portfolio._round_price(pair, price)

    assert rounded == 50000.12

    # Check cache hits/misses
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 1

    # Call again
    portfolio._round_price(pair, price)
    info = Portfolio._get_quantizer.cache_info()
    assert info.hits == 1
