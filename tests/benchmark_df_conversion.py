import timeit
from dataclasses import asdict

import pandas as pd

from kraken_bot.market_data.models import OHLCBar

# Generate 100,000 bars
ohlc = [
    OHLCBar(timestamp=i, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
    for i in range(100000)
]


def using_asdict():
    return pd.DataFrame([asdict(b) for b in ohlc])


def using_dict_list():
    return pd.DataFrame(
        {
            "timestamp": [b.timestamp for b in ohlc],
            "open": [b.open for b in ohlc],
            "high": [b.high for b in ohlc],
            "low": [b.low for b in ohlc],
            "close": [b.close for b in ohlc],
            "volume": [b.volume for b in ohlc],
        }
    )


t1 = timeit.timeit(using_asdict, number=5)
t2 = timeit.timeit(using_dict_list, number=5)

print(f"using_asdict: {t1/5:.4f}s")
print(f"using_dict_list: {t2/5:.4f}s")
