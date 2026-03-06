import time
from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class OHLCBar:
    time: int
    open: str
    high: str
    low: str
    close: str
    vwap: str
    volume: str
    count: int


def get_dummy_data(n: int) -> List[OHLCBar]:
    return [
        OHLCBar(
            time=1600000000 + i * 60,
            open="50000.0",
            high="50100.0",
            low="49900.0",
            close=str(50000.0 + i % 10),
            vwap="50000.0",
            volume="1.0",
            count=10,
        )
        for i in range(n)
    ]


def original(ohlc):
    df = pd.DataFrame([bar.__dict__ for bar in ohlc])
    closes = df["close"].astype(float)
    returns = closes.pct_change().dropna()
    return returns


def optimized(ohlc):
    # Vectorized list extraction directly to Series as float
    closes = pd.Series([bar.close for bar in ohlc], dtype=float)
    returns = closes.pct_change().dropna()
    return returns


def run_bench():
    ohlc = get_dummy_data(200)  # Since it fetches 200 bars in infer_regime

    # Assert equivalence
    pd.testing.assert_series_equal(original(ohlc), optimized(ohlc), check_names=False)

    # Warmup
    original(ohlc)
    optimized(ohlc)

    start = time.perf_counter()
    for _ in range(1000):
        original(ohlc)
    orig_time = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(1000):
        optimized(ohlc)
    opt_time = time.perf_counter() - start

    print(f"Original: {orig_time:.4f}s")
    print(f"Optimized: {opt_time:.4f}s")
    print(f"Improvement: {orig_time/opt_time:.2f}x")


if __name__ == "__main__":
    run_bench()
