import time
from typing import List

import numpy as np
import pandas as pd

from kraken_bot.market_data.models import OHLCBar


def current_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    timestamps = df.index.astype(int).tolist()
    opens = df["open"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    volumes = df["volume"].tolist()

    return [
        OHLCBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]


def run_benchmark():
    N = 100_000
    print(f"Generating {N} rows of data...")
    df = pd.DataFrame(
        {
            "open": np.random.rand(N),
            "high": np.random.rand(N),
            "low": np.random.rand(N),
            "close": np.random.rand(N),
            "volume": np.random.rand(N),
        }
    )
    # start, stop, step for RangeIndex
    df.index = pd.RangeIndex(start=1600000000, stop=1600000000 + N * 60, step=60)
    df.index.name = "timestamp"

    # Warmup
    current_implementation(df.head(100))
    optimized_implementation(df.head(100))

    runs = 5
    times_current = []
    times_optimized = []

    print(f"Running {runs} passes...")
    for _ in range(runs):
        start = time.perf_counter()
        res1 = current_implementation(df)
        times_current.append(time.perf_counter() - start)

        start = time.perf_counter()
        res2 = optimized_implementation(df)
        times_optimized.append(time.perf_counter() - start)

    median_current = np.median(times_current)
    median_optimized = np.median(times_optimized)

    print(f"Current (median): {median_current:.4f}s")
    print(f"Optimized (median): {median_optimized:.4f}s")
    print(f"Speedup: {median_current / median_optimized:.2f}x")

    # Verification
    assert len(res1) == len(res2)
    assert res1[0] == res2[0]
    assert res1[-1] == res2[-1]
    print("Verification successful!")


if __name__ == "__main__":
    run_benchmark()
