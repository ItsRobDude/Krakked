# src/kraken_bot/config.py

from dataclasses import dataclass, field
import yaml
import os
from typing import Dict, List, Optional, Any
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
class RiskConfig:
    max_risk_per_trade_pct: float = 1.0
    max_portfolio_risk_pct: float = 10.0
    max_open_positions: int = 10
    max_per_asset_pct: float = 5.0
    max_per_strategy_pct: Dict[str, float] = field(default_factory=dict)
    max_daily_drawdown_pct: float = 10.0
    kill_switch_on_drift: bool = True
    include_manual_positions: bool = True
    volatility_lookback_bars: int = 20
    min_liquidity_24h_usd: float = 100000.0

@dataclass
class StrategyConfig:
    name: str
    type: str
    enabled: bool
    # Generic parameter dict that specific strategies will parse into typed configs
    params: Dict[str, Any] = field(default_factory=dict)
    # Explicit userref to ensure consistent PnL tracking
    userref: Optional[int] = None

@dataclass
class StrategiesConfig:
    enabled: List[str] = field(default_factory=list)
    configs: Dict[str, StrategyConfig] = field(default_factory=dict)

@dataclass
class AppConfig:
    region: RegionProfile
    universe: UniverseConfig
    market_data: MarketDataConfig
    portfolio: PortfolioConfig
    risk: RiskConfig
    strategies: StrategiesConfig


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

    # Parsing Risk Config with defaults
    risk_data = raw_config.get("risk", {})
    risk_config = RiskConfig(
        max_risk_per_trade_pct=risk_data.get("max_risk_per_trade_pct", 1.0),
        max_portfolio_risk_pct=risk_data.get("max_portfolio_risk_pct", 10.0),
        max_open_positions=risk_data.get("max_open_positions", 10),
        max_per_asset_pct=risk_data.get("max_per_asset_pct", 5.0),
        max_per_strategy_pct=risk_data.get("max_per_strategy_pct", {}),
        max_daily_drawdown_pct=risk_data.get("max_daily_drawdown_pct", 10.0),
        kill_switch_on_drift=risk_data.get("kill_switch_on_drift", True),
        include_manual_positions=risk_data.get("include_manual_positions", True),
        volatility_lookback_bars=risk_data.get("volatility_lookback_bars", 20),
        min_liquidity_24h_usd=risk_data.get("min_liquidity_24h_usd", 100000.0)
    )

    # Parsing Strategies Config
    strategies_data = raw_config.get("strategies", {})
    strategy_configs = {}

    # Process 'configs' section
    raw_strategy_configs = strategies_data.get("configs", {})
    for name, cfg in raw_strategy_configs.items():
        # Copy cfg to avoid modifying the original dictionary during pop
        cfg_copy = cfg.copy()

        # Extract known fields
        s_type = cfg_copy.pop("type", "unknown")
        # In the config file, 'enabled' might be on the specific strategy config
        # or just inferred from the global enabled list. We'll support both, defaulting to True here
        # and checking the global list separately if needed, or assume the global list drives execution.
        # But strictly speaking, the global 'enabled' list in StrategiesConfig is the driver.
        # We'll just load the 'enabled' flag if present in the specific config too.
        s_enabled = cfg_copy.pop("enabled", True)
        userref = cfg_copy.pop("userref", None)

        # The rest are params
        params = cfg_copy

        strategy_configs[name] = StrategyConfig(
            name=name,
            type=s_type,
            enabled=s_enabled,
            userref=userref,
            params=params
        )

    strategies_config = StrategiesConfig(
        enabled=strategies_data.get("enabled", []),
        configs=strategy_configs
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
        portfolio=portfolio_config,
        risk=risk_config,
        strategies=strategies_config
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
