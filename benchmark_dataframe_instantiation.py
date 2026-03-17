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
    volume: float


# Generate sample data for benchmark - 200,000 rows
num_rows = 200000
ohlc = [
    OHLCBar(timestamp=i, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
    for i in range(num_rows)
]


def bench_asdict():
    start = time.perf_counter()
    _ = pd.DataFrame([asdict(b) for b in ohlc])
    return time.perf_counter() - start


def bench_dict_comprehension():
    start = time.perf_counter()
    _ = pd.DataFrame(
        {
            "timestamp": [b.timestamp for b in ohlc],
            "open": [b.open for b in ohlc],
            "high": [b.high for b in ohlc],
            "low": [b.low for b in ohlc],
            "close": [b.close for b in ohlc],
            "volume": [b.volume for b in ohlc],
        }
    )
    return time.perf_counter() - start


def bench_zip():
    start = time.perf_counter()
    data = list(
        zip(
            [b.timestamp for b in ohlc],
            [b.open for b in ohlc],
            [b.high for b in ohlc],
            [b.low for b in ohlc],
            [b.close for b in ohlc],
            [b.volume for b in ohlc],
        )
    )
    _ = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return time.perf_counter() - start


def bench_single_series():
    start = time.perf_counter()
    _ = pd.Series([b.close for b in ohlc])
    return time.perf_counter() - start


asdict_times = [bench_asdict() for _ in range(3)]
dict_comp_times = [bench_dict_comprehension() for _ in range(3)]
zip_times = [bench_zip() for _ in range(3)]
series_comp_times = [bench_single_series() for _ in range(3)]

print(f"asdict median: {sorted(asdict_times)[len(asdict_times)//2]:.4f}s")
print(f"dict_comp median: {sorted(dict_comp_times)[len(dict_comp_times)//2]:.4f}s")
print(f"zip median: {sorted(zip_times)[len(zip_times)//2]:.4f}s")
print(
    f"series_comp median: {sorted(series_comp_times)[len(series_comp_times)//2]:.4f}s"
)
