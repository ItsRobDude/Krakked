# tests/test_ohlc_store.py

import threading
from pathlib import Path
from typing import Generator

import appdirs  # type: ignore[import-untyped]
import pytest

from krakked.config import MarketDataConfig, OHLCBar
from krakked.market_data.ohlc_store import FileOHLCStore


@pytest.fixture
def mock_market_data_config(tmp_path: Path) -> MarketDataConfig:
    """Provides a MarketDataConfig pointing to a temporary directory for testing."""
    return MarketDataConfig(
        ws={},
        ohlc_store={"root_dir": str(tmp_path), "backend": "parquet"},
        backfill_timeframes=[],
        ws_timeframes=[],
    )


@pytest.fixture
def store(
    mock_market_data_config: MarketDataConfig,
) -> Generator[FileOHLCStore, None, None]:
    """Provides a FileOHLCStore instance and ensures shutdown."""
    s = FileOHLCStore(mock_market_data_config)
    yield s
    s.shutdown()


@pytest.fixture
def sample_bars() -> list[OHLCBar]:
    """Provides a list of sample OHLC bars for testing."""
    return [
        OHLCBar(1672531200, 100, 105, 99, 101, 1000),  # 2023-01-01 00:00:00
        OHLCBar(1672531260, 101, 106, 100, 102, 1100),  # 2023-01-01 00:01:00
        OHLCBar(1672531320, 102, 107, 101, 103, 1200),  # 2023-01-01 00:02:00
        OHLCBar(1672531380, 103, 108, 102, 104, 1300),  # 2023-01-01 00:03:00
    ]


def test_file_ohlc_store_init(
    store: FileOHLCStore, mock_market_data_config: MarketDataConfig
):
    """Tests that the store initializes correctly and creates the root directory."""
    assert store.root_dir == Path(mock_market_data_config.ohlc_store["root_dir"])
    assert Path(mock_market_data_config.ohlc_store["root_dir"]).exists()


def test_append_and_get_bars(store: FileOHLCStore, sample_bars: list[OHLCBar]):
    """Tests appending bars and retrieving them with a lookback."""
    pair = "XBTUSD"
    timeframe = "1m"

    store.append_bars(pair, timeframe, sample_bars)

    # Wait for async write to complete
    store._write_queue.join()

    # Test lookback
    retrieved_bars = store.get_bars(pair, timeframe, lookback=2)
    assert len(retrieved_bars) == 2
    assert retrieved_bars[0].timestamp == 1672531320
    assert retrieved_bars[1].timestamp == 1672531380

    # Test getting all bars
    all_bars = store.get_bars(pair, timeframe, lookback=10)
    assert len(all_bars) == 4


def test_append_deduplication(store: FileOHLCStore, sample_bars: list[OHLCBar]):
    """Tests that appending overlapping data does not create duplicates."""
    pair = "XBTUSD"
    timeframe = "1m"

    # Append the first 3 bars
    store.append_bars(pair, timeframe, sample_bars[:3])
    store._write_queue.join()

    # Append the last 3 bars (overlapping)
    store.append_bars(pair, timeframe, sample_bars[1:])
    store._write_queue.join()

    all_bars = store.get_bars(pair, timeframe, lookback=10)
    assert len(all_bars) == 4  # Should still be 4 unique bars
    assert [bar.timestamp for bar in all_bars] == [
        1672531200,
        1672531260,
        1672531320,
        1672531380,
    ]


def test_get_bars_since(store: FileOHLCStore, sample_bars: list[OHLCBar]):
    """Tests retrieving bars since a specific timestamp."""
    pair = "XBTUSD"
    timeframe = "1m"
    store.append_bars(pair, timeframe, sample_bars)
    store._write_queue.join()

    since_ts = 1672531300  # Retrieve bars from 00:02:00 onwards
    retrieved_bars = store.get_bars_since(pair, timeframe, since_ts=since_ts)

    assert len(retrieved_bars) == 2
    assert retrieved_bars[0].timestamp == 1672531320
    assert retrieved_bars[1].timestamp == 1672531380


def test_get_bars_non_existent(store: FileOHLCStore):
    """Tests that retrieving from a non-existent store returns an empty list."""
    bars = store.get_bars("NONEXISTENT", "1h", lookback=100)
    assert bars == []


def test_shutdown_drains_queued_writes(
    mock_market_data_config: MarketDataConfig,
) -> None:
    store = FileOHLCStore(mock_market_data_config)
    original_persist = store._persist_bars
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    persist_calls: list[int] = []

    def _delayed_persist(pair: str, timeframe: str, bars: list[OHLCBar]) -> None:
        persist_calls.append(bars[0].timestamp)
        if len(persist_calls) == 1:
            first_write_started.set()
            assert release_first_write.wait(timeout=2.0)
        original_persist(pair, timeframe, bars)

    store._persist_bars = _delayed_persist  # type: ignore[method-assign]
    store.append_bars("XBTUSD", "1m", [OHLCBar(1000, 1, 1, 1, 1, 1)])
    assert first_write_started.wait(timeout=2.0)
    store.append_bars("XBTUSD", "1m", [OHLCBar(1060, 2, 2, 2, 2, 2)])

    shutdown_thread = threading.Thread(target=store.shutdown)
    shutdown_thread.start()
    release_first_write.set()
    shutdown_thread.join(timeout=2.0)

    assert not shutdown_thread.is_alive()
    assert persist_calls == [1000, 1060]

    reopened = FileOHLCStore(mock_market_data_config)
    try:
        bars = reopened.get_bars("XBTUSD", "1m", lookback=10)
        assert [bar.timestamp for bar in bars] == [1000, 1060]
    finally:
        reopened.shutdown()


def test_file_ohlc_store_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """FileOHLCStore should fall back to the user data directory when not configured."""
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: tmp_path / "data")

    config = MarketDataConfig(
        ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]
    )
    store = FileOHLCStore(config)

    # We must shut down the worker manually here since we don't use the fixture
    try:
        expected_root = tmp_path / "data" / "ohlc"
        assert store.root_dir == expected_root
        assert store.backend == "parquet"
        assert expected_root.exists()
    finally:
        store.shutdown()
