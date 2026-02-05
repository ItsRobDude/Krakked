from decimal import Decimal

from kraken_bot.portfolio.portfolio import Portfolio


def test_quantizer_cache_correctness():
    """Test that _get_quantizer returns correct Decimal values."""
    q8 = Portfolio._get_quantizer(8)
    assert q8 == Decimal("1.00000000")

    q2 = Portfolio._get_quantizer(2)
    assert q2 == Decimal("1.00")

    q0 = Portfolio._get_quantizer(0)
    assert q0 == Decimal("1.")


def test_quantizer_cache_hits():
    """Test that _get_quantizer caches results."""
    # Clear cache to start fresh
    Portfolio._get_quantizer.cache_clear()

    # First call - miss
    q1 = Portfolio._get_quantizer(5)
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 1
    assert info.hits == 0

    # Second call - hit
    q2 = Portfolio._get_quantizer(5)
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 1
    assert info.hits == 1

    # Check identity
    assert q1 is q2

    # Different arg - miss
    Portfolio._get_quantizer(6)
    info = Portfolio._get_quantizer.cache_info()
    assert info.misses == 2
    assert info.hits == 1
