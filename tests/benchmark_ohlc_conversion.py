"""Benchmark for FileOHLCStore DataFrame to OHLCBar conversion."""

import tempfile
import time
from typing import List

import numpy as np
import pandas as pd

from kraken_bot.config import MarketDataConfig
from kraken_bot.market_data.models import OHLCBar
from kraken_bot.market_data.ohlc_store import FileOHLCStore


def baseline_conversion(df: pd.DataFrame) -> List[OHLCBar]:
    """Replicates the pre-optimization conversion method."""
    records = df.reset_index().to_dict("records")
    for row in records:
        row["timestamp"] = int(row["timestamp"])
    return [OHLCBar(**row) for row in records]


def run_benchmark() -> None:
    """Runs the conversion benchmark."""
    num_bars = 200_000
    print(f"Benchmarking OHLCBar conversion for {num_bars} bars...")

    with tempfile.TemporaryDirectory() as td:
        config = MarketDataConfig(
            ws={"enabled": False},
            ohlc_store={"backend": "parquet", "root_dir": td},
            backfill_timeframes=[],
            ws_timeframes=[],
        )
        store = FileOHLCStore(config)

        # generate dummy df
        now = int(time.time())
        timestamps = np.arange(now - num_bars * 60, now, 60)
        df = pd.DataFrame(
            {
                "open": np.random.rand(num_bars),
                "high": np.random.rand(num_bars),
                "low": np.random.rand(num_bars),
                "close": np.random.rand(num_bars),
                "volume": np.random.rand(num_bars),
            },
            index=timestamps,
        )
        df.index.name = "timestamp"  # type: ignore[attr-defined]

        pair = "XXBTZUSD"
        tf = "1m"
        filepath = store._get_file_path(pair, tf)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(filepath)

        # 1. Benchmark Baseline (old logic)
        baseline_times = []
        df_for_baseline = pd.read_parquet(filepath)
        for _ in range(5):
            start = time.time()
            _ = baseline_conversion(df_for_baseline)
            baseline_times.append(time.time() - start)
        median_baseline = np.median(baseline_times)
        print(f"Baseline (to_dict) median time: {median_baseline:.4f}s")

        # 2. Benchmark Optimized (new logic via the store directly)
        optimized_times = []
        for _ in range(5):
            # Clear cache between runs to measure just the read+convert time
            store._bar_cache.clear()
            start = time.time()
            _ = store.get_bars(pair, tf, lookback=num_bars)
            optimized_times.append(time.time() - start)

        median_optimized = np.median(optimized_times)
        print(f"Optimized (_df_to_bars) median time: {median_optimized:.4f}s")

        if median_optimized > 0:
            speedup = median_baseline / median_optimized
            print(f"Speedup: {speedup:.2f}x")

        store.shutdown()


if __name__ == "__main__":
    run_benchmark()
