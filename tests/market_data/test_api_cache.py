
import pytest
from unittest.mock import MagicMock, patch
from kraken_bot.market_data.api import MarketDataAPI, PairMetadata

@pytest.fixture
def market_data_api():
    config_mock = MagicMock()
    config_mock.market_data.metadata_path = None
    config_mock.market_data.ws = {"stale_tolerance_seconds": 60}

    rest_mock = MagicMock()

    md = MarketDataAPI(config=config_mock, rest_client=rest_mock)

    # Setup dummy universe
    meta = PairMetadata(
        canonical="XBTUSD",
        base="XBT",
        quote="USD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        raw_name="XXBTZUSD",
        price_decimals=1,
        volume_decimals=8,
        lot_size=1.0,
        min_order_size=0.0001,
        status="online"
    )
    md._universe_map["XBTUSD"] = meta
    md._alias_map["XBTUSD"] = meta

    return md

def test_normalize_pair_correctness(market_data_api):
    """Test that the cached method returns correct values."""
    assert market_data_api.normalize_pair("XBTUSD") == "XBTUSD"
    # Fallback to original
    assert market_data_api.normalize_pair("UNKNOWN") == "UNKNOWN"

def test_normalize_pair_caching(market_data_api):
    """Test that the method uses the cache."""
    # We can inspect the cache info
    cache_info = market_data_api._normalize_pair_cached.cache_info()
    assert cache_info.hits == 0
    assert cache_info.misses == 0

    # First call - Miss
    market_data_api.normalize_pair("XBTUSD")
    cache_info = market_data_api._normalize_pair_cached.cache_info()
    assert cache_info.hits == 0
    assert cache_info.misses == 1

    # Second call - Hit
    market_data_api.normalize_pair("XBTUSD")
    cache_info = market_data_api._normalize_pair_cached.cache_info()
    assert cache_info.hits == 1
    assert cache_info.misses == 1

def test_normalize_pair_cache_clear(market_data_api):
    """Test that refresh_universe clears the cache."""
    market_data_api.normalize_pair("XBTUSD")
    assert market_data_api._normalize_pair_cached.cache_info().misses == 1

    # Mock build_universe to return empty list so we don't need network
    with patch("kraken_bot.market_data.api.build_universe", return_value=[]):
        market_data_api.refresh_universe()

    cache_info = market_data_api._normalize_pair_cached.cache_info()
    # hits and misses are reset only if we recreated the cache object?
    # No, cache_clear() resets stats? No, it just clears content.
    # Actually, cache_clear() does NOT reset hits/misses in Python's lru_cache implementation usually.
    # Wait, let's verify if cache_clear resets stats.
    # Documentation says: "Clear the cache and the statistics." -> Yes it does.

    assert cache_info.hits == 0
    assert cache_info.misses == 0
    assert cache_info.currsize == 0

def test_instance_isolation(market_data_api):
    """Test that caches are isolated between instances."""
    config_mock = MagicMock()
    config_mock.market_data.metadata_path = None
    config_mock.market_data.ws = {}
    md2 = MarketDataAPI(config=config_mock, rest_client=MagicMock())

    market_data_api.normalize_pair("A")
    md2.normalize_pair("B")

    info1 = market_data_api._normalize_pair_cached.cache_info()
    info2 = md2._normalize_pair_cached.cache_info()

    assert info1.misses == 1
    assert info2.misses == 1

    market_data_api._normalize_pair_cached.cache_clear()

    info1 = market_data_api._normalize_pair_cached.cache_info()
    info2 = md2._normalize_pair_cached.cache_info()

    assert info1.misses == 0 # Cleared
    assert info2.misses == 1 # Not cleared
