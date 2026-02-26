import timeit

import numpy as np
import pandas as pd

from kraken_bot.market_data.models import OHLCBar


def setup_dataframe(n_rows=100000):
    data = {
        "open": np.random.rand(n_rows),
        "high": np.random.rand(n_rows),
        "low": np.random.rand(n_rows),
        "close": np.random.rand(n_rows),
        "volume": np.random.rand(n_rows),
    }
    index = pd.RangeIndex(
        start=1600000000, stop=1600000000 + n_rows * 60, step=60, name="timestamp"
    )
    return pd.DataFrame(data, index=index)


def current_implementation(df):
    records = df.reset_index().to_dict("records")
    # Simulate the modification done in the original code
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_implementation(df):
    # Ensure index is accessible as a column or separate list
    timestamps = df.index.astype(int).tolist()
    # Extract columns
    opens = df["open"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    volumes = df["volume"].tolist()

    return [
        OHLCBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]


def verify_correctness(df):
    result1 = current_implementation(df)
    result2 = optimized_implementation(df)

    if len(result1) != len(result2):
        print("Length mismatch!")
        return False

    for i in range(len(result1)):
        if result1[i] != result2[i]:
            print(f"Mismatch at index {i}: {result1[i]} != {result2[i]}")
            return False

    print("Verification passed: Both implementations return identical results.")
    return True


if __name__ == "__main__":
    df = setup_dataframe(200000)
    print(f"Benchmarking with {len(df)} rows...")

    if verify_correctness(df.head(100)):  # Verify on a subset first
        # Benchmark current
        t_current = timeit.timeit(lambda: current_implementation(df), number=5)
        print(
            f"Current implementation (5 runs): {t_current:.4f}s (avg: {t_current/5:.4f}s)"
        )

        # Benchmark optimized
        t_optimized = timeit.timeit(lambda: optimized_implementation(df), number=5)
        print(
            f"Optimized implementation (5 runs): {t_optimized:.4f}s (avg: {t_optimized/5:.4f}s)"
        )

        speedup = t_current / t_optimized
        print(f"Speedup: {speedup:.2f}x")
