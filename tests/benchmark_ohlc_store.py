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


def current_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_implementation(df: pd.DataFrame) -> List[OHLCBar]:
    df_reset = df.reset_index()
    timestamps = cast(Any, df_reset["timestamp"]).astype(int).tolist()
    opens = cast(Any, df_reset["open"]).tolist()
    highs = cast(Any, df_reset["high"]).tolist()
    lows = cast(Any, df_reset["low"]).tolist()
    closes = cast(Any, df_reset["close"]).tolist()
    volumes = cast(Any, df_reset["volume"]).tolist()

    return [
        OHLCBar(ts, o, h, l, c, v)
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]


def run_benchmark():
    # Setup data
    n_rows = 100_000
    df = pd.DataFrame(
        {
            "open": np.random.rand(n_rows),
            "high": np.random.rand(n_rows),
            "low": np.random.rand(n_rows),
            "close": np.random.rand(n_rows),
            "volume": np.random.rand(n_rows),
        }
    )
    df.index = np.arange(n_rows)
    df.index.name = "timestamp"

    print(f"Benchmarking with {n_rows} rows...")

    # Warmup
    current_implementation(df.head(100))
    optimized_implementation(df.head(100))

    # Measure current
    start_time = time.time()
    res_current = current_implementation(df)
    end_time = time.time()
    current_duration = end_time - start_time
    print(f"Current implementation: {current_duration:.4f} seconds")

    # Measure optimized
    start_time = time.time()
    res_optimized = optimized_implementation(df)
    end_time = time.time()
    optimized_duration = end_time - start_time
    print(f"Optimized implementation: {optimized_duration:.4f} seconds")

    # Verification
    assert len(res_current) == len(res_optimized)
    for b1, b2 in zip(res_current, res_optimized):
        assert b1 == b2
    print("Verification passed!")

    improvement = (current_duration - optimized_duration) / current_duration * 100
    print(f"Improvement: {improvement:.2f}%")


if __name__ == "__main__":
    run_benchmark()
