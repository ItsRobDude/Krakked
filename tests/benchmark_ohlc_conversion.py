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


def generate_dummy_data(n_rows=100_000):
    timestamps = np.arange(n_rows)
    data = {
        "timestamp": timestamps,
        "open": np.random.rand(n_rows),
        "high": np.random.rand(n_rows),
        "low": np.random.rand(n_rows),
        "close": np.random.rand(n_rows),
        "volume": np.random.rand(n_rows),
    }
    df = pd.DataFrame(data)
    df = df.set_index("timestamp")
    return df


def original_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    # Vectorized approach: extract columns to lists and zip
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


def benchmark():
    # Use a large enough dataset to make the difference significant
    # 100,000 rows simulates reading a reasonable history
    n_rows = 100_000
    df = generate_dummy_data(n_rows=n_rows)

    print(f"Benchmarking conversion of {n_rows} rows...")

    # Warmup
    original_conversion(df)
    optimized_conversion(df)

    # Benchmark Original
    start_time = time.time()
    iterations = 5
    for _ in range(iterations):
        _ = original_conversion(df)
    end_time = time.time()
    avg_original = (end_time - start_time) / iterations
    print(f"Original average time: {avg_original:.4f}s")

    # Benchmark Optimized
    start_time = time.time()
    for _ in range(iterations):
        _ = optimized_conversion(df)
    end_time = time.time()
    avg_optimized = (end_time - start_time) / iterations
    print(f"Optimized average time: {avg_optimized:.4f}s")

    improvement = (avg_original - avg_optimized) / avg_original * 100
    print(f"Improvement: {improvement:.2f}%")


if __name__ == "__main__":
    benchmark()
