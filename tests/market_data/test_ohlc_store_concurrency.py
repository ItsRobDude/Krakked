
import pytest
import threading
import time
from unittest.mock import MagicMock
from kraken_bot.market_data.ohlc_store import FileOHLCStore
from kraken_bot.config import MarketDataConfig
from kraken_bot.market_data.models import OHLCBar

@pytest.fixture
def mock_ohlc_store_config(tmp_path):
    config = MarketDataConfig(
        ws={},
        backfill_timeframes=[],
        ws_timeframes=[],
        ohlc_store={"root_dir": str(tmp_path), "backend": "parquet"}
    )
    return config

def test_get_bars_optimistic_locking(mock_ohlc_store_config):
    """
    Verifies that get_bars returns data from cache WITHOUT acquiring the file lock,
    but acquires it on cache miss.
    """
    store = FileOHLCStore(mock_ohlc_store_config)

    # Pre-populate cache manually
    pair = "XBTUSD"
    timeframe = "1m"
    bars = [
        OHLCBar(timestamp=1000 + i * 60, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
        for i in range(10)
    ]
    store._bar_cache[(pair, timeframe)] = bars

    # Spy on the lock
    lock_spy = MagicMock(wraps=store._file_lock)
    store._file_lock = lock_spy

    # Act: Get bars (should hit cache)
    result = store.get_bars(pair, timeframe, 5)

    # Assert: Correct data returned
    assert len(result) == 5
    assert result[-1].timestamp == bars[-1].timestamp

    # Assert: Lock was NOT acquired (because optimized path bypasses it)
    # The 'wraps' object routes calls to real lock, so we check call count.
    # 'acquire' should not be called in the optimized path.
    # Wait, RLock.__enter__ calls acquire.
    assert lock_spy.__enter__.call_count == 0

    # Act: Get bars with cache miss (lookback too large)
    _ = store.get_bars(pair, timeframe, 20)

    # Assert: Lock WAS acquired (fallback path)
    assert lock_spy.__enter__.call_count > 0

def test_get_bars_since_optimistic_locking(mock_ohlc_store_config):
    """
    Verifies that get_bars_since returns data from cache WITHOUT acquiring the file lock.
    """
    store = FileOHLCStore(mock_ohlc_store_config)

    pair = "XBTUSD"
    timeframe = "1m"
    bars = [
        OHLCBar(timestamp=1000 + i * 60, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
        for i in range(10)
    ]
    store._bar_cache[(pair, timeframe)] = bars

    lock_spy = MagicMock(wraps=store._file_lock)
    store._file_lock = lock_spy

    # Act: Hit cache
    result = store.get_bars_since(pair, timeframe, 1000)

    assert len(result) == 10
    assert lock_spy.__enter__.call_count == 0

    # Act: Miss cache (timestamp too early)
    result = store.get_bars_since(pair, timeframe, 0)
    assert lock_spy.__enter__.call_count > 0

def test_concurrency_correctness(mock_ohlc_store_config):
    """
    Verifies that optimistic reads return valid data even while a writer is updating the cache.
    """
    store = FileOHLCStore(mock_ohlc_store_config)
    pair = "XBTUSD"
    timeframe = "1m"

    # Initial state
    initial_bars = [OHLCBar(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(10)]
    store._bar_cache[(pair, timeframe)] = initial_bars

    stop_event = threading.Event()

    def writer():
        i = 0
        while not stop_event.is_set():
            i += 1
            # Simulate atomic update of cache (replacing the list)
            new_bars = [OHLCBar(timestamp=j + i, open=1, high=1, low=1, close=1, volume=1) for j in range(10)]
            store._bar_cache[(pair, timeframe)] = new_bars
            time.sleep(0.001)

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()

    try:
        # Reader should always get a consistent list of 10 bars
        for _ in range(100):
            result = store.get_bars(pair, timeframe, 10)
            assert len(result) == 10
            # Timestamps should be contiguous
            ts = [b.timestamp for b in result]
            assert ts == sorted(ts)
            time.sleep(0.001)
    finally:
        stop_event.set()
        writer_thread.join()
