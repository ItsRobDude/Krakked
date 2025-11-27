# tests/test_ohlc_fetcher.py

import pytest
from unittest.mock import MagicMock, call
from kraken_bot.config import PairMetadata
from kraken_bot.market_data.ohlc_fetcher import backfill_ohlc

@pytest.fixture
def mock_pair_metadata() -> PairMetadata:
    """Provides a mock PairMetadata object for testing."""
    return PairMetadata(
        canonical="XBTUSD", base="XBT", quote="USD", rest_symbol="XBTUSD",
        ws_symbol="XBT/USD", raw_name="XXBTZUSD", price_decimals=1,
        volume_decimals=8, lot_size=1.0, status="online", min_order_size=0.0001
    )

def test_backfill_ohlc_pagination(mock_pair_metadata: PairMetadata):
    """
    Tests that the backfill_ohlc function correctly handles pagination
    when the initial `since` value is None.
    """
    mock_client = MagicMock()
    mock_store = MagicMock()

    # Simulate a multi-page response from the Kraken API
    # Page 1: 2 bars, last timestamp is 1000
    page1_response = {
        "XXBTZUSD": [
            [940, 1, 1, 1, 1, 1, 1],
            [1000, 2, 2, 2, 2, 2, 2],
            [1060, 3, 3, 3, 3, 3, 3] # Running candle, should be ignored
        ],
        "last": 1000
    }
    # Page 2: 2 bars, last timestamp is 1120
    page2_response = {
        "XXBTZUSD": [
            [1060, 3, 3, 3, 3, 3, 3],
            [1120, 4, 4, 4, 4, 4, 4],
            [1180, 5, 5, 5, 5, 5, 5] # Running candle
        ],
        "last": 1120
    }
    # Page 3: 1 bar, but 'last' is null, which should terminate the loop gracefully
    page3_response = {
        "XXBTZUSD": [
            [1180, 5, 5, 5, 5, 5, 5],
            [1240, 6, 6, 6, 6, 6, 6] # Running candle
        ],
        "last": None
    }

    # Page 4: Should not be requested
    page4_response = {"XXBTZUSD": [], "last": 1240}


    mock_client.get_public.side_effect = [page1_response, page2_response, page3_response, page4_response]

    # Call the function with since=None, the default behavior
    count = backfill_ohlc(
        pair_metadata=mock_pair_metadata,
        timeframe="1m",
        since=None,
        client=mock_client,
        store=mock_store
    )

    # 1. Verify the total number of bars fetched is correct (2 from page 1 + 2 from page 2 + 1 from page 3)
    assert count == 5

    # 2. Verify the REST client was called correctly for each page (and not for page 4)
    assert mock_client.get_public.call_count == 3
    mock_client.get_public.assert_has_calls([
        call("OHLC", {"pair": "XBTUSD", "interval": 1}),
        call("OHLC", {"pair": "XBTUSD", "interval": 1, "since": 1000}),
        call("OHLC", {"pair": "XBTUSD", "interval": 1, "since": 1120}),
    ])

    # 3. Verify the data was stored three times with the correct content
    assert mock_store.append_bars.call_count == 3

    # Check the content of each call to append_bars
    first_call_args = mock_store.append_bars.call_args_list[0][0]
    second_call_args = mock_store.append_bars.call_args_list[1][0]
    third_call_args = mock_store.append_bars.call_args_list[2][0]

    # Page 1 should have 2 bars
    assert len(first_call_args[2]) == 2
    assert first_call_args[2][0].timestamp == 940
    assert first_call_args[2][1].timestamp == 1000

    # Page 2 should have 2 new bars
    assert len(second_call_args[2]) == 2
    assert second_call_args[2][0].timestamp == 1060
    assert second_call_args[2][1].timestamp == 1120

    # Page 3 should have 1 new bar
    assert len(third_call_args[2]) == 1
    assert third_call_args[2][0].timestamp == 1180

def test_backfill_first_page_no_since(mock_pair_metadata: PairMetadata):
    """
    Tests that a single page fetch works correctly when since=None.
    This validates against the `last_ts > since` TypeError.
    """
    mock_client = MagicMock()
    mock_store = MagicMock()

    response = {
        "XXBTZUSD": [[1000, 1, 1, 1, 1, 1, 1], [1060, 2, 2, 2, 2, 2, 2]],
        "last": 1060
    }
    mock_client.get_public.side_effect = [response, {"XXBTZUSD": [], "last": 1060}]

    count = backfill_ohlc(
        pair_metadata=mock_pair_metadata, timeframe="1m", since=None,
        client=mock_client, store=mock_store
    )

    assert count == 1 # 1 closed bar
    assert mock_client.get_public.call_count == 2
    mock_store.append_bars.assert_called_once()

def test_backfill_first_page_null_last(mock_pair_metadata: PairMetadata):
    """
    Tests that a single page fetch with last=None works correctly.
    This validates against the `int(None)` TypeError.
    """
    mock_client = MagicMock()
    mock_store = MagicMock()

    response = {
        "XXBTZUSD": [[1000, 1, 1, 1, 1, 1, 1], [1060, 2, 2, 2, 2, 2, 2]],
        "last": None
    }
    mock_client.get_public.return_value = response

    count = backfill_ohlc(
        pair_metadata=mock_pair_metadata, timeframe="1m", since=None,
        client=mock_client, store=mock_store
    )

    assert count == 1 # 1 closed bar
    mock_client.get_public.assert_called_once_with("OHLC", {"pair": "XBTUSD", "interval": 1})
    mock_store.append_bars.assert_called_once()
