import time

import numpy as np
import pandas as pd

from kraken_bot.market_data.models import OHLCBar


def benchmark():
    # Setup: Create a large DataFrame
    rows = 200_000
    df = pd.DataFrame(
        {
            "timestamp": np.arange(rows),
            "open": np.random.rand(rows),
            "high": np.random.rand(rows),
            "low": np.random.rand(rows),
            "close": np.random.rand(rows),
            "volume": np.random.rand(rows),
        }
    )
    df.set_index("timestamp", inplace=True)

    def run_method_1():
        start_time = time.time()
        records = df.reset_index().to_dict("records")
        for row in records:
            row["timestamp"] = int(row["timestamp"])
        bars = [OHLCBar(**row) for row in records]
        return time.time() - start_time, bars

    def run_method_2():
        start_time = time.time()
        timestamps = df.index.astype(int).tolist()
        opens = df["open"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        closes = df["close"].tolist()
        volumes = df["volume"].tolist()

        bars = [
            OHLCBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)
            for ts, o, h, l, c, v in zip(
                timestamps, opens, highs, lows, closes, volumes
            )
        ]
        return time.time() - start_time, bars

    # Warmup
    print("Warming up...")
    run_method_1()
    run_method_2()
    print("Warmup complete.")

    times_1 = []
    times_2 = []

    for i in range(5):
        t1, _ = run_method_1()
        times_1.append(t1)
        t2, _ = run_method_2()
        times_2.append(t2)
        print(f"Run {i+1}: Method 1 = {t1:.4f}s, Method 2 = {t2:.4f}s")

    mean_1 = np.mean(times_1)
    mean_2 = np.mean(times_2)
    print(f"\nAverage Method 1: {mean_1:.4f}s")
    print(f"Average Method 2: {mean_2:.4f}s")
    print(f"Improvement: {(1 - mean_2/mean_1)*100:.2f}%")


if __name__ == "__main__":
    benchmark()
