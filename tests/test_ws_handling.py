# tests/test_ws_handling.py

import json
import asyncio
import pytest
from kraken_bot.config import PairMetadata
from kraken_bot.market_data.ws_client import KrakenWSClientV2

@pytest.fixture
def mock_pairs() -> list[PairMetadata]:
    """Provides a mock list of PairMetadata for testing."""
    return [
        PairMetadata("XBTUSD", "XBT", "USD", "XBTUSD", "XBT/USD", "XXBTZUSD", 8, 8, 1.0, 0.0, "online"),
        PairMetadata("ETHUSD", "ETH", "USD", "ETHUSD", "ETH/USD", "XETHZUSD", 8, 8, 1.0, 0.0, "online"),
    ]

@pytest.fixture
def ws_client(mock_pairs: list[PairMetadata]) -> KrakenWSClientV2:
    """Provides a KrakenWSClientV2 instance for testing."""
    return KrakenWSClientV2(pairs=mock_pairs, timeframes=["1m"])

def test_handle_ticker_message(ws_client: KrakenWSClientV2):
    """Tests that a valid ticker message correctly updates the ticker_cache."""
    message = {
        "channel": "ticker",
        "data": [
            {
                "ask": "60000.0",
                "bid": "59999.0",
                "last": "60000.0",
                "volume": "100.0"
            }
        ],
        "symbol": "XBT/USD",
        "type": "snapshot"
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert "XBTUSD" in ws_client.ticker_cache
    assert ws_client.ticker_cache["XBTUSD"]["bid"] == "59999.0"
    assert "XBTUSD" in ws_client.last_ticker_update_ts

def test_handle_ohlc_message(ws_client: KrakenWSClientV2):
    """Tests that a valid ohlc message correctly updates the ohlc_cache."""
    message = {
        "channel": "ohlc",
        "data": [
            {
                "close": "2000.0",
                "high": "2001.0",
                "low": "1999.0",
                "open": "2000.5",
                "timestamp": "1672531200.123456",
                "volume": "50.0"
            }
        ],
        "params": {"interval": 1}, # Corresponds to 1m
        "symbol": "ETH/USD",
        "type": "snapshot"
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert "ETHUSD" in ws_client.ohlc_cache
    # The key format needs to be determined from the implementation, assuming `1m`
    assert "1m" in ws_client.ohlc_cache["ETHUSD"]
    assert ws_client.ohlc_cache["ETHUSD"]["1m"]["open"] == "2000.5"
    assert ws_client.last_ohlc_update_ts["ETHUSD"]["1m"] > 0

def test_handle_message_unknown_symbol(ws_client: KrakenWSClientV2, caplog):
    """Tests that a message for an unknown symbol is logged and ignored."""
    message = {
        "channel": "ticker",
        "data": [{"ask": "1.0", "bid": "0.9"}],
        "symbol": "DOGE/USD", # Not in our mock_pairs
        "type": "snapshot"
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert not ws_client.ticker_cache.get("DOGEUSD")
    assert "Received data for unknown ws_symbol: DOGE/USD" in caplog.text


def test_subscription_acknowledgment(ws_client: KrakenWSClientV2, caplog):
    """Tests that subscription acknowledgments are stored and logged."""
    caplog.set_level("INFO")
    message = {
        "event": "subscriptionStatus",
        "channel": "ticker",
        "symbol": "XBT/USD",
        "status": "subscribed",
        "req_id": 1,
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert ws_client.subscription_status["XBTUSD"]["ticker"]["status"] == "subscribed"
    assert "Subscribed to ticker for XBT/USD" in caplog.text


def test_subscription_failure(ws_client: KrakenWSClientV2, caplog):
    """Tests that subscription failures are captured and logged."""
    caplog.set_level("ERROR")
    message = {
        "event": "subscriptionStatus",
        "channel": "ohlc",
        "symbol": "ETH/USD",
        "status": "error",
        "errorMessage": "Invalid pair",
        "req_id": 2,
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    status_record = ws_client.subscription_status["ETHUSD"]["ohlc"]
    assert status_record["status"] == "error"
    assert status_record["message"] == "Invalid pair"
    assert "failed: Invalid pair" in caplog.text
