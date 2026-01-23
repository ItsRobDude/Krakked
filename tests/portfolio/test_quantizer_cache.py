
from decimal import Decimal
from kraken_bot.portfolio.portfolio import Portfolio


def test_quantizer_cache_correctness_and_hits():
    """
    Verify that _get_quantizer returns correct values and uses the cache.
    """
    # 1. Clear cache to start fresh
    Portfolio._get_quantizer.cache_clear()

    # 2. First call: Cache miss
    q1 = Portfolio._get_quantizer(8)
    # The implementation returns Decimal("1." + "0"*8) -> "1.00000000"
    assert q1 == Decimal("1.00000000")

    # Verify string representation matches expected precision format
    assert str(Portfolio._get_quantizer(2)) == "1.00"
    assert str(Portfolio._get_quantizer(8)) == "1.00000000"

    info = Portfolio._get_quantizer.cache_info()
    # Called for 8 (first), 2 (first), 8 (second - hits 8)

    # So expected: Misses=2, Hits=1
    assert info.misses == 2
    assert info.currsize == 2
    assert info.hits == 1

    # 3. Repeat calls: Cache hits
    Portfolio._get_quantizer(8)
    Portfolio._get_quantizer(2)
    Portfolio._get_quantizer(8)

    info = Portfolio._get_quantizer.cache_info()
    # Hits should increase by 3
    assert info.hits == 1 + 3
    assert info.misses == 2  # Stays same
