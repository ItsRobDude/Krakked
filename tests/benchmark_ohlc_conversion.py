import time
import pandas as pd
import sys
import os

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from kraken_bot.market_data.models import OHLCBar

def benchmark_conversion():
    n_rows = 10000
    n_runs = 20

    # Create a dummy DataFrame
    data = {
        "timestamp": range(1600000000, 1600000000 + n_rows * 60, 60),
        "open": [100.0] * n_rows,
        "high": [105.0] * n_rows,
        "low": [95.0] * n_rows,
        "close": [102.0] * n_rows,
        "volume": [1.5] * n_rows,
    }
    df = pd.DataFrame(data).set_index("timestamp")

    print(f"Benchmarking conversion of {n_rows} rows over {n_runs} runs...")

    # Method 1: Old approach (to_dict) - Simulated
    start_time = time.time()
    for _ in range(n_runs):
        records = df.reset_index().to_dict("records")
        for row in records:
            row["timestamp"] = int(row["timestamp"])
        _ = [OHLCBar(**row) for row in records]
    end_time = time.time()
    avg_time_1 = (end_time - start_time) / n_runs
    print(f"Old Method (to_dict): {avg_time_1:.6f}s per run")

    # Method 2: New approach (zip) - Implemented in FileOHLCStore._df_to_bars
    start_time = time.time()
    for _ in range(n_runs):
        timestamps = df.index.astype(int).tolist()
        opens = df["open"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        closes = df["close"].tolist()
        volumes = df["volume"].tolist()

        _ = [
            OHLCBar(ts, o, h, l, c, v)
            for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
        ]
    end_time = time.time()
    avg_time_2 = (end_time - start_time) / n_runs
    print(f"New Method (zip): {avg_time_2:.6f}s per run")

    improvement = (avg_time_1 - avg_time_2) / avg_time_1 * 100
    print(f"Improvement: {improvement:.2f}%")

if __name__ == "__main__":
    benchmark_conversion()
