import time
from typing import List

import pandas as pd

from kraken_bot.config import OHLCBar


def _df_to_bars(df: pd.DataFrame) -> List[OHLCBar]:
    idx = df.index.values.tolist()
    op = df["open"].values.tolist()
    hi = df["high"].values.tolist()
    lo = df["low"].values.tolist()
    cl = df["close"].values.tolist()
    vo = df["volume"].values.tolist()
    return [
        OHLCBar(int(t), o_, h_, l_, c_, v_)
        for t, o_, h_, l_, c_, v_ in zip(idx, op, hi, lo, cl, vo)
    ]


def run_benchmark():
    rows = 200000
    df = pd.DataFrame(
        {
            "timestamp": range(rows),
            "open": [100.0] * rows,
            "high": [105.0] * rows,
            "low": [99.0] * rows,
            "close": [101.0] * rows,
            "volume": [1000.0] * rows,
        }
    ).set_index("timestamp")

    def to_dict(d: pd.DataFrame):
        records = d.reset_index().to_dict("records")
        for row in records:
            row["timestamp"] = int(row["timestamp"])
        return [OHLCBar(**row) for row in records]

    start = time.perf_counter()
    to_dict(df)
    to_dict(df)
    to_dict(df)
    to_dict(df)
    to_dict(df)
    dict_time = time.perf_counter() - start

    start = time.perf_counter()
    _df_to_bars(df)
    _df_to_bars(df)
    _df_to_bars(df)
    _df_to_bars(df)
    _df_to_bars(df)
    zip_time = time.perf_counter() - start

    print(f"to_dict time: {dict_time:.4f}s")
    print(f"helper function time: {zip_time:.4f}s")
    print(f"Speedup: {dict_time / zip_time:.2f}x")


if __name__ == "__main__":
    run_benchmark()
