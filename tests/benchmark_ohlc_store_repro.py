
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


def current_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    # Ensure index is part of the columns for easier extraction if it's the timestamp
    df_reset = df.reset_index()

    # Extract columns to python lists
    timestamps = df_reset["timestamp"].astype(int).tolist()
    opens = df_reset["open"].tolist()
    highs = df_reset["high"].tolist()
    lows = df_reset["low"].tolist()
    closes = df_reset["close"].tolist()
    volumes = df_reset["volume"].tolist()

    # Zip and list comprehension
    return [
        OHLCBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]


def main():
    # Setup - Create a large DataFrame
    n_rows = 100000
    df = pd.DataFrame({
        "timestamp": np.arange(n_rows),
        "open": np.random.rand(n_rows),
        "high": np.random.rand(n_rows),
        "low": np.random.rand(n_rows),
        "close": np.random.rand(n_rows),
        "volume": np.random.rand(n_rows)
    })
    df = df.set_index("timestamp")

    print(f"Benchmarking with {n_rows} rows...")

    # Measure current implementation
    start_time = time.time()
    res_current = current_implementation(df)
    end_time = time.time()
    time_current = end_time - start_time
    print(f"Current implementation: {time_current:.4f} seconds")

    # Measure optimized implementation
    start_time = time.time()
    res_optimized = optimized_implementation(df)
    end_time = time.time()
    time_optimized = end_time - start_time
    print(f"Optimized implementation: {time_optimized:.4f} seconds")

    # Verify correctness
    assert len(res_current) == len(res_optimized)
    for i in range(len(res_current)):
        assert res_current[i] == res_optimized[i]

    print("Verification passed!")
    print(f"Speedup: {time_current / time_optimized:.2f}x")


if __name__ == "__main__":
    main()
