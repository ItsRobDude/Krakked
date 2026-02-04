from decimal import Decimal
from unittest.mock import MagicMock

from kraken_bot.portfolio.portfolio import Portfolio


def test_quantizer_caching():
    """Verify that _get_quantizer correctly creates and caches Decimal quantizers."""

    # Ensure method exists (will fail if not implemented)
    assert hasattr(Portfolio, "_get_quantizer")

    # 1. Correctness
    q2 = Portfolio._get_quantizer(2)
    assert q2 == Decimal("1.00")
    assert isinstance(q2, Decimal)

    q8 = Portfolio._get_quantizer(8)
    assert q8 == Decimal("1.00000000")

    # 2. Caching behavior
    Portfolio._get_quantizer.cache_clear()
    info_before = Portfolio._get_quantizer.cache_info()

    # First call - miss
    Portfolio._get_quantizer(3)
    info_after_1 = Portfolio._get_quantizer.cache_info()
    assert info_after_1.misses == info_before.misses + 1

    # Second call - hit
    Portfolio._get_quantizer(3)
    info_after_2 = Portfolio._get_quantizer.cache_info()
    assert info_after_2.hits == info_before.hits + 1

    # Different key - miss
    Portfolio._get_quantizer(4)
    info_after_3 = Portfolio._get_quantizer.cache_info()
    assert info_after_3.misses == info_after_2.misses + 1


def test_rounding_uses_cache():
    """Verify that rounding methods utilize the cached quantizer."""
    # Mock portfolio and market data
    mock_market_data = MagicMock()
    mock_config = MagicMock()
    mock_store = MagicMock()

    portfolio = Portfolio(mock_config, mock_market_data, mock_store)

    # Setup mock metadata
    pair_meta = MagicMock()
    pair_meta.volume_decimals = 4
    pair_meta.price_decimals = 2
    mock_market_data.get_pair_metadata.return_value = pair_meta

    # Ensure cache is clean
    if hasattr(Portfolio, "_get_quantizer"):
        Portfolio._get_quantizer.cache_clear()

    # Call _round_vol
    res = portfolio._round_vol("XBT/USD", 1.23456)
    assert res == 1.2345

    # Should be 1 miss (for decimals=4)
    assert Portfolio._get_quantizer.cache_info().misses == 1
    assert Portfolio._get_quantizer.cache_info().hits == 0

    # Call again
    res = portfolio._round_vol("XBT/USD", 1.23456)

    # Should be 1 hit
    assert Portfolio._get_quantizer.cache_info().misses == 1
    assert Portfolio._get_quantizer.cache_info().hits == 1
