import shutil
import tempfile
import time

from kraken_bot.config import MarketDataConfig, OHLCBar
from kraken_bot.market_data.ohlc_store import FileOHLCStore


def generate_bars(count: int, start_ts: int = 1600000000) -> list[OHLCBar]:
    return [
        OHLCBar(
            timestamp=start_ts + i * 60,
            open=100.0 + i * 0.01,
            high=101.0 + i * 0.01,
            low=99.0 + i * 0.01,
            close=100.5 + i * 0.01,
            volume=1000.0,
        )
        for i in range(count)
    ]


def run_benchmark():
    # Setup
    temp_dir = tempfile.mkdtemp()
    try:
        config = MarketDataConfig(
            ws={},
            ohlc_store={"root_dir": temp_dir, "backend": "parquet"},
            backfill_timeframes=[],
            ws_timeframes=[],
        )
        store = FileOHLCStore(config)

        # Generate data
        num_bars = 100000
        bars = generate_bars(num_bars)
        pair = "XBTUSD"
        timeframe = "1m"

        print(f"Generating {num_bars} bars...")
        store.append_bars(pair, timeframe, bars)

        # Wait for write
        store._write_queue.join()

        # Benchmark get_bars
        # Request more than cache size (1000) to force disk read path
        lookback = num_bars

        start_time = time.time()
        # Run multiple times to get a stable measurement
        iterations = 5
        for _ in range(iterations):
            _ = store.get_bars(pair, timeframe, lookback)
        end_time = time.time()

        avg_time = (end_time - start_time) / iterations
        print(
            f"Average time for get_bars with {lookback} items: {avg_time:.4f} seconds"
        )

        store.shutdown()

    finally:
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    run_benchmark()
