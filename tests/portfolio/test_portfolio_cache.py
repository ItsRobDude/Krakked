from unittest.mock import MagicMock

from kraken_bot.portfolio.portfolio import Portfolio


def test_process_trade_cache_optimization():
    """
    Verify that _process_trade uses the internal cache to avoid repetitive
    MarketDataAPI calls for the same pair.
    """
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
    pair_meta = MagicMock()
    pair_meta.canonical = "XBTUSD"
    pair_meta.base = "XBT"
    pair_meta.quote = "USD"
    pair_meta.volume_decimals = 8
    pair_meta.price_decimals = 1

    mock_market_data.get_pair_metadata.return_value = pair_meta

    # Configure normalize_asset to return simple strings
    mock_market_data.normalize_asset.side_effect = lambda x: x

    # Mock Store
    mock_store = MagicMock()

    portfolio = Portfolio(
        config=mock_config, market_data=mock_market_data, store=mock_store
    )

    # Define a sample trade
    trade = {
        "pair": "XXBTZUSD",
        "type": "buy",
        "price": "50000.0",
        "vol": "1.0",
        "cost": "50000.0",
        "fee": "50.0",
        "time": 1700000000,
        "ordertxid": "tx1",
        "userref": None,
        "comment": None,
    }

    # 1. First call: Should trigger MD lookups
    portfolio._process_trade(trade)

    # Note: _process_trade ALSO calls _round_vol and _round_price which call get_pair_metadata
    # We expect:
    # 1x from caching logic
    # 1x from _round_vol
    # 1x from _round_price
    # Total = 3 calls

    assert mock_market_data.get_pair_metadata.call_count >= 1
    initial_count = mock_market_data.get_pair_metadata.call_count

    # normalize_asset called twice (base + quote) in cache logic
    assert mock_market_data.normalize_asset.call_count == 2

    # 2. Second call with SAME pair: Should use cache for the MAIN resolution
    # Bolt optimization: _round_vol and _round_price now use the cached PairMetadata.
    # So we expect call_count to increase by 0.

    portfolio._process_trade(trade)

    new_count = mock_market_data.get_pair_metadata.call_count
    diff = new_count - initial_count
    assert diff == 0, f"Expected 0 additional calls, got {diff}"

    # Normalize asset should NOT increase
    assert mock_market_data.normalize_asset.call_count == 2

    # 3. Third call with DIFFERENT pair: Should trigger new lookups
    trade_eth = {
        "pair": "XETHZUSD",
        "type": "buy",
        "price": "3000.0",
        "vol": "1.0",
        "cost": "3000.0",
        "fee": "3.0",
        "time": 1700000001,
        "ordertxid": "tx2",
        "userref": None,
        "comment": None,
    }

    # Update mock for new pair
    eth_meta = MagicMock()
    eth_meta.canonical = "ETHUSD"
    eth_meta.base = "ETH"
    eth_meta.quote = "USD"
    eth_meta.volume_decimals = 8
    eth_meta.price_decimals = 2

    # Side effect to return correct meta based on input
    def get_meta_side_effect(pair):
        if pair == "XXBTZUSD" or pair == "XBTUSD":
            return pair_meta
        return eth_meta

    mock_market_data.get_pair_metadata.side_effect = get_meta_side_effect

    portfolio._process_trade(trade_eth)

    # Should trigger cache miss, which resolves it.
    # Bolt: the cached metadata is then passed to _round_vol and _round_price, so no extra calls there.
    # Total +1 call

    final_count = mock_market_data.get_pair_metadata.call_count
    assert final_count == new_count + 1

    # Normalize asset should increase by 2
    assert mock_market_data.normalize_asset.call_count == 4
