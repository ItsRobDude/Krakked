# tests/test_ws_handling.py

import asyncio
import json

import pytest

from krakked.config import PairMetadata
from krakked.market_data.ws_client import KrakenWSClientV2


@pytest.fixture
def mock_pairs() -> list[PairMetadata]:
    """Provides a mock list of PairMetadata for testing."""
    return [
        PairMetadata(
            "XBTUSD",
            "XBT",
            "USD",
            "XBTUSD",
            "XBT/USD",
            "XXBTZUSD",
            8,
            8,
            1.0,
            0.0,
            "online",
        ),
        PairMetadata(
            "ETHUSD",
            "ETH",
            "USD",
            "ETHUSD",
            "ETH/USD",
            "XETHZUSD",
            8,
            8,
            1.0,
            0.0,
            "online",
        ),
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
                "symbol": "XBT/USD",
                "ask": "60000.0",
                "bid": "59999.0",
                "last": "60000.0",
                "volume": "100.0",
            }
        ],
        "type": "snapshot",
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
                "open": "2000.5",
                "high": "2001.0",
                "low": "1999.0",
                "close": "2000.0",
                "symbol": "ETH/USD",
                "interval": 1,
                "interval_begin": "2026-04-19T23:03:00.000000000Z",
                "timestamp": "1672531200.123456",
                "volume": "50.0",
            }
        ],
        "type": "snapshot",
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
        "symbol": "DOGE/USD",  # Not in our mock_pairs
        "type": "snapshot",
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert not ws_client.ticker_cache.get("DOGEUSD")
    assert "Received data for unknown ws_symbol: DOGE/USD" in caplog.text


def test_handle_ticker_message_accepts_btc_alias_for_xbt_pair(
    ws_client: KrakenWSClientV2,
):
    message = {
        "channel": "ticker",
        "data": [
            {
                "symbol": "BTC/USD",
                "ask": "60000.0",
                "bid": "59999.0",
                "last": "60000.0",
                "volume": "100.0",
            }
        ],
        "type": "snapshot",
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert ws_client.ticker_cache["XBTUSD"]["last"] == "60000.0"


def test_subscription_acknowledgment(ws_client: KrakenWSClientV2, caplog):
    """Tests that subscription acknowledgments are stored and logged."""
    caplog.set_level("INFO")
    message = {
        "method": "subscribe",
        "success": True,
        "req_id": 1,
        "result": {"channel": "ticker", "symbol": "XBT/USD", "snapshot": True},
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert ws_client.subscription_status["XBTUSD"]["ticker"]["status"] == "subscribed"
    assert "Subscribed to ticker for XBT/USD" in caplog.text


def test_subscription_failure(ws_client: KrakenWSClientV2, caplog):
    """Tests that subscription failures are captured and logged."""
    caplog.set_level("ERROR")
    message = {
        "method": "subscribe",
        "success": False,
        "req_id": 2,
        "error": "Invalid pair",
        "result": {"channel": "ohlc", "symbol": "ETH/USD", "interval": 1},
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    status_record = ws_client.subscription_status["ETHUSD"]["ohlc"]
    assert status_record["status"] == "error"
    assert status_record["message"] == "Invalid pair"
    assert "failed: Invalid pair" in caplog.text


def test_subscription_failure_uses_pending_request_context(
    ws_client: KrakenWSClientV2, caplog
):
    caplog.set_level("ERROR")
    ws_client._pending_subscriptions[99] = {
        "channel": "ohlc",
        "symbol": ["BTC/USD"],
        "interval": 5,
    }
    message = {
        "method": "subscribe",
        "success": False,
        "req_id": 99,
        "error": "Already subscribed to one ohlc interval on this symbol",
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    status_record = ws_client.subscription_status["XBTUSD"]["ohlc"]
    assert status_record["status"] == "error"
    assert status_record["message"] == "Already subscribed to one ohlc interval on this symbol"
    assert "BTC/USD" in caplog.text


def test_client_uses_single_live_ohlc_interval_per_connection(
    ws_client: KrakenWSClientV2,
):
    ws_client_multi = KrakenWSClientV2(pairs=ws_client._pairs, timeframes=["1m", "5m"])

    assert ws_client_multi._live_ohlc_timeframes == ["1m"]


def test_channel_message_without_symbol_is_ignored(ws_client: KrakenWSClientV2):
    message = {
        "channel": "ticker",
        "data": [{"ask": "60000.0", "bid": "59999.0"}],
        "type": "snapshot",
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert ws_client.ticker_cache == {}


def test_channel_message_without_data_is_ignored(ws_client: KrakenWSClientV2):
    message = {
        "channel": "ticker",
        "symbol": "XBT/USD",
        "type": "snapshot",
    }

    asyncio.run(ws_client._handle_message(json.dumps(message)))

    assert ws_client.ticker_cache == {}


def test_heartbeat_message_is_ignored(ws_client: KrakenWSClientV2):
    asyncio.run(ws_client._handle_message(json.dumps({"channel": "heartbeat"})))

    assert ws_client.ticker_cache == {}
    assert ws_client.ohlc_cache == {}
