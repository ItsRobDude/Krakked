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

    # 1. First call: Should trigger exactly 1 MD lookup
    portfolio._process_trade(trade)

    # We now pass the cached PairMetadata object down to _round_vol and _round_price.
    # So we expect exactly 1 call to get_pair_metadata.
    assert mock_market_data.get_pair_metadata.call_count == 1
    initial_count = mock_market_data.get_pair_metadata.call_count

    # normalize_asset called twice (base + quote) in cache logic
    assert mock_market_data.normalize_asset.call_count == 2

    # 2. Second call with SAME pair: Should use cache for everything
    portfolio._process_trade(trade)

    new_count = mock_market_data.get_pair_metadata.call_count
    diff = new_count - initial_count
    assert diff == 0, f"Expected 0 additional calls due to caching, got {diff}"

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

    # Should trigger exactly 1 cache miss/lookup for the new pair
    final_count = mock_market_data.get_pair_metadata.call_count
    assert final_count == new_count + 1

    # Normalize asset should increase by 2
    assert mock_market_data.normalize_asset.call_count == 4


def test_quantizer_cache():
    """
    Verify that `_get_quantizer` properly memoizes Decimal instances.
    """
    q1 = Portfolio._get_quantizer(8)
    q2 = Portfolio._get_quantizer(8)
    q3 = Portfolio._get_quantizer(2)

    # Must return the same reference
    assert q1 is q2

    # Different inputs return different refs
    assert q1 is not q3

    # Value assertions
    assert str(q1) == "1.00000000"
    assert str(q3) == "1.00"
