# src/kraken_bot/config.py

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import appdirs
import yaml

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
    metadata_path: Optional[str] = None


@dataclass
class ExecutionConfig:
    mode: str = "paper"
    default_order_type: str = "limit"
    max_slippage_bps: int = 50
    time_in_force: str = "GTC"
    post_only: bool = False
    validate_only: bool = True
    allow_live_trading: bool = False
    dead_man_switch_seconds: int = 600
    max_retries: int = 3
    retry_backoff_seconds: int = 2
    retry_backoff_factor: float = 2.0
    max_concurrent_orders: int = 10
    min_order_notional_usd: float = 20.0
    max_pair_notional_usd: Optional[float] = None
    max_total_notional_usd: Optional[float] = None

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
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategies: StrategiesConfig = field(default_factory=StrategiesConfig)


def get_config_dir() -> Path:
    """
    Returns the OS-specific configuration directory for the bot using appdirs.
    """
    return Path(appdirs.user_config_dir("kraken_bot"))


def get_default_ohlc_store_config() -> Dict[str, str]:
    """
    Provides a sensible default configuration for the OHLC store, pointing to
    a user-specific data directory with a Parquet backend.
    """
    default_root = Path(appdirs.user_data_dir("kraken_bot")) / "ohlc"
    return {"root_dir": str(default_root), "backend": "parquet"}


