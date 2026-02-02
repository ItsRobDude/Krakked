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


def generate_data(n=100000):
    timestamps = np.arange(1600000000, 1600000000 + n * 60, 60)
    data = {
        "timestamp": timestamps,
        "open": np.random.rand(n) * 100,
        "high": np.random.rand(n) * 100,
        "low": np.random.rand(n) * 100,
        "close": np.random.rand(n) * 100,
        "volume": np.random.rand(n) * 10,
    }
    df = pd.DataFrame(data).set_index("timestamp")
    return df


def current_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    # Ensure index is treated as timestamp column if it's the index
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
    df = generate_data(100000)
    print(f"Benchmarking with {len(df)} rows...")

    # Warmup
    current_implementation(df.head(1000))
    optimized_implementation(df.head(1000))

    start = time.time()
    for _ in range(5):
        current_implementation(df)
    end = time.time()
    avg_current = (end - start) / 5
    print(f"Current implementation avg: {avg_current:.4f}s")

    start = time.time()
    for _ in range(5):
        optimized_implementation(df)
    end = time.time()
    avg_opt = (end - start) / 5
    print(f"Optimized implementation avg: {avg_opt:.4f}s")

    improvement = (avg_current - avg_opt) / avg_current * 100
    print(f"Improvement: {improvement:.2f}%")


if __name__ == "__main__":
    run_benchmark()
