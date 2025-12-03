# src/kraken_bot/config.py

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import appdirs  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from kraken_bot.strategy.catalog import CANONICAL_STRATEGIES


RUNTIME_OVERRIDES_FILENAME = "config.runtime.yaml"


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
    paper_tests_completed: bool = False
    dead_man_switch_seconds: int = 600
    max_retries: int = 3
    retry_backoff_seconds: int = 2
    retry_backoff_factor: float = 2.0
    max_concurrent_orders: int = 10
    min_order_notional_usd: float = 20.0
    max_pair_notional_usd: Optional[float] = None
    max_total_notional_usd: Optional[float] = None


@dataclass
class UIAuthConfig:
    enabled: bool = False
    token: str = ""


@dataclass
class UIRefreshConfig:
    dashboard_ms: int = 5000
    orders_ms: int = 5000
    strategies_ms: int = 10000


@dataclass
class UIConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    base_path: str = "/"
    auth: UIAuthConfig = field(default_factory=UIAuthConfig)
    read_only: bool = False
    refresh_intervals: UIRefreshConfig = field(default_factory=UIRefreshConfig)


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
    db_path: str = "portfolio.db"
    auto_migrate_schema: bool = True


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
    dynamic_allocation_enabled: bool = False
    dynamic_allocation_lookback_hours: int = 72
    min_strategy_weight_pct: float = 0.0
    max_strategy_weight_pct: float = 50.0


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
    ui: UIConfig = field(default_factory=UIConfig)


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


