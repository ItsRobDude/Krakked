# src/kraken_bot/market_data/ohlc_store.py

from typing import Protocol, List
from pathlib import Path
import pandas as pd
import logging
from kraken_bot.config import OHLCBar, MarketDataConfig, get_default_ohlc_store_config

logger = logging.getLogger(__name__)

class OHLCStore(Protocol):
    """
    An interface for a time-series data store for OHLC bars.
    """
    def append_bars(self, pair: str, timeframe: str, bars: List[OHLCBar]) -> None:
        ...

    def get_bars(self, pair: str, timeframe: str, lookback: int) -> List[OHLCBar]:
        ...

    def get_bars_since(self, pair: str, timeframe: str, since_ts: int) -> List[OHLCBar]:
        ...

class FileOHLCStore:
    """
    A file-based implementation of OHLCStore that saves data in Parquet format.

    The directory structure is: <root_dir>/<timeframe>/<pair>.parquet
    """
    def __init__(self, config: MarketDataConfig):
        default_config = get_default_ohlc_store_config()
        user_config = config.ohlc_store or {}
        if not isinstance(user_config, dict):
            logger.warning("Invalid OHLC store config; using defaults")
            user_config = {}

        store_config = {**default_config, **user_config}

        if "root_dir" not in store_config:
            raise ValueError("FileOHLCStore requires a 'root_dir' in the market_data.ohlc_store config.")

        self.root_dir = Path(store_config["root_dir"]).expanduser()
        self.backend = store_config.get("backend", "parquet")

        self.root_dir.mkdir(parents=True, exist_ok=True)

        if self.backend not in ["parquet"]:
            raise ValueError(f"Unsupported file backend: {self.backend}. Only 'parquet' is supported.")

        logger.info(f"Initialized FileOHLCStore with root directory: {self.root_dir}")

    def _get_file_path(self, pair: str, timeframe: str) -> Path:
        """Constructs the file path for a given pair and timeframe."""
        directory = self.root_dir / timeframe
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{pair}.{self.backend}"

    def append_bars(self, pair: str, timeframe: str, bars: List[OHLCBar]) -> None:
        """Appends new OHLC bars to the store, ensuring no duplicates."""
        if not bars:
            return

        file_path = self._get_file_path(pair, timeframe)
        new_df = pd.DataFrame([bar.__dict__ for bar in bars])
        new_df = new_df.set_index("timestamp")

        if file_path.exists():
            try:
                existing_df = pd.read_parquet(file_path)
                combined_df = pd.concat([existing_df, new_df])
                # Remove duplicates, keeping the last entry
                combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
                combined_df.to_parquet(file_path)
                logger.debug(f"Appended {len(new_df)} bars to {file_path}")
            except Exception as e:
                logger.error(f"Error reading or writing to {file_path}: {e}")
        else:
            new_df.to_parquet(file_path)
            logger.info(f"Created new OHLC store at {file_path} with {len(new_df)} bars.")

    def get_bars(self, pair: str, timeframe: str, lookback: int) -> List[OHLCBar]:
        """Retrieves the last N (lookback) bars from the store."""
        file_path = self._get_file_path(pair, timeframe)
        if not file_path.exists():
            return []

        try:
            df = pd.read_parquet(file_path)
            # Ensure the data is sorted by timestamp before taking the last N rows
            df = df.sort_index().tail(lookback)
            return [OHLCBar(**row) for row in df.reset_index().to_dict('records')]
        except Exception as e:
            logger.error(f"Error reading from {file_path}: {e}")
            return []

    def get_bars_since(self, pair: str, timeframe: str, since_ts: int) -> List[OHLCBar]:
        """Retrieves all bars since a given timestamp."""
        file_path = self._get_file_path(pair, timeframe)
        if not file_path.exists():
            return []

        try:
            df = pd.read_parquet(file_path)
            df = df[df.index >= since_ts].sort_index()
            return [OHLCBar(**row) for row in df.reset_index().to_dict('records')]
        except Exception as e:
            logger.error(f"Error reading from {file_path}: {e}")
            return []
