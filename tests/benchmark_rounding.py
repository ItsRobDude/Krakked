import time
from unittest.mock import MagicMock

from kraken_bot.portfolio.portfolio import Portfolio


def benchmark_rounding():
    # Setup
    mock_config = MagicMock()
    mock_config.valuation_pairs = {}
    mock_config.base_currency = "USD"

    mock_market_data = MagicMock()
    meta = MagicMock()
    meta.canonical = "XBTUSD"
    meta.volume_decimals = 8
    meta.price_decimals = 2
    mock_market_data.get_pair_metadata.return_value = meta

    portfolio = Portfolio(
        config=mock_config, market_data=mock_market_data, store=MagicMock()
    )

    # Prepare data
    iterations = 200_000
    vol_input = 1.23456789123
    price_input = 50000.12345

    # Benchmark _round_vol
    start_time = time.perf_counter()
    for _ in range(iterations):
        portfolio._round_vol("XBTUSD", vol_input)
    end_time = time.perf_counter()
    vol_time = end_time - start_time

    # Benchmark _round_price
    start_time = time.perf_counter()
    for _ in range(iterations):
        portfolio._round_price("XBTUSD", price_input)
    end_time = time.perf_counter()
    price_time = end_time - start_time

    print(f"Iterations: {iterations}")
    print(f"_round_vol time: {vol_time:.4f}s")
    print(f"_round_price time: {price_time:.4f}s")
    print(f"Total time: {vol_time + price_time:.4f}s")

    # Check cache info
    print(f"Cache info: {portfolio._get_quantizer.cache_info()}")


if __name__ == "__main__":
    benchmark_rounding()
