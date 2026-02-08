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


def legacy_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    # Reset index to make timestamp a column, but avoid to_dict overhead
    # We assume the index is the timestamp

    # Vectorized extraction is much faster
    timestamps = cast(Any, df.index).astype(int).tolist()
    opens = cast(Any, df["open"]).tolist()
    highs = cast(Any, df["high"]).tolist()
    lows = cast(Any, df["low"]).tolist()
    closes = cast(Any, df["close"]).tolist()
    volumes = cast(Any, df["volume"]).tolist()

    return [
        OHLCBar(ts, o, h, l, c, v)
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]


def run_benchmark():
    # Setup: Create a large DataFrame
    N = 100_000
    df = pd.DataFrame(
        {
            "open": np.random.rand(N),
            "high": np.random.rand(N),
            "low": np.random.rand(N),
            "close": np.random.rand(N),
            "volume": np.random.rand(N),
        },
        index=pd.Index(np.arange(N), name="timestamp"),
    )

    print(f"Benchmarking conversion of {N} rows...")

    # Warmup
    legacy_conversion(df.head(100))
    optimized_conversion(df.head(100))

    # Measure Legacy
    start = time.time()
    for _ in range(5):
        _ = legacy_conversion(df)
    end = time.time()
    legacy_avg = (end - start) / 5
    print(f"Legacy average time: {legacy_avg:.4f}s")

    # Measure Optimized
    start = time.time()
    for _ in range(5):
        _ = optimized_conversion(df)
    end = time.time()
    opt_avg = (end - start) / 5
    print(f"Optimized average time: {opt_avg:.4f}s")

    improvement = (legacy_avg - opt_avg) / legacy_avg * 100
    print(f"Improvement: {improvement:.2f}%")


if __name__ == "__main__":
    run_benchmark()
