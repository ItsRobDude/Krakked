import time

import numpy as np
import pandas as pd

from kraken_bot.market_data.models import OHLCBar


def run_benchmark():
    N = 100_000
    print(f"Generating {N} rows of sample data...")

    df = pd.DataFrame(
        {
            "open": np.random.rand(N) * 100,
            "high": np.random.rand(N) * 100,
            "low": np.random.rand(N) * 100,
            "close": np.random.rand(N) * 100,
            "volume": np.random.rand(N) * 1000,
        },
        index=pd.to_datetime(np.arange(N), unit="s"),
    )
    df.index.name = "timestamp"

    # Warmup
    print("Warming up...")
    df.head().reset_index().to_dict("records")

    # Baseline: to_dict("records")
    start_time = time.perf_counter()
    records = df.reset_index().to_dict("records")
    # Simulate the integer timestamp conversion that happens in the loop
    for row in records:
        ts = row["timestamp"]
        if hasattr(ts, "timestamp"):
            row["timestamp"] = int(ts.timestamp())
        else:
            row["timestamp"] = int(ts)

    _ = [OHLCBar(**row) for row in records]
    baseline_time = time.perf_counter() - start_time
    print(f"Baseline (to_dict): {baseline_time:.4f}s")

    print("\n--- Retrying with Int Index (Real Scenario) ---")
    # FileOHLCStore likely stores timestamps as Ints because OHLCBar defines timestamp as int.

    df_int = df.copy()
    df_int.index = df_int.index.astype("int64") // 10**9  # seconds
    df_int.index.name = "timestamp"

    start_time = time.perf_counter()
    records = df_int.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    _ = [OHLCBar(**row) for row in records]
    baseline_time = time.perf_counter() - start_time
    print(f"Baseline (to_dict) with Int Index: {baseline_time:.4f}s")

    # Optimization
    start_time = time.perf_counter()

    timestamps = df_int.index.tolist()  # fast if already int

    opens = df_int["open"].tolist()
    highs = df_int["high"].tolist()
    lows = df_int["low"].tolist()
    closes = df_int["close"].tolist()
    volumes = df_int["volume"].tolist()

    _ = [
        OHLCBar(t, o, h, l, c, v)
        for t, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]

    opt_time = time.perf_counter() - start_time
    print(f"Optimized (zip): {opt_time:.4f}s")

    print(f"Improvement: {baseline_time / opt_time:.2f}x")


if __name__ == "__main__":
    run_benchmark()
