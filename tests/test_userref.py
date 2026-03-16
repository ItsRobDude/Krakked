import pytest

from kraken_bot.execution.userref import resolve_userref


def test_resolve_userref_handles_none_and_whitespace() -> None:
    assert resolve_userref(None) is None
    assert resolve_userref("") is None
    assert resolve_userref("   ") is None


def test_resolve_userref_accepts_signed_int32_strings() -> None:
    assert resolve_userref("0") == 0
    assert resolve_userref("-1") == -1
    assert resolve_userref("+42") == 42


def test_resolve_userref_rejects_out_of_range_ints() -> None:
    with pytest.raises(ValueError):
        resolve_userref(2_147_483_648)
    with pytest.raises(ValueError):
        resolve_userref("-2147483649")


def test_resolve_userref_derives_stable_int_for_tags() -> None:
    first = resolve_userref("alpha:1h")
    second = resolve_userref("alpha:1h")

    assert isinstance(first, int)
    assert first == second
    # Derived refs should be positive and safely within int32 range.
    assert first > 0
    assert first <= 2_147_483_647


def test_resolve_userref_cache_behavior() -> None:
    # Clear the cache before testing to ensure a clean slate
    resolve_userref.cache_clear()

    # First call - cache miss
    res1 = resolve_userref("cache_test_tag")
    info1 = resolve_userref.cache_info()
    assert info1.misses == 1
    assert info1.hits == 0

    # Second call - cache hit
    res2 = resolve_userref("cache_test_tag")
    info2 = resolve_userref.cache_info()
    assert res1 == res2
    assert info2.misses == 1
    assert info2.hits == 1

    # Different input - cache miss
    res3 = resolve_userref("cache_test_tag_2")
    info3 = resolve_userref.cache_info()
    assert res3 != res1
    assert info3.misses == 2
    assert info3.hits == 1
