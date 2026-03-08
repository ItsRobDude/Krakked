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


def main():
    # Generate 100k bars
    bars = [
        OHLCBar(timestamp=i, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
        for i in range(100_000)
    ]

    # Measure asdict
    t0 = time.time()
    df1 = pd.DataFrame([asdict(b) for b in bars])
    t1 = time.time()
    print(f"asdict approach: {t1 - t0:.4f}s")

    # Measure simple list compression
    t0 = time.time()
    df4 = pd.DataFrame(
        [(b.timestamp, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    t1 = time.time()
    print(f"list tuple approach: {t1 - t0:.4f}s")

    assert df1.equals(df4)


if __name__ == "__main__":
    main()
