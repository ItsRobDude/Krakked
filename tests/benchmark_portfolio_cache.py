import time
from unittest.mock import MagicMock

from kraken_bot.portfolio.portfolio import Portfolio


def generate_trades(num_trades):
    trades = []
    pairs = ["XXBTZUSD", "XETHZUSD", "SOLUSD", "ADAUSD", "DOTUSD"]
    for i in range(num_trades):
        trades.append(
            {
                "pair": pairs[i % len(pairs)],
                "type": "buy" if i % 2 == 0 else "sell",
                "price": "50000.0",
                "vol": "1.0",
                "cost": "50000.0",
                "fee": "50.0",
                "time": 1700000000 + i,
                "ordertxid": f"tx{i}",
                "userref": None,
                "comment": None,
            }
        )
    return trades


def run_benchmark():
    # Setup
    mock_config = MagicMock()
    mock_config.valuation_pairs = {}
    mock_config.base_currency = "USD"
    mock_config.include_assets = []
    mock_config.exclude_assets = []
    mock_config.track_manual_trades = True

    # Mock MarketData
    mock_market_data = MagicMock()

    # Configure get_pair_metadata return value
    def get_meta_side_effect(pair):
        meta = MagicMock()
        if "XBT" in pair:
            meta.canonical = "XBTUSD"
            meta.base = "XBT"
            meta.quote = "USD"
        elif "ETH" in pair:
            meta.canonical = "ETHUSD"
            meta.base = "ETH"
            meta.quote = "USD"
        else:
            meta.canonical = pair
            meta.base = pair.replace("USD", "")
            meta.quote = "USD"
        meta.volume_decimals = 8
        meta.price_decimals = 2
        return meta

    mock_market_data.get_pair_metadata.side_effect = get_meta_side_effect
    mock_market_data.normalize_asset.side_effect = lambda x: x

    # Mock Store
    mock_store = MagicMock()

    portfolio = Portfolio(
        config=mock_config, market_data=mock_market_data, store=mock_store
    )

    trades = generate_trades(100000)

    start_time = time.time()
    # Ingest without persistence
    portfolio.ingest_trades(trades, persist=False)
    end_time = time.time()

    print(f"Time taken to ingest 100,000 trades: {end_time - start_time:.4f} seconds")
    print(
        f"MarketDataAPI.get_pair_metadata call count: {mock_market_data.get_pair_metadata.call_count}"
    )


if __name__ == "__main__":
    run_benchmark()
