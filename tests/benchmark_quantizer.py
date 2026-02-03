import timeit
from unittest.mock import MagicMock

from kraken_bot.portfolio.portfolio import Portfolio


class FakeMeta:
    canonical = "XBTUSD"
    volume_decimals = 8
    price_decimals = 2


class FakeMarketData:
    def get_pair_metadata(self, pair):
        return FakeMeta()

    def normalize_asset(self, asset):
        return asset

    def get_valuation_pair(self, asset):
        return None

    def get_latest_price(self, pair):
        return 50000.0


def run_benchmark():
    # Setup
    mock_config = MagicMock()
    mock_config.valuation_pairs = {}
    mock_config.base_currency = "USD"
    mock_config.reconciliation_tolerance = 0.01

    fake_market_data = FakeMarketData()

    portfolio = Portfolio(
        config=mock_config, market_data=fake_market_data, store=MagicMock()
    )

    pair = "XBTUSD"
    vol = 123.456789123

    # Benchmark function
    def task():
        portfolio._round_vol(pair, vol)

    # Warmup
    for _ in range(100):
        task()

    # Run
    count = 100_000
    t = timeit.timeit(task, number=count)
    print(f"Time for {count} calls: {t:.4f}s")


if __name__ == "__main__":
    run_benchmark()
