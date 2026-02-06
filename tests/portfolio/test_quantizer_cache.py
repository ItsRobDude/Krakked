from decimal import Decimal

from kraken_bot.portfolio.portfolio import Portfolio


def test_quantizer_caching_behavior():
    """Verify that _get_quantizer uses lru_cache correctly."""

    # 1. Clear cache to start fresh
    Portfolio._get_quantizer.cache_clear()

    # 2. First call - should be a miss
    q1 = Portfolio._get_quantizer(8)
    assert q1 == Decimal("1.00000000")

    info = Portfolio._get_quantizer.cache_info()
    assert info.hits == 0
    assert info.misses == 1
    assert info.currsize == 1

    # 3. Second call with same arg - should be a hit
    q2 = Portfolio._get_quantizer(8)
    assert q2 is q1  # Should be the exact same object

    info = Portfolio._get_quantizer.cache_info()
    assert info.hits == 1
    assert info.misses == 1

    # 4. Third call with different arg - should be a miss
    q3 = Portfolio._get_quantizer(2)
    assert q3 == Decimal("1.00")

    info = Portfolio._get_quantizer.cache_info()
    assert info.hits == 1
    assert info.misses == 2
    assert info.currsize == 2

    # 5. Verify maxsize is respected (optional, but good sanity check)
    assert info.maxsize == 128
