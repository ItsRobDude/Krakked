import time
from unittest.mock import MagicMock
from typing import Optional

from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.models import PairMetadata
from kraken_bot.config import AppConfig


# Mock classes to avoid dependencies
class MockWSClient:
    def __init__(self):
        self.ticker_cache = {}
        self.last_ticker_update_ts = {}


def create_pair(symbol: str) -> PairMetadata:
    return PairMetadata(
        canonical=symbol,
        base="XBT",
        quote="USD",
        rest_symbol=symbol,
        ws_symbol=symbol,
        raw_name=symbol,
        price_decimals=2,
        volume_decimals=8,
        lot_size=1,
        min_order_size=0.0001,
        status="online"
    )


def setup_env():
    # Mock Config
    config = MagicMock(spec=AppConfig)
    config.market_data = MagicMock()
    config.market_data.ws = {"stale_tolerance_seconds": 60}
    config.market_data.metadata_path = None

    # Init API
    api = MarketDataAPI(config)

    # Inject Mock WS Client
    api._ws_client = MockWSClient()

    # Inject Universe
    pairs = ["XBTUSD", "ETHUSD", "SOLUSD", "DOGEUSD"]
    api._universe = [create_pair(p) for p in pairs]
    api._universe_map = {p.canonical: p for p in api._universe}
    api._alias_map = {p.canonical: p for p in api._universe}  # Minimal alias map

    # Populate Cache with typical string values from API
    api._ws_client.ticker_cache["XBTUSD"] = {
        "bid": "50000.0000",
        "ask": "50010.0000",
        "last": "50005.0000",
        "volume": "100.0",
        "vwap": "50002.0"
    }
    api._ws_client.last_ticker_update_ts["XBTUSD"] = time.monotonic()

    return api


class FastMarketDataAPI(MarketDataAPI):
    def get_latest_price(self, pair: str) -> Optional[float]:
        # Fast path simulation: assuming normalize_pair is cached and fast enough
        # In reality, we will optimize get_latest_price to use pre-calc mid

        # We need to bypass the original implementation to test the "after" state
        # without modifying the source yet.

        canonical = self.normalize_pair(pair)
        if canonical == "USD":
            return 1.0

        is_fresh, stale_time = self._ticker_freshness(canonical)

        if is_fresh and self._ws_client:
            ticker = self._ws_client.ticker_cache.get(canonical)
            if ticker:
                # Optimized: access pre-calculated mid
                # In the real impl, we'd ensure ticker has 'mid'
                return ticker.get("mid")

        # Fallback (simplified for benchmark)
        return None


def setup_fast_env():
    api = setup_env()
    # Patch the cache to have pre-calculated mid
    # The 'ticker_cache' will hold dicts that now have 'mid' as float
    # and we will patch the class to be FastMarketDataAPI

    # Manually adding 'mid' to the mock data
    current_data = api._ws_client.ticker_cache["XBTUSD"]
    current_data["mid"] = (float(current_data["bid"]) + float(current_data["ask"])) / 2.0

    # Monkey patch the instance to use the fast method
    # Or cleaner: just instantiate FastMarketDataAPI but it's harder to mock
    # exactly the same way without duplication.
    # Let's just create a new instance using FastMarketDataAPI logic.

    fast_api = FastMarketDataAPI(api._config)
    fast_api._ws_client = api._ws_client
    fast_api._universe = api._universe
    fast_api._universe_map = api._universe_map
    fast_api._alias_map = api._alias_map

    return fast_api


def run_benchmark():
    iterations = 500_000

    # Baseline
    api = setup_env()
    start_time = time.perf_counter()
    for _ in range(iterations):
        api.get_latest_price("XBTUSD")
    end_time = time.perf_counter()
    baseline_time = end_time - start_time
    print(f"Baseline: {baseline_time:.4f}s ({baseline_time/iterations*1e6:.2f}us/call)")

    # Fast
    fast_api = setup_fast_env()
    # Warmup
    assert fast_api.get_latest_price("XBTUSD") == 50005.0

    start_time = time.perf_counter()
    for _ in range(iterations):
        fast_api.get_latest_price("XBTUSD")
    end_time = time.perf_counter()
    fast_time = end_time - start_time
    print(f"Fast:     {fast_time:.4f}s ({fast_time/iterations*1e6:.2f}us/call)")

    improvement = (baseline_time - fast_time) / baseline_time * 100
    print(f"Improvement: {improvement:.1f}%")


if __name__ == "__main__":
    run_benchmark()
