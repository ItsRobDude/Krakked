
import pytest
from kraken_bot.execution.userref import resolve_userref

def test_resolve_userref_caches_results() -> None:
    # Clear cache to ensure clean state
    resolve_userref.cache_clear()

    # First call - should be a miss
    res1 = resolve_userref("cache_test:1h")
    info1 = resolve_userref.cache_info()
    assert info1.hits == 0
    assert info1.misses == 1
    assert info1.currsize == 1

    # Second call - should be a hit
    res2 = resolve_userref("cache_test:1h")
    info2 = resolve_userref.cache_info()
    assert res1 == res2
    assert info2.hits == 1
    assert info2.misses == 1
    assert info2.currsize == 1

    # Different input - should be a miss
    res3 = resolve_userref("cache_test:4h")
    info3 = resolve_userref.cache_info()
    assert res3 != res1
    assert info3.hits == 1
    assert info3.misses == 2
    assert info3.currsize == 2

def test_resolve_userref_cache_clear() -> None:
    resolve_userref.cache_clear()
    resolve_userref("clear_test:1h")
    assert resolve_userref.cache_info().currsize == 1

    resolve_userref.cache_clear()
    assert resolve_userref.cache_info().currsize == 0
