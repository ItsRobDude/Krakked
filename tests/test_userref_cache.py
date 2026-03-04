from kraken_bot.execution.userref import resolve_userref


def test_resolve_userref_cache():
    # Clear cache if any
    if hasattr(resolve_userref, "cache_clear"):
        resolve_userref.cache_clear()

    res1 = resolve_userref("alpha:1h")
    res2 = resolve_userref("alpha:1h")

    assert res1 == res2
    if hasattr(resolve_userref, "cache_info"):
        assert resolve_userref.cache_info().hits == 1


def test_resolve_userref_cache_miss():
    if hasattr(resolve_userref, "cache_clear"):
        resolve_userref.cache_clear()

    res1 = resolve_userref("alpha:1h")
    res2 = resolve_userref("beta:1h")

    assert res1 != res2
    if hasattr(resolve_userref, "cache_info"):
        assert resolve_userref.cache_info().misses == 2
