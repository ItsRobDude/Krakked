# tests/test_api.py

import pytest
import time
from unittest.mock import MagicMock, patch, PropertyMock
from kraken_bot.config import AppConfig, UniverseConfig, MarketDataConfig, ConnectionStatus, PortfolioConfig
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
        ),
        portfolio=PortfolioConfig()
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
    # Mock the is_connected property using PropertyMock
    type(mock_ws_instance).is_connected = PropertyMock(return_value=True)
    mock_ws_instance.last_ticker_update_ts = {"XBTUSD": time.monotonic()}
    mock_ws_instance.subscription_status = {}
    api._ws_client = mock_ws_instance

    status = api.get_data_status()
    assert isinstance(status, ConnectionStatus)
    assert status.rest_api_reachable is True
    assert status.websocket_connected is True
    assert status.streaming_pairs == 1
    assert status.stale_pairs == 0
    assert status.subscription_errors == 0

    # --- Test Case 2: REST API fails ---
    api._rest_client.get_public.side_effect = Exception("Connection failed")
    status = api.get_data_status()
    assert status.rest_api_reachable is False

    # --- Test Case 3: WebSocket is disconnected ---
    api._rest_client.get_public.side_effect = None # Reset side effect
    type(mock_ws_instance).is_connected = PropertyMock(return_value=False)
    status = api.get_data_status()
    assert status.websocket_connected is False
    assert status.streaming_pairs == 0
    assert status.stale_pairs == 1 # The one pair is now stale
    assert status.subscription_errors == 0

    # --- Test Case 4: Data is stale ---
    type(mock_ws_instance).is_connected = PropertyMock(return_value=True)
    # Set the last update to be older than the tolerance
    mock_ws_instance.last_ticker_update_ts = {"XBTUSD": time.monotonic() - 120}
    mock_ws_instance.subscription_status = {"XBTUSD": {"ticker": {"status": "error"}}}
    status = api.get_data_status()
    assert status.websocket_connected is True
    assert status.streaming_pairs == 0
    assert status.stale_pairs == 1
    assert status.subscription_errors == 1


@patch('kraken_bot.market_data.api.build_universe')
@patch('kraken_bot.market_data.api.PairMetadataStore')
def test_get_universe_returns_canonical_pairs(mock_metadata_store, mock_build_universe, mock_config):
    pair_one = MagicMock()
    pair_one.canonical = "XBTUSD"
    pair_two = MagicMock()
    pair_two.canonical = "ETHUSD"
    mock_build_universe.return_value = [pair_one, pair_two]

    api = MarketDataAPI(mock_config)
    api.refresh_universe()

    assert api.get_universe() == ["XBTUSD", "ETHUSD"]
    assert api.get_universe_metadata() == [pair_one, pair_two]


def test_channel_specific_staleness_checks(mock_config):
    pair = MagicMock()
    pair.canonical = "XBTUSD"

    api = MarketDataAPI(mock_config)
    api._universe_map = {"XBTUSD": pair}

    mock_ws = MagicMock()
    api._ws_client = mock_ws

    mock_ws.last_ticker_update_ts = {"XBTUSD": time.monotonic()}
    mock_ws.ticker_cache = {"XBTUSD": {"bid": "1.0", "ask": "3.0"}}
    mock_ws.last_ohlc_update_ts = {}
    mock_ws.ohlc_cache = {}

    assert api.get_latest_price("XBTUSD") == 2.0

    with pytest.raises(DataStaleError):
        api.get_live_ohlc("XBTUSD", "1m")


def test_ohlc_staleness_independent_from_ticker(mock_config):
    pair = MagicMock()
    pair.canonical = "XBTUSD"

    api = MarketDataAPI(mock_config)
    api._universe_map = {"XBTUSD": pair}

    mock_ws = MagicMock()
    api._ws_client = mock_ws

    mock_ws.last_ticker_update_ts = {"XBTUSD": time.monotonic() - 120}
    mock_ws.ticker_cache = {"XBTUSD": {"bid": "1.0", "ask": "3.0"}}
    mock_ws.last_ohlc_update_ts = {"XBTUSD": {"1m": time.monotonic()}}
    mock_ws.ohlc_cache = {
        "XBTUSD": {
            "1m": {
                "timestamp": "1672531200.0",
                "open": "1.0",
                "high": "2.0",
                "low": "0.5",
                "close": "1.5",
                "volume": "10.0",
            }
        }
    }

    with pytest.raises(DataStaleError):
        api.get_latest_price("XBTUSD")

    ohlc_bar = api.get_live_ohlc("XBTUSD", "1m")
    assert ohlc_bar is not None
    assert ohlc_bar.close == 1.5
