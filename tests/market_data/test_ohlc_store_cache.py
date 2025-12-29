import shutil
import tempfile
from unittest.mock import MagicMock
import dataclasses

import pandas as pd
import pytest

from kraken_bot.config import MarketDataConfig, OHLCBar
from kraken_bot.market_data.ohlc_store import FileOHLCStore


class TestFileOHLCStoreCache:
    @pytest.fixture
    def store_and_dir(self):
        # Create a temporary directory
        temp_dir = tempfile.mkdtemp()
        config = MagicMock(spec=MarketDataConfig)
        config.ohlc_store = {"root_dir": temp_dir}

        store = FileOHLCStore(config)

        yield store, temp_dir

        # Cleanup
        store.shutdown()
        shutil.rmtree(temp_dir)

    def test_cache_population_and_hit(self, store_and_dir):
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"

        # Create some bars
        bars = [
            OHLCBar(
                timestamp=1000 + i * 60,
                open=100,
                high=110,
                low=90,
                close=105,
                volume=10,
            )
            for i in range(10)
        ]

        # Write bars (synchronously for testing via private method to ensure completion)
        store._persist_bars(pair, timeframe, bars)

        # Check if cache is populated
        key = (pair, timeframe)
        assert key in store._bar_cache
        assert len(store._bar_cache[key]) == 10

        # Read back - should come from cache (we can verify this by checking if it matches)
        fetched = store.get_bars(pair, timeframe, 5)
        assert len(fetched) == 5
        assert fetched[-1].timestamp == bars[-1].timestamp

    def test_cache_ordering(self, store_and_dir):
        """Verify that cache is sorted even if input is not."""
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"

        # Bars out of order
        bars = [
            OHLCBar(timestamp=2000, open=100, high=110, low=90, close=105, volume=10),
            OHLCBar(timestamp=1000, open=100, high=110, low=90, close=105, volume=10),
            OHLCBar(timestamp=3000, open=100, high=110, low=90, close=105, volume=10),
        ]

        store._persist_bars(pair, timeframe, bars)

        # Check cache
        key = (pair, timeframe)
        cached = store._bar_cache[key]
        timestamps = [b.timestamp for b in cached]
        assert timestamps == [1000, 2000, 3000], "Cache should be sorted by timestamp"

        # Check get_bars returns sorted tail
        fetched = store.get_bars(pair, timeframe, 3)
        fetched_ts = [b.timestamp for b in fetched]
        assert fetched_ts == [1000, 2000, 3000]

    def test_cache_fallback_when_lookback_exceeds_cache(self, store_and_dir):
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"

        # Set a small cache size for testing
        store._cache_size = 5

        bars = [
            OHLCBar(
                timestamp=1000 + i, open=100, high=110, low=90, close=105, volume=10
            )
            for i in range(10)
        ]

        store._persist_bars(pair, timeframe, bars)

        # Cache should only have last 5
        key = (pair, timeframe)
        assert len(store._bar_cache[key]) == 5
        assert store._bar_cache[key][0].timestamp == 1005

        # Request 8 bars (should trigger disk read logic, though we might not be able to spy on it easily without mocking)
        # But correctness is what matters: we should get 8 bars.
        fetched = store.get_bars(pair, timeframe, 8)
        assert len(fetched) == 8
        assert fetched[0].timestamp == 1002
        assert fetched[-1].timestamp == 1009

    def test_get_bars_since_cache_hit(self, store_and_dir):
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"

        bars = [
            OHLCBar(
                timestamp=1000 + i * 60,
                open=100,
                high=110,
                low=90,
                close=105,
                volume=10,
            )
            for i in range(20)
        ]
        store._persist_bars(pair, timeframe, bars)

        # Case 1: since_ts is inside cache coverage
        # Cache has all 20 bars. since_ts = last bar - 2 bars
        since_ts = bars[-3].timestamp
        fetched = store.get_bars_since(pair, timeframe, since_ts)
        assert len(fetched) == 3
        assert fetched[0].timestamp == since_ts

    def test_get_bars_since_cache_miss(self, store_and_dir):
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"
        store._cache_size = 5

        bars = [
            OHLCBar(
                timestamp=1000 + i * 60,
                open=100,
                high=110,
                low=90,
                close=105,
                volume=10,
            )
            for i in range(10)
        ]
        store._persist_bars(pair, timeframe, bars)

        # Cache has last 5 (indices 5-9)
        # Request since index 2 (timestamp 1120) -> Partial miss (need 2,3,4 from disk)
        since_ts = bars[2].timestamp
        fetched = store.get_bars_since(pair, timeframe, since_ts)
        assert len(fetched) == 8
        assert fetched[0].timestamp == since_ts

    def test_cache_update_error_handling(self, store_and_dir):
        """Ensure get_bars doesn't crash if cache update fails/is missing key."""
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"
        key = (pair, timeframe)

        # Simulate a state where file exists but cache is empty (e.g. restart)
        # Write directly to file bypassing store methods
        p = store._get_file_path(pair, timeframe)
        p.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [
                {
                    "timestamp": 1000,
                    "open": 100,
                    "high": 110,
                    "low": 90,
                    "close": 105,
                    "volume": 10,
                }
            ]
        )
        df = df.set_index("timestamp")
        df.to_parquet(p)

        # Ensure cache is empty
        if key in store._bar_cache:
            del store._bar_cache[key]

        # get_bars should still work by populating cache on fly
        fetched = store.get_bars(pair, timeframe, 1)
        assert len(fetched) == 1
        assert fetched[0].timestamp == 1000
        assert key in store._bar_cache  # Should be populated now

    def test_get_bars_zero_lookback(self, store_and_dir):
        """Verify that lookback=0 returns empty list, not the whole cache."""
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"

        bars = [
            OHLCBar(
                timestamp=1000 + i, open=100, high=110, low=90, close=105, volume=10
            )
            for i in range(10)
        ]
        store._persist_bars(pair, timeframe, bars)

        # Cache is populated with 10 items
        key = (pair, timeframe)
        assert len(store._bar_cache[key]) == 10

        # Request 0 bars
        fetched = store.get_bars(pair, timeframe, 0)
        assert fetched == [], f"Expected empty list, got {len(fetched)} items"

        # Request negative bars (should also be empty)
        fetched_neg = store.get_bars(pair, timeframe, -5)
        assert fetched_neg == []

    def test_cache_immutability(self, store_and_dir):
        """Verify that mutating returned bars is not possible (frozen objects)."""
        store, _ = store_and_dir
        pair = "XBTUSD"
        timeframe = "1m"

        bars = [
            OHLCBar(timestamp=1000, open=100, high=110, low=90, close=105, volume=10)
        ]
        store._persist_bars(pair, timeframe, bars)

        # Fetch from cache
        fetched1 = store.get_bars(pair, timeframe, 1)
        assert fetched1[0].open == 100

        # Attempt to mutate the returned object should fail
        with pytest.raises(dataclasses.FrozenInstanceError):
            fetched1[0].open = 9999

        # Verify value remains unchanged
        assert fetched1[0].open == 100

        # Verify subsequent fetches return the same immutable object
        fetched2 = store.get_bars(pair, timeframe, 1)
        assert fetched2[0] is fetched1[0]  # Now they ARE the same object, which is fine because they are immutable
