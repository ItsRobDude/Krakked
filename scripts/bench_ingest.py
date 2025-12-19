
import time
import random
from unittest.mock import MagicMock
from kraken_bot.portfolio.portfolio import Portfolio
from kraken_bot.market_data.api import MarketDataAPI

# Mock Config
mock_config = MagicMock()
mock_config.valuation_pairs = {}
mock_config.base_currency = "USD"
mock_config.include_assets = []
mock_config.exclude_assets = []
mock_config.track_manual_trades = True

# Mock Store
mock_store = MagicMock()

# Setup MarketDataAPI
mock_rest_client = MagicMock()
mock_config_app = MagicMock()
mock_config_app.market_data.metadata_path = None
mock_config_app.market_data.ws_timeframes = []
mock_config_app.market_data.backfill_timeframes = []

market_data = MarketDataAPI(config=mock_config_app, rest_client=mock_rest_client)

# Manually populate universe maps
from kraken_bot.market_data.models import PairMetadata
universe = [
    PairMetadata(
        canonical="XBTUSD",
        rest_symbol="XXBTZUSD",
        ws_symbol="XBT/USD",
        base="XBT",
        quote="USD",
        raw_name="XXBTZUSD",
        price_decimals=1,
        volume_decimals=8,
        min_order_size=0.0001,
        lot_size=1,
        status="online"
    ),
    PairMetadata(
        canonical="ETHUSD",
        rest_symbol="XETHZUSD",
        ws_symbol="ETH/USD",
        base="ETH",
        quote="USD",
        raw_name="XETHZUSD",
        price_decimals=2,
        volume_decimals=8,
        min_order_size=0.01,
        lot_size=1,
        status="online"
    ),
]
market_data._universe = universe
market_data._universe_map = {p.canonical: p for p in universe}
market_data._asset_map = {"XXBT": "XBT", "XBT": "XBT", "ZUSD": "USD", "USD": "USD", "XETH": "ETH", "ETH": "ETH"}
market_data._alias_map = {}
for p in universe:
    market_data._alias_map[p.canonical] = p
    market_data._alias_map[p.rest_symbol] = p
    market_data._alias_map[p.ws_symbol] = p
    market_data._alias_map[p.ws_symbol.replace("/", "")] = p

market_data.get_latest_price = MagicMock(return_value=100.0)

# Generate trades
NUM_TRADES = 100000
trades = []
for i in range(NUM_TRADES):
    pair = "XBTUSD" if i % 2 == 0 else "ETHUSD"
    pair_input = pair if i % 3 == 0 else (universe[0].ws_symbol if pair == "XBTUSD" else universe[1].ws_symbol)

    trades.append({
        "pair": pair_input,
        "type": "buy",
        "price": "100.0",
        "vol": "0.1",
        "cost": "10.0",
        "fee": "0.1",
        "time": 1000 + i,
        "ordertxid": f"ord{i}",
        "userref": None,
        "comment": None
    })

# --- Measure Logic ---
# Since the code is now modified to use the cache, we can only verify the current performance.
# To show "before/after", one would need to disable the cache.
# We can simulate "no cache" by clearing it every iteration or patching methods, but that's complex.
# This script serves as a stable harness to measure the CURRENT state.

portfolio = Portfolio(
    config=mock_config, market_data=market_data, store=mock_store
)

start_time = time.time()
portfolio.ingest_trades(trades, persist=False)
end_time = time.time()
duration = end_time - start_time

print(f"Ingested {NUM_TRADES} trades in {duration:.4f}s ({NUM_TRADES/duration:.0f} trades/s)")
