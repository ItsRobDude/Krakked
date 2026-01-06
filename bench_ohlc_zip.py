
import time
import pandas as pd
from dataclasses import dataclass
from typing import List

@dataclass
class OHLCBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

def get_bars_original(df: pd.DataFrame) -> List[OHLCBar]:
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]

def get_bars_itertuples(df: pd.DataFrame) -> List[OHLCBar]:
    return [
        OHLCBar(
            timestamp=int(row.timestamp),
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume
        )
        for row in df.reset_index().itertuples(index=False)
    ]

def get_bars_zip(df: pd.DataFrame) -> List[OHLCBar]:
    # reset_index ensures timestamp is a column
    df_reset = df.reset_index()
    # Assuming columns are present and strictly ordered or accessed by name
    return [
        OHLCBar(int(ts), o, h, l, c, v)
        for ts, o, h, l, c, v in zip(
            df_reset["timestamp"],
            df_reset["open"],
            df_reset["high"],
            df_reset["low"],
            df_reset["close"],
            df_reset["volume"]
        )
    ]

def main():
    # Setup data
    N = 10000
    data = {
        "timestamp": range(1000000000, 1000000000 + N * 60, 60),
        "open": [100.0] * N,
        "high": [105.0] * N,
        "low": [95.0] * N,
        "close": [102.0] * N,
        "volume": [1.5] * N,
    }
    df = pd.DataFrame(data).set_index("timestamp")

    # Warmup
    get_bars_original(df.head(100))
    get_bars_itertuples(df.head(100))
    get_bars_zip(df.head(100))

    # Benchmark Original
    start = time.perf_counter()
    for _ in range(50):
        get_bars_original(df)
    end = time.perf_counter()
    orig_time = (end - start) / 50

    # Benchmark Itertuples
    start = time.perf_counter()
    for _ in range(50):
        get_bars_itertuples(df)
    end = time.perf_counter()
    iter_time = (end - start) / 50

    # Benchmark Zip
    start = time.perf_counter()
    for _ in range(50):
        get_bars_zip(df)
    end = time.perf_counter()
    zip_time = (end - start) / 50

    print(f"Original:   {orig_time:.6f}s")
    print(f"Itertuples: {iter_time:.6f}s (Speedup: {orig_time / iter_time:.2f}x)")
    print(f"Zip:        {zip_time:.6f}s (Speedup: {orig_time / zip_time:.2f}x)")

if __name__ == "__main__":
    main()
