from kraken_bot.execution.userref import _DERIVED_SEEN, resolve_userref


def test_resolve_userref_cache_hit():
    # Clear the cache and seen dictionary
    resolve_userref.cache_clear()
    _DERIVED_SEEN.clear()

    # First call - cache miss
    val1 = resolve_userref("alpha:1h")
    assert resolve_userref.cache_info().hits == 0
    assert resolve_userref.cache_info().misses == 1

    # Second call - cache hit
    val2 = resolve_userref("alpha:1h")
    assert resolve_userref.cache_info().hits == 1
    assert resolve_userref.cache_info().misses == 1

    assert val1 == val2
