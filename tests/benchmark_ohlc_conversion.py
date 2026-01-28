import time
import pandas as pd
import numpy as np
from kraken_bot.market_data.models import OHLCBar
from kraken_bot.market_data.ohlc_store import FileOHLCStore

def run_benchmark():
    N = 1000
    df = pd.DataFrame({
        "timestamp": np.arange(N),
        "open": np.random.rand(N),
        "high": np.random.rand(N),
        "low": np.random.rand(N),
        "close": np.random.rand(N),
        "volume": np.random.rand(N)
    }).set_index("timestamp")

    # Method 1: Current (Legacy logic)
    start_time = time.time()
    iterations = 100
    for _ in range(iterations):
        records = df.reset_index().to_dict("records")
        for row in records:
            row["timestamp"] = int(row["timestamp"])
        res1 = [OHLCBar(**row) for row in records]
    end_time = time.time()
    t1 = (end_time - start_time) / iterations
    print(f"Method 1 (Legacy): {t1*1000:.4f} ms")

    # Method 2: Proposed (Actual Implementation)
    start_time = time.time()
    for _ in range(iterations):
        res2 = FileOHLCStore._df_to_bars(df)
    end_time = time.time()
    t2 = (end_time - start_time) / iterations
    print(f"Method 2 (Implementation): {t2*1000:.4f} ms")

    print(f"Speedup: {t1/t2:.2f}x")

    # Correctness check
    assert len(res1) == len(res2)
    assert res1[0] == res2[0]
    assert res1[-1] == res2[-1]

if __name__ == "__main__":
    run_benchmark()
