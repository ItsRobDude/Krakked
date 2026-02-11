"""Benchmark script for Portfolio rounding methods.

This script measures the performance of _round_vol and _round_price methods
using minimal fake objects to avoid mock overhead.
"""

import time

from kraken_bot.config import PortfolioConfig
from kraken_bot.portfolio.portfolio import Portfolio


# Minimal fake classes
class FakePairMeta:
    __slots__ = ("volume_decimals", "price_decimals")

    def __init__(self):
        self.volume_decimals = 8
        self.price_decimals = 2


class FakeMarketData:
    def __init__(self):
        self.meta = FakePairMeta()

    def get_pair_metadata(self, pair):
        return self.meta


class FakeStore:
    pass


def benchmark():
    # Setup Fakes
    mock_config = PortfolioConfig()
    mock_market_data = FakeMarketData()
    mock_store = FakeStore()

    # Instantiate Portfolio
    portfolio = Portfolio(mock_config, mock_market_data, mock_store)

    # Test Data
    pair = "XBTUSD"
    vol = 1.23456789
    price = 50000.12345
    iterations = 200000

    print(f"Running benchmark with {iterations} iterations...")

    # Warmup
    for _ in range(1000):
        portfolio._round_vol(pair, vol)

    # Benchmark _round_vol
    start_time = time.time()
    for _ in range(iterations):
        portfolio._round_vol(pair, vol)
    end_time = time.time()
    vol_time = end_time - start_time
    print(f"_round_vol time: {vol_time:.4f}s")

    # Benchmark _round_price
    start_time = time.time()
    for _ in range(iterations):
        portfolio._round_price(pair, price)
    end_time = time.time()
    price_time = end_time - start_time
    print(f"_round_price time: {price_time:.4f}s")

    total_time = vol_time + price_time
    print(f"Total time: {total_time:.4f}s")


if __name__ == "__main__":
    benchmark()