def _load_runtime_overrides(config_dir: Path) -> dict:
    path = config_dir / RUNTIME_OVERRIDES_FILENAME
    if not path.exists():
        return {}
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def dump_runtime_overrides(config: AppConfig, config_dir: Path | None = None) -> None:
    config_dir = config_dir or get_config_dir()
    path = config_dir / RUNTIME_OVERRIDES_FILENAME

    data = {
        "risk": {
            "max_risk_per_trade_pct": config.risk.max_risk_per_trade_pct,
            "max_portfolio_risk_pct": config.risk.max_portfolio_risk_pct,
            "max_per_strategy_pct": config.risk.max_per_strategy_pct,
        },
        "strategies": {
            "enabled": config.strategies.enabled,
            "configs": {
                sid: {"params": cfg.params, "enabled": cfg.enabled}
                for sid, cfg in config.strategies.configs.items()
            },
        },
        "ui": {"refresh_intervals": config.ui.refresh_intervals.__dict__},
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


def load_config(
    config_path: Optional[Path] = None, env: Optional[str] = None
) -> AppConfig:
    """
    Loads the main application configuration from the default location or a specified path.
    """
    logger = logging.getLogger(__name__)

    def _validated_int(
        value: Any, default: int, field_name: str, min_value: int = 1
    ) -> int:
        if isinstance(value, int) and value >= min_value:
            return value

        logger.warning(
            "%s is invalid; using default",
            field_name,
            extra={"event": field_name, "config_path": str(config_path)},
        )
        return default

    allowed_envs = {"dev", "paper", "live"}

    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    config_path = config_path.expanduser()

    initial_env = env if env is not None else os.environ.get("KRAKEN_BOT_ENV")
    if initial_env not in allowed_envs:
        logger.warning(
            "Invalid or missing environment '%s'; defaulting to 'paper'",
            initial_env,
            extra={"event": "config_invalid_env", "config_path": str(config_path)},
        )
        effective_env = "paper"
    else:
        effective_env = initial_env

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

    def _deep_merge_dicts(
        base: Dict[str, Any], overlay: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = base.copy()
        for key, value in overlay.items():
            if (
                key in merged
                and isinstance(merged[key], dict)
                and isinstance(value, dict)
            ):
                merged[key] = _deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    env_config_path = config_path.parent / f"config.{effective_env}.yaml"
    if env_config_path.exists():
        with open(env_config_path, "r") as f:
            env_config = yaml.safe_load(f) or {}

        if not isinstance(env_config, dict):
            logger.warning(
                "Environment config is not a mapping; skipping env overlay",
                extra={
                    "event": "config_invalid_env_file",
                    "config_path": str(env_config_path),
                },
            )
            env_config = {}

        raw_config = _deep_merge_dicts(raw_config, env_config)

    config_dir = get_config_dir()
    runtime_overrides = _load_runtime_overrides(config_dir)
    raw_config = _deep_merge_dicts(raw_config, runtime_overrides)

    default_region = RegionProfile(
        code="US_CA",
        capabilities=RegionCapabilities(
            supports_margin=False,
            supports_futures=False,
            supports_staking=False,
        ),
        default_quote="USD",
    )
    default_ui = UIConfig()

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
            extra={
                "event": "config_invalid_capabilities",
                "config_path": str(config_path),
            },
        )
        capabilities_data = {}

    # Parsing Portfolio Config with defaults
    portfolio_data = raw_config.get("portfolio") or {}
    if not isinstance(portfolio_data, dict):
        logger.warning(
            "Portfolio config is not a mapping; using defaults",
            extra={
                "event": "config_invalid_portfolio",
                "config_path": str(config_path),
            },
        )
        portfolio_data = {}
    default_auto_migrate_schema = effective_env != "live"
    portfolio_config = PortfolioConfig(
        base_currency=portfolio_data.get("base_currency", "USD"),
        valuation_pairs=portfolio_data.get("valuation_pairs", {}),
        include_assets=portfolio_data.get("include_assets", []),
        exclude_assets=portfolio_data.get("exclude_assets", []),
        cost_basis_method=portfolio_data.get("cost_basis_method", "wac"),
        track_manual_trades=portfolio_data.get("track_manual_trades", True),
        snapshot_retention_days=portfolio_data.get("snapshot_retention_days", 30),
        reconciliation_tolerance=portfolio_data.get("reconciliation_tolerance", 1.0),
        db_path=portfolio_data.get("db_path", "portfolio.db"),
        auto_migrate_schema=bool(
            portfolio_data.get("auto_migrate_schema", default_auto_migrate_schema)
        ),
    )

    # Parsing Risk Config with defaults
    risk_data = raw_config.get("risk") or {}
    if not isinstance(risk_data, dict):
        logger.warning(
            "Risk config is not a mapping; using defaults",
            extra={"event": "config_invalid_risk", "config_path": str(config_path)},
        )
        risk_data = {}

    # Parsing Strategies Config
    strategies_data = raw_config.get("strategies") or {}
    if not isinstance(strategies_data, dict):
        logger.warning(
            "Strategies config is not a mapping; using defaults",
            extra={
                "event": "config_invalid_strategies",
                "config_path": str(config_path),
            },
        )
        strategies_data = {}
    strategy_configs: Dict[str, StrategyConfig] = {}

    # Process 'configs' section
    raw_strategy_configs = strategies_data.get("configs") or {}
    if not isinstance(raw_strategy_configs, dict):
        logger.warning(
            "Strategy configs section is not a mapping; skipping strategy-specific configs",
            extra={
                "event": "config_invalid_strategy_configs",
                "config_path": str(config_path),
            },
        )
        raw_strategy_configs = {}
    for config_key, cfg in raw_strategy_configs.items():
        if not isinstance(cfg, dict):
            logger.warning(
                "Strategy config is not a mapping; skipping entry",
                extra={
                    "event": "config_invalid_strategy_entry",
                    "config_path": str(config_path),
                    "strategy": config_key,
                },
            )
            continue
        # Copy cfg to avoid modifying the original dictionary during pop
        cfg_copy = cfg.copy()

        cfg_name = cfg_copy.pop("name", config_key)
        if cfg_name != config_key:
            logger.warning(
                "Strategy name '%s' does not match key '%s'; using key as canonical id",
                cfg_name,
                config_key,
                extra={
                    "event": "config_strategy_name_mismatch",
                    "config_path": str(config_path),
                    "strategy_key": config_key,
                    "strategy_name": cfg_name,
                },
            )
            cfg_name = config_key

        canonical_def = CANONICAL_STRATEGIES.get(cfg_name)
        s_type = cfg_copy.pop(
            "type", canonical_def.type if canonical_def else "unknown"
        )
        if canonical_def and s_type != canonical_def.type:
            logger.warning(
                "Strategy %s type '%s' does not match canonical '%s'; forcing canonical type",
                cfg_name,
                s_type,
                canonical_def.type,
                extra={
                    "event": "config_strategy_type_mismatch",
                    "config_path": str(config_path),
                    "strategy_id": cfg_name,
                    "strategy_type": s_type,
                    "canonical_type": canonical_def.type,
                },
            )
            s_type = canonical_def.type

        # In the config file, 'enabled' might be on the specific strategy config
        # or just inferred from the global enabled list. We'll support both, defaulting to True here
        # and checking the global list separately if needed, or assume the global list drives execution.
        # But strictly speaking, the global 'enabled' list in StrategiesConfig is the driver.
        # We'll just load the 'enabled' flag if present in the specific config too.
        s_enabled = cfg_copy.pop("enabled", True)
        userref = cfg_copy.pop("userref", None)

        # The rest are params
        params = cfg_copy

        strategy_configs[cfg_name] = StrategyConfig(
            name=cfg_name,
            type=s_type,
            enabled=s_enabled,
            userref=userref,
            params=params,
        )

    raw_enabled = strategies_data.get("enabled", [])
    if not isinstance(raw_enabled, list):
        logger.warning(
            "Enabled strategies should be a list; defaulting to empty",
            extra={
                "event": "config_invalid_strategy_enabled",
                "config_path": str(config_path),
            },
        )
        raw_enabled = []

    normalized_enabled: List[str] = []
    for strategy_id in raw_enabled:
        if strategy_id not in strategy_configs:
            logger.warning(
                "Enabled strategy %s has no matching config; skipping",
                strategy_id,
                extra={
                    "event": "config_unknown_strategy_enabled",
                    "config_path": str(config_path),
                    "strategy_id": strategy_id,
                },
            )
            continue
        normalized_enabled.append(strategy_id)

    strategies_config = StrategiesConfig(
        enabled=normalized_enabled, configs=strategy_configs
    )

    raw_strategy_limits = risk_data.get("max_per_strategy_pct", {})
    if not isinstance(raw_strategy_limits, dict):
        logger.warning(
            "max_per_strategy_pct should be a mapping; defaulting to empty",
            extra={
                "event": "config_invalid_strategy_limits",
                "config_path": str(config_path),
            },
        )
        raw_strategy_limits = {}

    normalized_limits: Dict[str, float] = {}
    for strategy_id, pct_limit in raw_strategy_limits.items():
        if strategy_id not in strategy_configs:
            logger.warning(
                "Risk limit references unknown strategy %s; skipping",
                strategy_id,
                extra={
                    "event": "config_unknown_strategy_limit",
                    "config_path": str(config_path),
                    "strategy_id": strategy_id,
                },
            )
            continue
        normalized_limits[strategy_id] = pct_limit

    risk_config = RiskConfig(
        max_risk_per_trade_pct=risk_data.get("max_risk_per_trade_pct", 1.0),
        max_portfolio_risk_pct=risk_data.get("max_portfolio_risk_pct", 10.0),
        max_open_positions=risk_data.get("max_open_positions", 10),
        max_per_asset_pct=risk_data.get("max_per_asset_pct", 5.0),
        max_per_strategy_pct=normalized_limits,
        max_daily_drawdown_pct=risk_data.get("max_daily_drawdown_pct", 10.0),
        kill_switch_on_drift=risk_data.get("kill_switch_on_drift", True),
        include_manual_positions=risk_data.get("include_manual_positions", True),
        volatility_lookback_bars=risk_data.get("volatility_lookback_bars", 20),
        min_liquidity_24h_usd=risk_data.get("min_liquidity_24h_usd", 100000.0),
        dynamic_allocation_enabled=risk_data.get("dynamic_allocation_enabled", False),
        dynamic_allocation_lookback_hours=risk_data.get(
            "dynamic_allocation_lookback_hours", 72
        ),
        min_strategy_weight_pct=risk_data.get("min_strategy_weight_pct", 0.0),
        max_strategy_weight_pct=risk_data.get("max_strategy_weight_pct", 50.0),
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
            extra={
                "event": "config_invalid_market_data",
                "config_path": str(config_path),
            },
        )
        market_data = {}

    default_ohlc_store = get_default_ohlc_store_config()
    ohlc_store_config = market_data.get("ohlc_store", {}) or {}
    if not isinstance(ohlc_store_config, dict):
        logger.warning(
            "OHLC store config is not a mapping; using defaults",
            extra={
                "event": "config_invalid_ohlc_store",
                "config_path": str(config_path),
            },
        )
        ohlc_store_config = {}
    merged_ohlc_store = {**default_ohlc_store, **ohlc_store_config}

    execution_data = raw_config.get("execution") or {}
    if not isinstance(execution_data, dict):
        logger.warning(
            "Execution config is not a mapping; using defaults",
            extra={
                "event": "config_invalid_execution",
                "config_path": str(config_path),
            },
        )
        execution_data = {}

    default_execution = ExecutionConfig()
    execution_mode = execution_data.get("mode")
    if execution_mode is None:
        execution_mode = "live" if effective_env == "live" else "paper"

    if execution_mode not in {"paper", "live"}:
        logger.warning(
            "Invalid execution mode '%s'; defaulting to 'paper'",
            execution_mode,
            extra={
                "event": "config_invalid_execution_mode",
                "config_path": str(config_path),
            },
        )
        execution_mode = "paper"

    validate_only = execution_data.get("validate_only")
    if validate_only is None:
        validate_only = True

    execution_config = ExecutionConfig(
        mode=execution_mode,
        default_order_type=execution_data.get(
            "default_order_type", default_execution.default_order_type
        ),
        max_slippage_bps=execution_data.get(
            "max_slippage_bps", default_execution.max_slippage_bps
        ),
        time_in_force=execution_data.get(
            "time_in_force", default_execution.time_in_force
        ),
        post_only=execution_data.get("post_only", default_execution.post_only),
        validate_only=validate_only,
        allow_live_trading=execution_data.get(
            "allow_live_trading", default_execution.allow_live_trading
        ),
        paper_tests_completed=execution_data.get(
            "paper_tests_completed", default_execution.paper_tests_completed
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

    if (
        execution_config.mode == "live"
        and execution_config.validate_only is False
        and execution_config.allow_live_trading is False
    ):
        logger.warning(
            "Live mode requested without allow_live_trading; forcing validate_only",
            extra={
                "event": "config_live_without_allow_live",
                "config_path": str(config_path),
            },
        )
        execution_config.validate_only = True
    elif (
        execution_config.mode == "live"
        and execution_config.validate_only is False
        and execution_config.allow_live_trading is True
    ):
        logger.info(
            "Live mode with trading enabled; fully live trading is active",
            extra={
                "event": "config_live_trading_enabled",
                "config_path": str(config_path),
            },
        )

    ui_data = raw_config.get("ui") or {}
    if not isinstance(ui_data, dict):
        logger.warning(
            "UI config is not a mapping; using defaults",
            extra={"event": "config_invalid_ui", "config_path": str(config_path)},
        )
        ui_data = {}

    auth_data = ui_data.get("auth") or {}
    if not isinstance(auth_data, dict):
        logger.warning(
            "UI auth config is not a mapping; using defaults",
            extra={"event": "config_invalid_ui_auth", "config_path": str(config_path)},
        )
        auth_data = {}

    refresh_data = ui_data.get("refresh_intervals") or {}
    if not isinstance(refresh_data, dict):
        logger.warning(
            "UI refresh intervals config is not a mapping; using defaults",
            extra={
                "event": "config_invalid_ui_refresh",
                "config_path": str(config_path),
            },
        )
        refresh_data = {}

    auth_token = auth_data.get("token", default_ui.auth.token)
    auth_config = UIAuthConfig(
        enabled=auth_data.get("enabled", default_ui.auth.enabled),
        token=auth_token if isinstance(auth_token, str) else default_ui.auth.token,
    )

    refresh_config = UIRefreshConfig(
        dashboard_ms=_validated_int(
            refresh_data.get("dashboard_ms"),
            default_ui.refresh_intervals.dashboard_ms,
            "config_ui_dashboard_ms",
        ),
        orders_ms=_validated_int(
            refresh_data.get("orders_ms"),
            default_ui.refresh_intervals.orders_ms,
            "config_ui_orders_ms",
        ),
        strategies_ms=_validated_int(
            refresh_data.get("strategies_ms"),
            default_ui.refresh_intervals.strategies_ms,
            "config_ui_strategies_ms",
        ),
    )

    base_path = ui_data.get("base_path", default_ui.base_path)
    if not isinstance(base_path, str):
        logger.warning(
            "UI base_path is not a string; using default",
            extra={
                "event": "config_invalid_ui_base_path",
                "config_path": str(config_path),
            },
        )
        base_path = default_ui.base_path
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"

    ui_port = _validated_int(ui_data.get("port"), default_ui.port, "config_ui_port")
    if ui_port > 65535:
        logger.warning(
            "UI port is out of valid range; using default",
            extra={"event": "config_invalid_ui_port", "config_path": str(config_path)},
        )
        ui_port = default_ui.port

    ui_config = UIConfig(
        enabled=ui_data.get("enabled", default_ui.enabled),
        host=(
            ui_data.get("host", default_ui.host)
            if isinstance(ui_data.get("host", default_ui.host), str)
            else default_ui.host
        ),
        port=ui_port,
        base_path=base_path,
        auth=auth_config,
        read_only=ui_data.get("read_only", default_ui.read_only),
        refresh_intervals=refresh_config,
    )

    return AppConfig(
        region=RegionProfile(
            code=region_data.get("code", default_region.code),
            capabilities=RegionCapabilities(
                supports_margin=capabilities_data.get(
                    "supports_margin", default_region.capabilities.supports_margin
                ),
                supports_futures=capabilities_data.get(
                    "supports_futures", default_region.capabilities.supports_futures
                ),
                supports_staking=capabilities_data.get(
                    "supports_staking", default_region.capabilities.supports_staking
                ),
            ),
            default_quote=region_data.get(
                "default_quote", default_region.default_quote
            ),
        ),
        universe=UniverseConfig(
            include_pairs=universe_data.get("include_pairs", []),
            exclude_pairs=universe_data.get("exclude_pairs", []),
            min_24h_volume_usd=universe_data.get("min_24h_volume_usd", 0.0),
        ),
        market_data=MarketDataConfig(
            ws=market_data.get("ws", {}),
            ohlc_store=merged_ohlc_store,
            backfill_timeframes=market_data.get(
                "backfill_timeframes", ["1d", "4h", "1h"]
            ),
            ws_timeframes=market_data.get("ws_timeframes", ["1m"]),
            metadata_path=market_data.get("metadata_path"),
        ),
        portfolio=portfolio_config,
        execution=execution_config,
        risk=risk_config,
        strategies=strategies_config,
        ui=ui_config,
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
