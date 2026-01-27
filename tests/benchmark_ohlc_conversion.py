
import time
from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd


@dataclass
class OHLCBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def legacy_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    # Ensure index is accessible as column if needed, or just access it directly
    # This simulates the optimized approach I intend to implement
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
    # Setup
    N = 10000
    df = pd.DataFrame({
        "open": np.random.rand(N),
        "high": np.random.rand(N),
        "low": np.random.rand(N),
        "close": np.random.rand(N),
        "volume": np.random.rand(N)
    })
    df.index = np.arange(1600000000, 1600000000 + N)
    df.index.name = "timestamp"

    # Warmup
    legacy_conversion(df.head(100))
    optimized_conversion(df.head(100))

    # Measure Legacy
    start_time = time.time()
    for _ in range(50):
        _ = legacy_conversion(df)
    legacy_duration = time.time() - start_time
    print(f"Legacy implementation (50 runs): {legacy_duration:.4f}s")

    # Measure Optimized
    start_time = time.time()
    for _ in range(50):
        _ = optimized_conversion(df)
    optimized_duration = time.time() - start_time
    print(f"Optimized implementation (50 runs): {optimized_duration:.4f}s")

    speedup = legacy_duration / optimized_duration
    print(f"Speedup: {speedup:.2f}x")


if __name__ == "__main__":
    run_benchmark()
