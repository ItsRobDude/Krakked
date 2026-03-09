import statistics
import time
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class OHLCBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    vwap: float
    volume: float
    count: int


def generate_bars(n):
    return [
        OHLCBar(1600000000 + i, 100.0, 105.0, 95.0, 102.0, 101.0, 1000.0, 50)
        for i in range(n)
    ]


def benchmark():
    bars = generate_bars(100_000)

    # We will do 5 runs of each to get the median
    runs = 5
    t_series_list = []
    t_asdict_list = []
    t_dict_list = []
    t_asdict2_list = []

    for _ in range(runs):
        # Method 1: pd.Series comprehension (Mean Reversion only needs close)
        start = time.perf_counter()
        close_series = pd.Series([b.close for b in bars])  # noqa: F841
        t_series_list.append(time.perf_counter() - start)

        # Method 2: pd.DataFrame asdict (Mean Reversion current)
        start = time.perf_counter()
        df_asdict = pd.DataFrame([asdict(bar) for bar in bars])
        close_series_asdict = df_asdict["close"]  # noqa: F841
        t_asdict_list.append(time.perf_counter() - start)

        # Method 3: dict comprehension (Vol Breakout & Risk current)
        start = time.perf_counter()
        df_dict = pd.DataFrame(  # noqa: F841
            {
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
            }
        )
        t_dict_list.append(time.perf_counter() - start)

        # Method 4: pd.DataFrame asdict (Vol Breakout & Risk current)
        start = time.perf_counter()
        df_asdict2 = pd.DataFrame([asdict(b) for b in bars])  # noqa: F841
        t_asdict2_list.append(time.perf_counter() - start)

    t_series = statistics.median(t_series_list)
    t_asdict = statistics.median(t_asdict_list)
    t_dict = statistics.median(t_dict_list)
    t_asdict2 = statistics.median(t_asdict2_list)

    print(f"pd.Series (close only) Median ({runs} runs): {t_series:.4f}s")
    print(
        f"pd.DataFrame asdict (Mean Reversion current) Median ({runs} runs): {t_asdict:.4f}s"
    )
    print(f"Mean Reversion Speedup: {t_asdict / t_series:.2f}x\n")

    print(f"dict of list comprehensions Median ({runs} runs): {t_dict:.4f}s")
    print(
        f"pd.DataFrame asdict (Vol Breakout & Risk current) Median ({runs} runs): {t_asdict2:.4f}s"
    )
    print(f"Vol Breakout / Risk Speedup: {t_asdict2/t_dict:.2f}x\n")


if __name__ == "__main__":
    benchmark()
