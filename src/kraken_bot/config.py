# src/kraken_bot/config.py

from dataclasses import dataclass
import yaml
import os
from pathlib import Path

@dataclass
class RegionCapabilities:
    supports_margin: bool
    supports_futures: bool
    supports_staking: bool

@dataclass
class RegionProfile:
    code: str
    capabilities: RegionCapabilities
    default_quote: str = "USD"

@dataclass
class UniverseConfig:
    include_pairs: list[str]
    exclude_pairs: list[str]
    min_24h_volume_usd: float

@dataclass
class MarketDataConfig:
    ws: dict
    ohlc_store: dict
    backfill_timeframes: list[str]
    ws_timeframes: list[str]

@dataclass
class AppConfig:
    region: RegionProfile
    universe: UniverseConfig
    market_data: MarketDataConfig


import appdirs

def get_config_dir() -> Path:
    """
    Returns the OS-specific configuration directory for the bot using appdirs.
    """
    return Path(appdirs.user_config_dir("kraken_bot"))


def load_config(config_path: Path = None) -> AppConfig:
    """
    Loads the main application configuration from the default location or a specified path.
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    config_path = config_path.expanduser()

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found at {config_path}")

    with open(config_path, "r") as f:
        raw_config = yaml.safe_load(f)

    return AppConfig(
        region=RegionProfile(
            code=raw_config["region"]["code"],
            capabilities=RegionCapabilities(**raw_config["region"]["capabilities"]),
            default_quote=raw_config["region"]["default_quote"]
        ),
        universe=UniverseConfig(
            include_pairs=raw_config.get("universe", {}).get("include_pairs", []),
            exclude_pairs=raw_config.get("universe", {}).get("exclude_pairs", []),
            min_24h_volume_usd=raw_config.get("universe", {}).get("min_24h_volume_usd", 0.0)
        ),
        market_data=MarketDataConfig(
            ws=raw_config.get("market_data", {}).get("ws", {}),
            ohlc_store=raw_config.get("market_data", {}).get("ohlc_store", {}),
            backfill_timeframes=raw_config.get("market_data", {}).get("backfill_timeframes", ["1d", "4h", "1h"]),
            ws_timeframes=raw_config.get("market_data", {}).get("ws_timeframes", ["1m"])
        )
    )

@dataclass
class PairMetadata:
    canonical: str
    base: str
    quote: str
    rest_symbol: str
    ws_symbol: str
    raw_name: str
    price_decimals: int
    volume_decimals: int
    lot_size: float
    status: str
    min_order_size: float

@dataclass
class OHLCBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class ConnectionStatus:
    rest_api_reachable: bool
    websocket_connected: bool
    streaming_pairs: int
    stale_pairs: int
