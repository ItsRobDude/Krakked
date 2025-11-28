# tests/test_api_extensions.py

import pytest
from unittest.mock import MagicMock, patch
from kraken_bot.config import AppConfig, MarketDataConfig, PairMetadata, OHLCBar, PortfolioConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.exceptions import PairNotFoundError

@pytest.fixture
def mock_config() -> AppConfig:
    return AppConfig(
        region=MagicMock(),
        universe=MagicMock(),
        market_data=MarketDataConfig(
            ws={"stale_tolerance_seconds": 60},
            ohlc_store={"root_dir": "/tmp/test", "backend": "parquet"},
            backfill_timeframes=[],
            ws_timeframes=[]
        ),
        portfolio=PortfolioConfig()
    )

@pytest.fixture
def api(mock_config):
    with patch('kraken_bot.market_data.api.FileOHLCStore') as mock_store_cls:
        # Mock universe building
        with patch('kraken_bot.market_data.api.build_universe') as mock_build_universe:
            pair_meta = PairMetadata(
                canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XXBTZUSD",
                ws_symbol="XBT/USD", raw_name="XXBTZUSD", price_decimals=1,
                volume_decimals=8, lot_size=1, min_order_size=0.0, status="online"
            )
            mock_build_universe.return_value = [pair_meta]

            api_instance = MarketDataAPI(mock_config)
            api_instance.initialize(backfill=False)
            return api_instance

def test_get_ohlc_since_success(api):
    # Setup mock return from store
    mock_bar = OHLCBar(timestamp=100, open=1, high=2, low=0.5, close=1.5, volume=10)
    api._ohlc_store.get_bars_since.return_value = [mock_bar]

    result = api.get_ohlc_since("XBTUSD", "1h", 50)

    assert result == [mock_bar]
    api._ohlc_store.get_bars_since.assert_called_once_with("XBTUSD", "1h", 50)

def test_get_ohlc_since_pair_not_found(api):
    with pytest.raises(PairNotFoundError):
        api.get_ohlc_since("UNKNOWN", "1h", 50)

def test_backfill_ohlc_delegation(api):
    with patch('kraken_bot.market_data.api.backfill_ohlc') as mock_backfill_fn:
        mock_backfill_fn.return_value = 100

        count = api.backfill_ohlc("XBTUSD", "1h", 50)

        assert count == 100
        mock_backfill_fn.assert_called_once()
        args, kwargs = mock_backfill_fn.call_args
        assert kwargs['pair_metadata'].canonical == "XBTUSD"
        assert kwargs['timeframe'] == "1h"
        assert kwargs['since'] == 50
        assert kwargs['store'] == api._ohlc_store
        assert kwargs['client'] == api._rest_client

def test_backfill_ohlc_pair_not_found(api):
    with pytest.raises(PairNotFoundError):
        api.backfill_ohlc("UNKNOWN", "1h")
