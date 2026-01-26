import time
import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class OHLCBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def generate_df(n=1000):
    timestamps = np.arange(n)
    data = np.random.rand(n, 5)
    df = pd.DataFrame(data, columns=['open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = timestamps
    df = df.set_index('timestamp')
    return df


def baseline_conversion(df):
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def optimized_conversion(df):
    # Avoid to_dict("records") which is slow
    # Extract columns and zip them
    # Ensure timestamp is int

    # reset index to get timestamp as column
    df_reset = df.reset_index()

    timestamps = df_reset['timestamp'].astype(int).tolist()
    opens = df_reset['open'].tolist()
    highs = df_reset['high'].tolist()
    lows = df_reset['low'].tolist()
    closes = df_reset['close'].tolist()
    volumes = df_reset['volume'].tolist()

    return [
        OHLCBar(timestamp=t, open=o, high=h, low=l, close=c, volume=v)
        for t, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
    ]


def optimized_conversion_v2(df):
    # Using itertuples might be faster than zip for many columns, or slower.
    # But zip of lists is usually very fast in Python for creating objects.
    # Let's stick to zip approach for comparison first.
    return optimized_conversion(df)


def main():
    N = 50000  # 50k bars
    runs = 10

    df = generate_df(N)

    # Warmup
    baseline_conversion(df.head(100))
    optimized_conversion(df.head(100))

    start = time.time()
    for _ in range(runs):
        _ = baseline_conversion(df)
    end = time.time()
    avg_baseline = (end - start) / runs
    print(f"Baseline (avg of {runs}): {avg_baseline:.4f}s")

    start = time.time()
    for _ in range(runs):
        _ = optimized_conversion(df)
    end = time.time()
    avg_optimized = (end - start) / runs
    print(f"Optimized (avg of {runs}): {avg_optimized:.4f}s")

    improvement = (avg_baseline - avg_optimized) / avg_baseline * 100
    print(f"Improvement: {improvement:.2f}%")


if __name__ == "__main__":
    main()