def load_config(config_path: Path = None) -> AppConfig:
    """
    Loads the main application configuration from the default location or a specified path.
    """
    logger = logging.getLogger(__name__)

    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    config_path = config_path.expanduser()

    if not config_path.exists():
        logger.warning(
            "Configuration file not found; using defaults",
            extra={"event": "config_missing_file", "config_path": str(config_path)},
        )
        raw_config: Dict[str, Any] = {}
    else:
        with open(config_path, "r") as f:
            raw_config = yaml.safe_load(f) or {}

    if not isinstance(raw_config, dict):
        logger.warning(
            "Configuration file is not a mapping; falling back to defaults",
            extra={"event": "config_invalid_format", "config_path": str(config_path)},
        )
        raw_config = {}

    default_region = RegionProfile(
        code="US_CA",
        capabilities=RegionCapabilities(
            supports_margin=False,
            supports_futures=False,
            supports_staking=False,
        ),
        default_quote="USD",
    )

    region_data = raw_config.get("region") or {}
    if not isinstance(region_data, dict):
        logger.warning(
            "Region config is not a mapping; using default region profile",
            extra={"event": "config_invalid_region", "config_path": str(config_path)},
        )
        region_data = {}

    capabilities_data = region_data.get("capabilities") or {}
    if not isinstance(capabilities_data, dict):
        logger.warning(
            "Region capabilities config is not a mapping; defaulting to conservative capabilities",
            extra={"event": "config_invalid_capabilities", "config_path": str(config_path)},
        )
        capabilities_data = {}

    # Parsing Portfolio Config with defaults
    portfolio_data = raw_config.get("portfolio") or {}
    if not isinstance(portfolio_data, dict):
        logger.warning(
            "Portfolio config is not a mapping; using defaults",
            extra={"event": "config_invalid_portfolio", "config_path": str(config_path)},
        )
        portfolio_data = {}
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
    risk_data = raw_config.get("risk") or {}
    if not isinstance(risk_data, dict):
        logger.warning(
            "Risk config is not a mapping; using defaults",
            extra={"event": "config_invalid_risk", "config_path": str(config_path)},
        )
        risk_data = {}
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
    strategies_data = raw_config.get("strategies") or {}
    if not isinstance(strategies_data, dict):
        logger.warning(
            "Strategies config is not a mapping; using defaults",
            extra={"event": "config_invalid_strategies", "config_path": str(config_path)},
        )
        strategies_data = {}
    strategy_configs = {}

    # Process 'configs' section
    raw_strategy_configs = strategies_data.get("configs") or {}
    if not isinstance(raw_strategy_configs, dict):
        logger.warning(
            "Strategy configs section is not a mapping; skipping strategy-specific configs",
            extra={"event": "config_invalid_strategy_configs", "config_path": str(config_path)},
        )
        raw_strategy_configs = {}
    for name, cfg in raw_strategy_configs.items():
        if not isinstance(cfg, dict):
            logger.warning(
                "Strategy config is not a mapping; skipping entry",
                extra={
                    "event": "config_invalid_strategy_entry",
                    "config_path": str(config_path),
                    "strategy": name,
                },
            )
            continue
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

    universe_data = raw_config.get("universe") or {}
    if not isinstance(universe_data, dict):
        logger.warning(
            "Universe config is not a mapping; using defaults",
            extra={"event": "config_invalid_universe", "config_path": str(config_path)},
        )
        universe_data = {}

    market_data = raw_config.get("market_data") or {}
    if not isinstance(market_data, dict):
        logger.warning(
            "Market data config is not a mapping; using defaults",
            extra={"event": "config_invalid_market_data", "config_path": str(config_path)},
        )
        market_data = {}

    default_ohlc_store = get_default_ohlc_store_config()
    ohlc_store_config = market_data.get("ohlc_store", {}) or {}
    if not isinstance(ohlc_store_config, dict):
        logger.warning(
            "OHLC store config is not a mapping; using defaults",
            extra={"event": "config_invalid_ohlc_store", "config_path": str(config_path)},
        )
        ohlc_store_config = {}
    merged_ohlc_store = {**default_ohlc_store, **ohlc_store_config}

    execution_data = raw_config.get("execution") or {}
    if not isinstance(execution_data, dict):
        logger.warning(
            "Execution config is not a mapping; using defaults",
            extra={"event": "config_invalid_execution", "config_path": str(config_path)},
        )
        execution_data = {}

    default_execution = ExecutionConfig()
    execution_mode = execution_data.get("mode", default_execution.mode)
    validate_only = execution_data.get("validate_only")
    if validate_only is None:
        validate_only = execution_mode != "live"

    execution_config = ExecutionConfig(
        mode=execution_mode,
        default_order_type=execution_data.get(
            "default_order_type", default_execution.default_order_type
        ),
        max_slippage_bps=execution_data.get(
            "max_slippage_bps", default_execution.max_slippage_bps
        ),
        time_in_force=execution_data.get("time_in_force", default_execution.time_in_force),
        post_only=execution_data.get("post_only", default_execution.post_only),
        validate_only=validate_only,
        allow_live_trading=execution_data.get(
            "allow_live_trading", default_execution.allow_live_trading
        ),
        dead_man_switch_seconds=execution_data.get(
            "dead_man_switch_seconds", default_execution.dead_man_switch_seconds
        ),
        max_retries=execution_data.get("max_retries", default_execution.max_retries),
        retry_backoff_seconds=execution_data.get(
            "retry_backoff_seconds", default_execution.retry_backoff_seconds
        ),
        retry_backoff_factor=execution_data.get(
            "retry_backoff_factor", default_execution.retry_backoff_factor
        ),
        max_concurrent_orders=execution_data.get(
            "max_concurrent_orders", default_execution.max_concurrent_orders
        ),
        min_order_notional_usd=execution_data.get(
            "min_order_notional_usd", default_execution.min_order_notional_usd
        ),
    )

    return AppConfig(
        region=RegionProfile(
            code=region_data.get("code", default_region.code),
            capabilities=RegionCapabilities(
                supports_margin=capabilities_data.get("supports_margin", default_region.capabilities.supports_margin),
                supports_futures=capabilities_data.get("supports_futures", default_region.capabilities.supports_futures),
                supports_staking=capabilities_data.get("supports_staking", default_region.capabilities.supports_staking),
            ),
            default_quote=region_data.get("default_quote", default_region.default_quote),
        ),
        universe=UniverseConfig(
            include_pairs=universe_data.get("include_pairs", []),
            exclude_pairs=universe_data.get("exclude_pairs", []),
            min_24h_volume_usd=universe_data.get("min_24h_volume_usd", 0.0),
        ),
        market_data=MarketDataConfig(
            ws=market_data.get("ws", {}),
            ohlc_store=merged_ohlc_store,
            backfill_timeframes=market_data.get("backfill_timeframes", ["1d", "4h", "1h"]),
            ws_timeframes=market_data.get("ws_timeframes", ["1m"]),
            metadata_path=market_data.get("metadata_path"),
        ),
        portfolio=portfolio_config,
        execution=execution_config,
        risk=risk_config,
        strategies=strategies_config,
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
    min_order_size: float
    status: str
    liquidity_24h_usd: float | None = None

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
    subscription_errors: int
