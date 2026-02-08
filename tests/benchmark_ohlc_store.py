
import shutil
import tempfile
import time
from pathlib import Path
from typing import List

from kraken_bot.config import MarketDataConfig, OHLCBar
from kraken_bot.market_data.ohlc_store import FileOHLCStore


def create_dummy_bars(count: int, start_ts: int = 1600000000) -> List[OHLCBar]:
    return [
        OHLCBar(
            timestamp=start_ts + i * 60,
            open=100.0 + i * 0.1,
            high=101.0 + i * 0.1,
            low=99.0 + i * 0.1,
            close=100.5 + i * 0.1,
            volume=1000.0 + i
        )
        for i in range(count)
    ]


def run_benchmark():
    # Setup temporary directory
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
        N = 100_000

        print(f"Generating {N} bars...")
        bars = create_dummy_bars(N)

        # Write bars to store (synchronously for benchmark setup)
        # We access private method to avoid async wait complexity in setup
        store._persist_bars(pair, timeframe, bars)

        # Clear cache to force disk read and conversion
        store._bar_cache.clear()

        print(f"Benchmarking get_bars({N})...")

        times = []
        for i in range(10):
            # Clear cache each time to measure conversion cost
            store._bar_cache.clear()

            start = time.time()
            _ = store.get_bars(pair, timeframe, lookback=N)
            end = time.time()
            times.append(end - start)

        avg_time = sum(times) / len(times)
        median_time = sorted(times)[len(times) // 2]

        print(f"Average time: {avg_time:.4f}s")
        print(f"Median time: {median_time:.4f}s")

        store.shutdown()

    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    run_benchmark()
