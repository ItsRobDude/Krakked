import random
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from kraken_bot.config import MarketDataConfig, OHLCBar
from kraken_bot.market_data.ohlc_store import FileOHLCStore


def benchmark_ohlc_store():
    # Setup
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        config = MarketDataConfig(
            ws={},
            ohlc_store={"root_dir": str(tmp_dir), "backend": "parquet"},
            backfill_timeframes=[],
            ws_timeframes=[],
        )
        store = FileOHLCStore(config)

        pair = "XBTUSD"
        timeframe = "1m"
        num_bars = 100_000

        print(f"Generating {num_bars} bars...")
        bars = []
        base_ts = 1672531200
        for i in range(num_bars):
            bars.append(
                OHLCBar(
                    timestamp=base_ts + i * 60,
                    open=100.0 + random.random(),
                    high=105.0 + random.random(),
                    low=95.0 + random.random(),
                    close=102.0 + random.random(),
                    volume=1000.0 + random.random(),
                )
            )

        print("Appending bars to store...")
        store.append_bars(pair, timeframe, bars)

        # Wait for write to complete
        store._write_queue.join()

        durations = []
        for i in range(5):
            # Force a reload from disk by clearing cache
            store._bar_cache.clear()

            # print(f"Run {i+1}...")
            start_time = time.time()

            # Retrieve all bars
            retrieved_bars = store.get_bars(pair, timeframe, lookback=num_bars)

            end_time = time.time()
            duration = end_time - start_time
            durations.append(duration)

            # Verification
            assert len(retrieved_bars) == num_bars

        median_duration = statistics.median(durations)
        print(
            f"Retrieved {num_bars} bars. Median duration over 5 runs: {median_duration:.4f} seconds"
        )
        print(f"Raw durations: {durations}")

        store.shutdown()
    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    benchmark_ohlc_store()
