# tests/test_api.py

import pytest
import time
from unittest.mock import MagicMock, patch
from kraken_bot.config import AppConfig, UniverseConfig, MarketDataConfig, ConnectionStatus
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.market_data.exceptions import DataStaleError

@pytest.fixture
def mock_config() -> AppConfig:
    """Provides a mock AppConfig for testing."""
    return AppConfig(
        region=MagicMock(),
        universe=MagicMock(),
        market_data=MarketDataConfig(
            ws={"stale_tolerance_seconds": 60},
            ohlc_store={},
            backfill_timeframes=[],
            ws_timeframes=[]
        )
    )

@patch('kraken_bot.market_data.api.build_universe')
@patch('kraken_bot.market_data.api.KrakenWSClientV2')
@patch('kraken_bot.market_data.api.FileOHLCStore')
def test_get_data_status(mock_store, mock_ws_client_class, mock_build_universe, mock_config):
    """
    Tests the get_data_status method under various conditions.
    """
    # Mock the universe to contain one pair
    mock_pair = MagicMock()
    mock_pair.canonical = "XBTUSD"
    mock_build_universe.return_value = [mock_pair]

    api = MarketDataAPI(mock_config)
    api.initialize(backfill=False)

    # Mock the REST client on the instance
    api._rest_client = MagicMock()

    # --- Test Case 1: Everything is healthy ---
    api._rest_client.get_public.return_value = {} # Successful call

    mock_ws_instance = mock_ws_client_class.return_value
    mock_ws_instance._websocket.open = True
    mock_ws_instance.last_update_ts = {"XBTUSD": time.monotonic()}
    api._ws_client = mock_ws_instance

    status = api.get_data_status()
    assert isinstance(status, ConnectionStatus)
    assert status.rest_api_reachable is True
    assert status.websocket_connected is True
    assert status.streaming_pairs == 1
    assert status.stale_pairs == 0

    # --- Test Case 2: REST API fails ---
    api._rest_client.get_public.side_effect = Exception("Connection failed")
    status = api.get_data_status()
    assert status.rest_api_reachable is False

    # --- Test Case 3: WebSocket is disconnected ---
    api._rest_client.get_public.side_effect = None # Reset side effect
    mock_ws_instance._websocket.open = False
    status = api.get_data_status()
    assert status.websocket_connected is False
    assert status.streaming_pairs == 0
    assert status.stale_pairs == 1 # The one pair is now stale

    # --- Test Case 4: Data is stale ---
    mock_ws_instance._websocket.open = True
    # Set the last update to be older than the tolerance
    mock_ws_instance.last_update_ts = {"XBTUSD": time.monotonic() - 120}
    status = api.get_data_status()
    assert status.websocket_connected is True
    assert status.streaming_pairs == 0
    assert status.stale_pairs == 1

@patch('kraken_bot.market_data.api.build_universe')
@patch('kraken_bot.market_data.api.KrakenWSClientV2')
@patch('kraken_bot.market_data.api.FileOHLCStore')
def test_live_data_methods(mock_store, mock_ws_client_class, mock_build_universe, mock_config):
    """
    Tests get_latest_price, get_best_bid_ask, and get_live_ohlc under different
    data freshness conditions.
    """
    # Setup
    mock_pair = MagicMock()
    mock_pair.canonical = "XBTUSD"
    mock_build_universe.return_value = [mock_pair]

    api = MarketDataAPI(mock_config)
    api.initialize(backfill=False)

    mock_ws_instance = mock_ws_client_class.return_value
    mock_ws_instance._websocket.open = True
    api._ws_client = mock_ws_instance

    # --- Scenario 1: Fresh Data ---
    mock_ws_instance.last_update_ts = {"XBTUSD": time.monotonic()}
    mock_ws_instance.ticker_cache = {"XBTUSD": {"bid": "50000", "ask": "50001"}}
    mock_ws_instance.ohlc_cache = {"XBTUSD": {"1m": {
        "timestamp": "1672531200.123", "open": "49000", "high": "51000",
        "low": "48000", "close": "50000", "volume": "100"
    }}}

    assert api.get_latest_price("XBTUSD") == 50000.5
    assert api.get_best_bid_ask("XBTUSD") == {"bid": 50000, "ask": 50001}
    live_ohlc = api.get_live_ohlc("XBTUSD", "1m")
    assert live_ohlc is not None
    assert live_ohlc.open == 49000

    # --- Scenario 2: Stale Data ---
    mock_ws_instance.last_update_ts = {"XBTUSD": time.monotonic() - 100} # > 60s tolerance

    with pytest.raises(DataStaleError):
        api.get_latest_price("XBTUSD")
    with pytest.raises(DataStaleError):
        api.get_best_bid_ask("XBTUSD")
    with pytest.raises(DataStaleError):
        api.get_live_ohlc("XBTUSD", "1m")

    # --- Scenario 3: No Data Yet ---
    mock_ws_instance.last_update_ts = {"ETHUSD": time.monotonic()} # Update for a different pair
    mock_ws_instance.ticker_cache = {}
    mock_ws_instance.ohlc_cache = {}

    # Should raise stale error because no update has ever been received for XBTUSD
    with pytest.raises(DataStaleError):
        api.get_latest_price("XBTUSD")

    # After a fake update, it should return None because the cache is empty
    mock_ws_instance.last_update_ts["XBTUSD"] = time.monotonic()
    assert api.get_latest_price("XBTUSD") is None
    assert api.get_best_bid_ask("XBTUSD") is None
    assert api.get_live_ohlc("XBTUSD", "1m") is None
