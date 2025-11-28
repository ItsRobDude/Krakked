# src/kraken_bot/config.py

from dataclasses import dataclass, field
import yaml
import os
from typing import Dict, List, Optional
from pathlib import Path
import appdirs

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
class PortfolioConfig:
    base_currency: str = "USD"
    valuation_pairs: Dict[str, str] = field(default_factory=dict)
    include_assets: List[str] = field(default_factory=list)
    exclude_assets: List[str] = field(default_factory=list)
    cost_basis_method: str = "wac"
    track_manual_trades: bool = True
    snapshot_retention_days: int = 30
    reconciliation_tolerance: float = 1.0

@dataclass
class AppConfig:
    region: RegionProfile
    universe: UniverseConfig
    market_data: MarketDataConfig
    portfolio: PortfolioConfig


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

    # Parsing Portfolio Config with defaults
    portfolio_data = raw_config.get("portfolio", {})
    portfolio_config = PortfolioConfig(
        base_currency=portfolio_data.get("base_currency", "USD"),
        valuation_pairs=portfolio_data.get("valuation_pairs", {}),
        include_assets=portfolio_data.get("include_assets", []),
        exclude_assets=portfolio_data.get("exclude_assets", []),
        cost_basis_method=portfolio_data.get("cost_basis_method", "wac"),
        track_manual_trades=portfolio_data.get("track_manual_trades", True),
        snapshot_retention_days=portfolio_data.get("snapshot_retention_days", 30),
        reconciliation_tolerance=portfolio_data.get("reconciliation_tolerance", 1.0)
    )

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
        ),
        portfolio=portfolio_config
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
