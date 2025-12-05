from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import appdirs  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from kraken_bot.strategy.catalog import CANONICAL_STRATEGIES
from kraken_bot.config_models import (
    AppConfig,
    ExecutionConfig,
    PortfolioConfig,
    ProfileConfig,
    RegionCapabilities,
    RegionProfile,
    RiskConfig,
    SessionConfig,
    StrategiesConfig,
    StrategyConfig,
    UIAuthConfig,
    UIConfig,
    UIRefreshConfig,
    UniverseConfig,
)


RUNTIME_OVERRIDES_FILENAME = "config.runtime.yaml"


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


def dump_runtime_overrides(
    config: AppConfig,
    config_dir: Path | None = None,
    session: SessionConfig | None = None,
) -> None:
    config_dir = config_dir or get_config_dir()
    path = config_dir / RUNTIME_OVERRIDES_FILENAME

    session_config = session or getattr(config, "session", None)

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

    if session_config:
        data["session"] = {
            "profile_name": session_config.profile_name,
            "mode": session_config.mode,
            "loop_interval_sec": session_config.loop_interval_sec,
            "ml_enabled": session_config.ml_enabled,
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        # Create a unique temp file in the same directory
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=path.parent,
            prefix=path.name,
            suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)

            # Write the full YAML document to the temp file
            yaml.safe_dump(data, f)

            # Ensure it’s flushed to disk before we swap it in
            f.flush()
            os.fsync(f.fileno())

        # Atomically replace the old overrides file with the new one
        os.replace(tmp_path, path)
    finally:
        # If something went wrong before os.replace, clean up any stray temp file
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                # Best effort cleanup – failure here is non-fatal
                pass


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

    profiles_data = raw_config.get("profiles") or {}
    profiles: Dict[str, ProfileConfig] = {}
    if not isinstance(profiles_data, dict):
        logger.warning(
            "Profiles config is not a mapping; defaulting to empty profiles",
            extra={"event": "config_invalid_profiles", "config_path": str(config_path)},
        )
        profiles_data = {}

    for profile_name, profile_cfg in profiles_data.items():
        if not isinstance(profile_cfg, dict):
            logger.warning(
                "Profile %s config is not a mapping; skipping",
                profile_name,
                extra={
                    "event": "config_invalid_profile_entry",
                    "config_path": str(config_path),
                    "profile": profile_name,
                },
            )
            continue

        profiles[profile_name] = ProfileConfig(
            name=profile_cfg.get("name", profile_name),
            description=profile_cfg.get("description", ""),
            config_path=profile_cfg.get("config_path", ""),
            credentials_path=profile_cfg.get("credentials_path", ""),
            default_mode=profile_cfg.get("default_mode", "paper"),
            default_loop_interval_sec=float(
                profile_cfg.get("default_loop_interval_sec", 15.0)
            ),
            default_ml_enabled=bool(profile_cfg.get("default_ml_enabled", True)),
        )

    session_data = raw_config.get("session") or {}
    if not isinstance(session_data, dict):
        logger.warning(
            "Session config is not a mapping; using defaults",
            extra={"event": "config_invalid_session", "config_path": str(config_path)},
        )
        session_data = {}

    session_loop_seconds = session_data.get("loop_interval_sec", 15.0)
    if not isinstance(session_loop_seconds, (int, float)) or session_loop_seconds <= 0:
        logger.warning(
            "Session loop_interval_sec invalid; using default",
            extra={
                "event": "config_invalid_session_loop_interval",
                "config_path": str(config_path),
                "loop_interval": session_loop_seconds,
            },
        )
        session_loop_seconds = 15.0

    session_config = SessionConfig(
        active=bool(session_data.get("active", False)),
        profile_name=session_data.get("profile_name"),
        mode=session_data.get("mode", "paper"),
        loop_interval_sec=float(session_loop_seconds),
        ml_enabled=bool(session_data.get("ml_enabled", True)),
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

    ohlc_store = market_data.get("ohlc_store", {})
    if not isinstance(ohlc_store, dict):
        logger.warning(
            "OHLC store config is not a mapping; using defaults",
            extra={
                "event": "config_invalid_ohlc_store",
                "config_path": str(config_path),
            },
        )
        ohlc_store = {}

    default_ohlc_store = get_default_ohlc_store_config()
    merged_ohlc_store = {**default_ohlc_store, **ohlc_store}

    execution_data = raw_config.get("execution") or {}
    if not isinstance(execution_data, dict):
        logger.warning(
            "Execution config is not a mapping; using defaults",
            extra={"event": "config_invalid_execution", "config_path": str(config_path)},
        )
        execution_data = {}

    execution_mode = execution_data.get("mode", "paper")
    if execution_mode not in allowed_envs:
        logger.warning(
            "Invalid execution mode '%s'; defaulting to 'paper'",
            execution_mode,
            extra={
                "event": "config_invalid_execution_mode",
                "config_path": str(config_path),
            },
        )
        execution_mode = "paper"

    execution_config = ExecutionConfig(
        mode=execution_mode,
        default_order_type=execution_data.get("default_order_type", "limit"),
        max_slippage_bps=execution_data.get("max_slippage_bps", 50),
        time_in_force=execution_data.get("time_in_force", "GTC"),
        post_only=execution_data.get("post_only", False),
        validate_only=execution_data.get("validate_only", True),
        allow_live_trading=execution_data.get("allow_live_trading", False),
        paper_tests_completed=execution_data.get("paper_tests_completed", False),
        dead_man_switch_seconds=execution_data.get("dead_man_switch_seconds", 600),
        max_retries=execution_data.get("max_retries", 3),
        retry_backoff_seconds=execution_data.get("retry_backoff_seconds", 2),
        retry_backoff_factor=execution_data.get("retry_backoff_factor", 2.0),
        max_concurrent_orders=execution_data.get("max_concurrent_orders", 10),
        min_order_notional_usd=execution_data.get("min_order_notional_usd", 20.0),
        max_pair_notional_usd=execution_data.get("max_pair_notional_usd"),
        max_total_notional_usd=execution_data.get("max_total_notional_usd"),
    )

    # Parsing Portfolio Config
    portfolio_data = raw_config.get("portfolio") or {}
    if not isinstance(portfolio_data, dict):
        logger.warning(
            "Portfolio config is not a mapping; using defaults",
            extra={"event": "config_invalid_portfolio", "config_path": str(config_path)},
        )
        portfolio_data = {}

    valuation_pairs = portfolio_data.get("valuation_pairs", {})
    if not isinstance(valuation_pairs, dict):
        logger.warning(
            "valuation_pairs should be a mapping; defaulting to empty",
            extra={
                "event": "config_invalid_valuation_pairs",
                "config_path": str(config_path),
            },
        )
        valuation_pairs = {}

    include_assets = portfolio_data.get("include_assets", [])
    if not isinstance(include_assets, list):
        logger.warning(
            "include_assets should be a list; defaulting to empty",
            extra={
                "event": "config_invalid_include_assets",
                "config_path": str(config_path),
            },
        )
        include_assets = []

    exclude_assets = portfolio_data.get("exclude_assets", [])
    if not isinstance(exclude_assets, list):
        logger.warning(
            "exclude_assets should be a list; defaulting to empty",
            extra={
                "event": "config_invalid_exclude_assets",
                "config_path": str(config_path),
            },
        )
        exclude_assets = []

    risk_tolerance = portfolio_data.get("reconciliation_tolerance", 1.0)
    if not isinstance(risk_tolerance, (int, float)):
        logger.warning(
            "reconciliation_tolerance should be a number; defaulting to 1.0",
            extra={
                "event": "config_invalid_reconciliation_tolerance",
                "config_path": str(config_path),
            },
        )
        risk_tolerance = 1.0

    portfolio_config = PortfolioConfig(
        base_currency=portfolio_data.get("base_currency", "USD"),
        valuation_pairs=valuation_pairs,
        include_assets=include_assets,
        exclude_assets=exclude_assets,
        cost_basis_method=portfolio_data.get("cost_basis_method", "wac"),
        track_manual_trades=portfolio_data.get("track_manual_trades", True),
        snapshot_retention_days=portfolio_data.get("snapshot_retention_days", 30),
        reconciliation_tolerance=risk_tolerance,
        db_path=portfolio_data.get("db_path", "portfolio.db"),
        auto_migrate_schema=portfolio_data.get("auto_migrate_schema", True),
    )

    # Execution/Risk validation for per-mode settings
    if execution_config.mode == "live":
        if not execution_config.allow_live_trading:
            logger.warning(
                "Live trading mode requested but allow_live_trading is False",
                extra={
                    "event": "config_live_trading_disabled",
                    "config_path": str(config_path),
                },
            )
        if not execution_config.paper_tests_completed:
            logger.warning(
                "Live trading mode requested but paper tests not marked completed",
                extra={
                    "event": "config_paper_tests_incomplete",
                    "config_path": str(config_path),
                },
            )

    # Parsing UI Config
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
                "event": "config_invalid_ui_refresh_intervals",
                "config_path": str(config_path),
            },
        )
        refresh_data = {}

    auth_config = UIAuthConfig(
        enabled=bool(auth_data.get("enabled", default_ui.auth.enabled)),
        token=auth_data.get("token", default_ui.auth.token),
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
        profiles=profiles,
        session=session_config,
    )
