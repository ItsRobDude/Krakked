import time
from dataclasses import dataclass
from typing import Any, List, cast

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


def generate_data(n: int) -> pd.DataFrame:
    timestamps = np.arange(n)
    data = {
        "open": np.random.rand(n),
        "high": np.random.rand(n),
        "low": np.random.rand(n),
        "close": np.random.rand(n),
        "volume": np.random.rand(n),
    }
    df = pd.DataFrame(data, index=timestamps)
    # Cast to Any to suppress mypy error about .name on Index
    cast(Any, df.index).name = "timestamp"
    return df


def original_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    # Simulate the int conversion done in the original code
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    timestamps = cast(Any, df.index).astype(int).tolist()
    opens = cast(Any, df["open"]).tolist()
    highs = cast(Any, df["high"]).tolist()
    lows = cast(Any, df["low"]).tolist()
    closes = cast(Any, df["close"]).tolist()
    volumes = cast(Any, df["volume"]).tolist()

    return [
        OHLCBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]


def run_benchmark():
    n = 200000  # Number of rows
    df = generate_data(n)

    print(f"Benchmarking with {n} rows...")

    # Warmup
    original_conversion(df.head(100))
    optimized_conversion(df.head(100))

    # Original
    start_time = time.time()
    for _ in range(5):
        original_conversion(df)
    end_time = time.time()
    original_avg = (end_time - start_time) / 5
    print(f"Original method avg time: {original_avg:.4f}s")

    # Optimized
    start_time = time.time()
    for _ in range(5):
        optimized_conversion(df)
    end_time = time.time()
    optimized_avg = (end_time - start_time) / 5
    print(f"Optimized method avg time: {optimized_avg:.4f}s")

    improvement = (original_avg - optimized_avg) / original_avg * 100
    print(f"Improvement: {improvement:.2f}%")


if __name__ == "__main__":
    run_benchmark()
