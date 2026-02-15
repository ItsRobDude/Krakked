import time
import shutil
import tempfile
import pandas as pd
import numpy as np
from pathlib import Path
from kraken_bot.config import MarketDataConfig
from kraken_bot.market_data.ohlc_store import FileOHLCStore

def benchmark():
    temp_dir = tempfile.mkdtemp()
    try:
        # Mock config object since MarketDataConfig might be a Pydantic model or similar
        # But here we import it, so let's check if we can instantiate it easily.
        # The code uses config.ohlc_store which is a dict.

        class MockConfig:
            ohlc_store = {"root_dir": temp_dir}

        store = FileOHLCStore(MockConfig())

        pair = "XXBTZUSD"
        timeframe = "1m"

        # Generate 100k rows
        n_rows = 100000
        df = pd.DataFrame({
            "timestamp": np.arange(1000000000, 1000000000 + n_rows * 60, 60),
            "open": np.random.rand(n_rows) * 1000,
            "high": np.random.rand(n_rows) * 1000,
            "low": np.random.rand(n_rows) * 1000,
            "close": np.random.rand(n_rows) * 1000,
            "volume": np.random.rand(n_rows) * 10
        })
        df = df.set_index("timestamp")

        # Write directly to parquet to simulate existing data
        file_path = store._get_file_path(pair, timeframe)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(file_path)

        # Warmup (optional, but good to ensure OS caching isn't the main factor changing)
        # But here we want to measure the Python overhead.

        times = []
        for _ in range(5):
            start_time = time.time()
            bars = store.get_bars(pair, timeframe, n_rows)
            end_time = time.time()
            times.append(end_time - start_time)

        print(f"Loaded {len(bars)} bars. Median time: {np.median(times):.4f}s")
        print(f"All times: {times}")

        store.shutdown()

    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    benchmark()
