import time
import pandas as pd
import numpy as np
from kraken_bot.market_data.models import OHLCBar
from typing import List, Any, cast


def _df_to_bars_legacy(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def _df_to_bars_optimized(df: pd.DataFrame) -> List[OHLCBar]:
    # Ensure index is treated as timestamp column if needed, but here we assume it's the index
    # We need to access columns by name.
    # df.index should be timestamp.

    timestamps = cast(Any, df.index).astype(int).tolist()
    # Using 'get' or direct access depending on if columns exist.
    # Assuming columns are correct as per FileOHLCStore
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
    # Setup
    N = 100_000
    df = pd.DataFrame({
        "open": np.random.rand(N),
        "high": np.random.rand(N),
        "low": np.random.rand(N),
        "close": np.random.rand(N),
        "volume": np.random.rand(N),
    }, index=pd.Index(np.arange(N), name="timestamp"))

    # Warmup
    _df_to_bars_legacy(df.head(100))
    _df_to_bars_optimized(df.head(100))

    # Benchmark Legacy
    start_time = time.time()
    res_legacy = _df_to_bars_legacy(df)
    end_time = time.time()
    legacy_duration = end_time - start_time
    print(f"Legacy implementation: {legacy_duration:.4f} seconds")

    # Benchmark Optimized
    start_time = time.time()
    res_opt = _df_to_bars_optimized(df)
    end_time = time.time()
    opt_duration = end_time - start_time
    print(f"Optimized implementation: {opt_duration:.4f} seconds")

    # Verify Correctness
    assert len(res_legacy) == len(res_opt)
    assert res_legacy == res_opt
    print("Correctness verified!")

    improvement = (legacy_duration - opt_duration) / legacy_duration * 100
    print(f"Improvement: {improvement:.2f}%")


if __name__ == "__main__":
    run_benchmark()
