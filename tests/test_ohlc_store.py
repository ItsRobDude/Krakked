# tests/test_ohlc_store.py

import pytest
from pathlib import Path
import appdirs  # type: ignore[import-untyped]
from kraken_bot.config import MarketDataConfig, OHLCBar
from kraken_bot.market_data.ohlc_store import FileOHLCStore

@pytest.fixture
def mock_market_data_config(tmp_path: Path) -> MarketDataConfig:
    """Provides a MarketDataConfig pointing to a temporary directory for testing."""
    return MarketDataConfig(
        ws={},
        ohlc_store={"root_dir": str(tmp_path), "backend": "parquet"},
        backfill_timeframes=[],
        ws_timeframes=[]
    )

@pytest.fixture
def sample_bars() -> list[OHLCBar]:
    """Provides a list of sample OHLC bars for testing."""
    return [
        OHLCBar(1672531200, 100, 105, 99, 101, 1000), # 2023-01-01 00:00:00
        OHLCBar(1672531260, 101, 106, 100, 102, 1100), # 2023-01-01 00:01:00
        OHLCBar(1672531320, 102, 107, 101, 103, 1200), # 2023-01-01 00:02:00
        OHLCBar(1672531380, 103, 108, 102, 104, 1300), # 2023-01-01 00:03:00
    ]

def test_file_ohlc_store_init(mock_market_data_config: MarketDataConfig):
    """Tests that the store initializes correctly and creates the root directory."""
    store = FileOHLCStore(mock_market_data_config)
    expected_root = Path(mock_market_data_config.ohlc_store["root_dir"])

    assert store.root_dir == expected_root
    assert expected_root.exists()

def test_append_and_get_bars(mock_market_data_config: MarketDataConfig, sample_bars: list[OHLCBar]):
    """Tests appending bars and retrieving them with a lookback."""
    store = FileOHLCStore(mock_market_data_config)
    pair = "XBTUSD"
    timeframe = "1m"

    store.append_bars(pair, timeframe, sample_bars)

    # Test lookback
    retrieved_bars = store.get_bars(pair, timeframe, lookback=2)
    assert len(retrieved_bars) == 2
    assert retrieved_bars[0].timestamp == 1672531320
    assert retrieved_bars[1].timestamp == 1672531380

    # Test getting all bars
    all_bars = store.get_bars(pair, timeframe, lookback=10)
    assert len(all_bars) == 4

def test_append_deduplication(mock_market_data_config: MarketDataConfig, sample_bars: list[OHLCBar]):
    """Tests that appending overlapping data does not create duplicates."""
    store = FileOHLCStore(mock_market_data_config)
    pair = "XBTUSD"
    timeframe = "1m"

    # Append the first 3 bars
    store.append_bars(pair, timeframe, sample_bars[:3])

    # Append the last 3 bars (overlapping)
    store.append_bars(pair, timeframe, sample_bars[1:])

    all_bars = store.get_bars(pair, timeframe, lookback=10)
    assert len(all_bars) == 4 # Should still be 4 unique bars
    assert [bar.timestamp for bar in all_bars] == [1672531200, 1672531260, 1672531320, 1672531380]

def test_get_bars_since(mock_market_data_config: MarketDataConfig, sample_bars: list[OHLCBar]):
    """Tests retrieving bars since a specific timestamp."""
    store = FileOHLCStore(mock_market_data_config)
    pair = "XBTUSD"
    timeframe = "1m"
    store.append_bars(pair, timeframe, sample_bars)

    since_ts = 1672531300 # Retrieve bars from 00:02:00 onwards
    retrieved_bars = store.get_bars_since(pair, timeframe, since_ts=since_ts)

    assert len(retrieved_bars) == 2
    assert retrieved_bars[0].timestamp == 1672531320
    assert retrieved_bars[1].timestamp == 1672531380

def test_get_bars_non_existent(mock_market_data_config: MarketDataConfig):
    """Tests that retrieving from a non-existent store returns an empty list."""
    store = FileOHLCStore(mock_market_data_config)
    bars = store.get_bars("NONEXISTENT", "1h", lookback=100)
    assert bars == []


def test_file_ohlc_store_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """FileOHLCStore should fall back to the user data directory when not configured."""
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: tmp_path / "data")

    config = MarketDataConfig(ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[])
    store = FileOHLCStore(config)

    expected_root = tmp_path / "data" / "ohlc"
    assert store.root_dir == expected_root
    assert store.backend == "parquet"
    assert expected_root.exists()
