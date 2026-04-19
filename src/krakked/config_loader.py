from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import appdirs  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from krakked.config_models import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    MLConfig,
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
from krakked.logging_config import get_log_environment, structured_log_extra
from krakked.strategy.catalog import CANONICAL_STRATEGIES

RUNTIME_OVERRIDES_FILENAME = "config.runtime.yaml"
DEFAULT_STARTER_STRATEGY_IDS = [
    "trend_core",
    "vol_breakout",
    "majors_mean_rev",
    "rs_rotation",
]

logger = logging.getLogger(__name__)


def _get_path_override(*env_names: str) -> Path | None:
    """Return the first non-empty path override from the provided env vars."""

    for env_name in env_names:
        value = os.environ.get(env_name, "").strip()
        if value:
            return Path(value).expanduser()
    return None


def get_initial_ui_config() -> dict[str, Any]:
    """Return safe UI bind defaults for local and containerized first boot."""

    default_host = UIConfig().host
    default_port = UIConfig().port

    env_host = os.environ.get("KRAKKED_UI_HOST")
    env_port = os.environ.get("KRAKKED_UI_PORT")

    if env_host:
        host = env_host.strip() or default_host
    elif os.environ.get("UI_DIST_DIR") == "/app/ui-dist":
        host = "0.0.0.0"
    else:
        host = default_host

    try:
        port = int(env_port) if env_port else default_port
    except ValueError:
        logger.warning(
            "Invalid KRAKKED_UI_PORT=%s; using default %s", env_port, default_port
        )
        port = default_port

    if port <= 0 or port > 65535:
        logger.warning("Out-of-range UI port %s; using default %s", port, default_port)
        port = default_port

    return {"enabled": True, "host": host, "port": port}


def get_config_dir() -> Path:
    """
    Returns the OS-specific configuration directory for the bot using appdirs.
    """
    override = _get_path_override("KRAKKED_CONFIG_DIR")
    if override is not None:
        return override
    return Path(appdirs.user_config_dir("krakked"))


def get_default_ohlc_store_config() -> Dict[str, str]:
    """
    Provides a sensible default configuration for the OHLC store, pointing to
    a user-specific data directory with a Parquet backend.
    """
    data_dir = _get_path_override("KRAKKED_DATA_DIR")
    if data_dir is None:
        data_dir = Path(appdirs.user_data_dir("krakked"))
    default_root = data_dir / "ohlc"
    return {"root_dir": str(default_root), "backend": "parquet"}


def get_default_starter_strategies_config() -> Dict[str, Any]:
    """Return a conservative non-ML starter strategy block for first run."""

    configs: Dict[str, Any] = {}
    for strategy_id in DEFAULT_STARTER_STRATEGY_IDS:
        definition = CANONICAL_STRATEGIES[strategy_id]
        configs[strategy_id] = {
            "name": strategy_id,
            "type": definition.type,
            "enabled": True,
            "strategy_weight": 100,
        }

    return {
        "enabled": list(DEFAULT_STARTER_STRATEGY_IDS),
        "configs": configs,
    }


def _has_nonempty_strategy_config(config_data: dict[str, Any]) -> bool:
    strategies = config_data.get("strategies")
    if not isinstance(strategies, dict):
        return False

    enabled = strategies.get("enabled")
    if isinstance(enabled, list) and len(enabled) > 0:
        return True

    configs = strategies.get("configs")
    if isinstance(configs, dict) and len(configs) > 0:
        return True

    return False


def _load_yaml_mapping(path: Path) -> dict:
    """
    Helper to load a YAML file as a dict.
    - If missing: returns {}.
    - If invalid YAML: RAISES exception (fail closed).
    - If valid but not a mapping (e.g. list/string): returns {} (fail safe).
    """
    if not path.exists():
        return {}
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return {}
    return data


def _coerce_strategy_weight(
    value: Any, strategy_id: str, config_path: Path
) -> int:
    """Validate a user-facing strategy weight on a simple 1-100 scale."""

    if value is None:
        return 100

    try:
        weight = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "Strategy weight for %s is invalid; using default",
            strategy_id,
            extra={
                "event": "config_invalid_strategy_weight",
                "config_path": str(config_path),
                "strategy_id": strategy_id,
                "provided": repr(value),
            },
        )
        return 100

    if 1 <= weight <= 100:
        return weight

    logger.warning(
        "Strategy weight for %s out of range; clamping to 1-100",
        strategy_id,
        extra={
            "event": "config_strategy_weight_clamped",
            "config_path": str(config_path),
            "strategy_id": strategy_id,
            "provided": weight,
        },
    )
    return min(max(weight, 1), 100)


def _resolve_effective_env(
    env: Optional[str], config_path: Optional[str] = None
) -> str:
    """
    Resolves the effective environment string from param or OS env.
    Default: 'paper'.
    Logs warning if invalid.
    """
    allowed_envs = {"dev", "paper", "live"}
    initial_env = env if env is not None else os.environ.get("KRAKKED_ENV")

    if initial_env not in allowed_envs:
        # We only log here if we can verify it's invalid (not None)
        # or if we want to log the fallback. The original logic logged if not allowed.
        logger.warning(
            "Invalid or missing environment '%s'; defaulting to 'paper'",
            initial_env,
            extra={"event": "config_invalid_env", "config_path": str(config_path)},
        )
        return "paper"
    return initial_env


def _load_runtime_overrides(config_dir: Path) -> dict:
    path = config_dir / RUNTIME_OVERRIDES_FILENAME
    try:
        return _load_yaml_mapping(path)
    except Exception:
        raise


def dump_runtime_overrides(
    config: AppConfig,
    config_dir: Path | None = None,
    session: SessionConfig | None = None,
    sections: set[str] | None = None,
) -> None:
    """Persist runtime overrides to disk.

    This file is intentionally a *partial* config overlay (layered on top of
    config.yaml and (optionally) a profile yaml). Some call-sites only want to
    update a subset of the overlay (e.g., session state) without clobbering
    other persisted runtime overrides (e.g., strategy toggles).

    Args:
        config: Current in-memory AppConfig.
        config_dir: Optional override for the config directory.
        session: Optional SessionConfig source (defaults to config.session).
        sections: Optional set of top-level sections to update. If omitted,
            updates all supported sections: {"risk","strategies","ui","session"}.

    Notes:
        - This function merges updates into the existing runtime overrides file
          instead of overwriting it with only the requested sections.
        - Section updates replace the entire section (not a deep merge) to avoid
          leaving stale keys behind.
    """

    config_dir = config_dir or get_config_dir()

    session_config = session or getattr(config, "session", None)
    profile_name = session_config.profile_name if session_config else None

    if profile_name:
        path = config_dir / "profiles" / profile_name / RUNTIME_OVERRIDES_FILENAME
    else:
        path = config_dir / RUNTIME_OVERRIDES_FILENAME

    # Load existing runtime overrides so we can update only requested sections.
    existing: dict = {}
    if path.exists():
        try:
            # We use local safe load here to handle corruption gracefully during DUMP
            # (overwrite corrupt file with new state)
            with open(path, "r") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            # If the file is unreadable/corrupt, fall back to a clean slate.
            existing = {}

    # Unconditionally scrub 'active' and 'ml_enabled' from existing session config if present
    # to ensure they are never persisted even if 'session' section is not updated.
    if isinstance(existing.get("session"), dict):
        existing["session"].pop("active", None)
        existing["session"].pop("ml_enabled", None)

    update_sections = sections or {"risk", "strategies", "ui", "session"}

    if "risk" in update_sections:
        existing["risk"] = asdict(config.risk)

    if "strategies" in update_sections:
        existing["strategies"] = {
            "enabled": list(config.strategies.enabled),
            "configs": {
                sid: asdict(cfg) for sid, cfg in config.strategies.configs.items()
            },
        }

    if "ui" in update_sections:
        existing["ui"] = {"refresh_intervals": asdict(config.ui.refresh_intervals)}

    if "session" in update_sections:
        if session_config:
            existing["session"] = {
                "profile_name": session_config.profile_name,
                "mode": session_config.mode,
                "loop_interval_sec": session_config.loop_interval_sec,
                # ml_enabled removed
                "emergency_flatten": getattr(
                    session_config, "emergency_flatten", False
                ),
                "account_id": session_config.account_id or "default",
            }
        else:
            # If explicitly requested but we have no session source, remove it.
            existing.pop("session", None)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp_path, "w") as f:
            yaml.safe_dump(existing, f)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists() and tmp_path != path:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def write_initial_config(config_data: dict, config_dir: Path | None = None) -> None:
    """
    Writes the initial configuration file to disk.

    Args:
        config_data: A dictionary representing the full configuration structure.
        config_dir: Optional override for the target directory.
    """
    config_dir = config_dir or get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.yaml"

    if path.exists():
        raise FileExistsError(f"Configuration file already exists at {path}")

    # Ensure critical sections exist to prevent invalid configs
    if "region" not in config_data:
        config_data["region"] = {"code": "US_CA", "default_quote": "USD"}
    if "universe" not in config_data:
        config_data["universe"] = {"include_pairs": [], "min_24h_volume_usd": 0.0}

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w") as f:
            yaml.safe_dump(config_data, f, default_flow_style=False)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists() and tmp_path != path:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _validate_config_int(
    value: Any, default: int, field_name: str, config_path: Path, min_value: int = 1
) -> int:
    """Validate a config value intended to be an integer."""
    if value is None:
        return default

    def _warn_invalid(provided: object) -> int:
        logger.warning(
            "%s is invalid; using default",
            field_name,
            extra={
                "event": field_name,
                "config_path": str(config_path),
                "provided": repr(provided),
                "provided_type": type(provided).__name__,
            },
        )
        return default

    if isinstance(value, bool):
        return _warn_invalid(value)

    if isinstance(value, int):
        if value >= min_value:
            return value
        return _warn_invalid(value)

    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return _warn_invalid(value)
        try:
            parsed = int(stripped, 10)
        except ValueError:
            return _warn_invalid(value)
        if parsed >= min_value:
            return parsed
        return _warn_invalid(parsed)

    return _warn_invalid(value)


def _parse_ui_config(
    ui_data: Dict[str, Any],
    default_ui: UIConfig,
    is_live_env: bool,
    config_path: Path,
) -> UIConfig:
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

    auth_enabled = bool(auth_data.get("enabled", default_ui.auth.enabled))
    auth_token = (auth_data.get("token", default_ui.auth.token) or "").strip()

    if auth_enabled and not auth_token:
        logger.warning(
            "UI auth misconfigured: enabled but token is empty; forcing auth.enabled = False",
            extra=structured_log_extra(
                env=get_log_environment(),
                event="ui_auth_empty_token",
            ),
        )
        auth_enabled = False

    auth_config = UIAuthConfig(enabled=auth_enabled, token=auth_token)

    refresh_config = UIRefreshConfig(
        dashboard_ms=_validate_config_int(
            refresh_data.get("dashboard_ms"),
            default_ui.refresh_intervals.dashboard_ms,
            "config_ui_dashboard_ms",
            config_path,
        ),
        orders_ms=_validate_config_int(
            refresh_data.get("orders_ms"),
            default_ui.refresh_intervals.orders_ms,
            "config_ui_orders_ms",
            config_path,
        ),
        strategies_ms=_validate_config_int(
            refresh_data.get("strategies_ms"),
            default_ui.refresh_intervals.strategies_ms,
            "config_ui_strategies_ms",
            config_path,
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

    ui_port = _validate_config_int(
        ui_data.get("port"), default_ui.port, "config_ui_port", config_path
    )
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

    if is_live_env and ui_config.enabled:
        if not ui_config.auth.enabled:
            logger.warning(
                "Disabling UI in live environment: ui.auth.enabled is False",
                extra=structured_log_extra(
                    env="live",
                    event="live_ui_disabled_no_auth",
                    ui_host=ui_config.host,
                    ui_port=ui_config.port,
                ),
            )
            ui_config.enabled = False

        if ui_config.host == "0.0.0.0":
            logger.warning(
                "UI is configured to listen on 0.0.0.0 in live environment",
                extra=structured_log_extra(
                    env="live",
                    event="live_ui_public_host_warning",
                    ui_host=ui_config.host,
                    ui_port=ui_config.port,
                ),
            )

    return ui_config


def parse_app_config(
    raw_config: Dict[str, Any],
    *,
    config_path: Path,
    effective_env: str,
) -> AppConfig:
    """
    Parses a fully merged dictionary into an AppConfig object, running all validations.
    """
    is_live_env = effective_env == "live"

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
    strategies_declared = "strategies" in raw_config
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
    if not strategies_declared:
        strategies_data = get_default_starter_strategies_config()
        logger.info(
            "No strategies block found; applying starter strategy defaults",
            extra={
                "event": "config_default_starter_strategies",
                "config_path": str(config_path),
                "strategy_ids": list(DEFAULT_STARTER_STRATEGY_IDS),
            },
        )
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

        s_enabled = cfg_copy.pop("enabled", True)
        userref = cfg_copy.pop("userref", None)
        strategy_weight = _coerce_strategy_weight(
            cfg_copy.pop("strategy_weight", 100), cfg_name, config_path
        )
        params = cfg_copy

        strategy_configs[cfg_name] = StrategyConfig(
            name=cfg_name,
            type=s_type,
            enabled=s_enabled,
            userref=userref,
            strategy_weight=strategy_weight,
            params=params,
        )

    # --- ML Config Parsing ---
    ml_data = raw_config.get("ml") or {}
    if not isinstance(ml_data, dict):
        logger.warning(
            "ML config is not a mapping; using defaults",
            extra={"event": "config_invalid_ml", "config_path": str(config_path)},
        )
        ml_data = {}

    session_data = raw_config.get("session") or {}
    if not isinstance(session_data, dict):
        session_data = {}

    # ML Enablement Precedence:
    # 1. ml.enabled (if present in config)
    # 2. session.ml_enabled (legacy fallback)
    # 3. default True
    ml_enabled_value: bool = True
    if "enabled" in ml_data:
        ml_enabled_value = bool(ml_data["enabled"])
    elif "ml_enabled" in session_data:
        ml_enabled_value = bool(session_data["ml_enabled"])
        logger.warning(
            "Using legacy session.ml_enabled; please migrate to ml.enabled",
            extra={
                "event": "config_legacy_ml_enabled",
                "config_path": str(config_path),
            },
        )

    # Validate ML numeric fields
    training_window = _validate_config_int(
        ml_data.get("training_window_examples"),
        5000,
        "ml_training_window_examples",
        config_path,
    )
    catch_up_days = _validate_config_int(
        ml_data.get("catch_up_max_days"), 7, "ml_catch_up_max_days", config_path
    )
    catch_up_bars = _validate_config_int(
        ml_data.get("catch_up_max_bars"), 500, "ml_catch_up_max_bars", config_path
    )

    ml_config = MLConfig(
        enabled=ml_enabled_value,
        training_window_examples=training_window,
        catch_up_max_days=catch_up_days,
        catch_up_max_bars=catch_up_bars,
    )

    # --- Strategy Gating ---

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

        # If ML is disabled, we remove ML strategies from the enabled list
        # regardless of what the user put in 'enabled'
        if not ml_config.enabled:
            scfg = strategy_configs[strategy_id]
            if scfg.type.startswith("machine_learning"):
                # Also force config flag to False to be safe
                scfg.enabled = False
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

    missing_limits = [sid for sid in normalized_enabled if sid not in normalized_limits]
    if missing_limits:
        default_strategy_limit = float(
            risk_data.get(
                "max_risk_per_trade_pct",
                risk_data.get("max_portfolio_risk_pct", 10.0),
            )
        )

        for strategy_id in missing_limits:
            logger.warning(
                "Enabled strategy %s missing risk limit; applying default",
                strategy_id,
                extra={
                    "event": "config_missing_strategy_limit",
                    "config_path": str(config_path),
                    "strategy_id": strategy_id,
                    "applied_limit_pct": default_strategy_limit,
                },
            )
            normalized_limits[strategy_id] = default_strategy_limit

        if is_live_env:
            raise ValueError(
                "Live trading requires explicit max_per_strategy_pct entries for all enabled strategies"
            )

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
            # default_ml_enabled removed
        )

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
        active=False,
        profile_name=session_data.get("profile_name"),
        mode=session_data.get("mode", "paper"),
        loop_interval_sec=float(session_loop_seconds),
        # ml_enabled removed
        emergency_flatten=bool(session_data.get("emergency_flatten", False)),
        account_id=session_data.get("account_id") or "default",
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
            extra={
                "event": "config_invalid_execution",
                "config_path": str(config_path),
            },
        )
        execution_data = {}

    allowed_execution_modes = {"live", "paper", "dry_run", "simulation"}
    execution_mode = execution_data.get("mode", "paper")

    if execution_mode == "dev":
        execution_mode = "dry_run"

    if execution_mode not in allowed_execution_modes:
        logger.warning(
            "Invalid execution mode '%s'; defaulting to 'paper'",
            execution_mode,
            extra={
                "event": "config_invalid_execution_mode",
                "config_path": str(config_path),
                "allowed_modes": list(allowed_execution_modes),
            },
        )
        execution_mode = "paper"

    raw_slippage_bps = execution_data.get("max_slippage_bps", 50)
    if not isinstance(raw_slippage_bps, (int, float)):
        logger.warning(
            "max_slippage_bps is invalid; using default",
            extra={
                "event": "config_invalid_max_slippage_bps",
                "config_path": str(config_path),
            },
        )
        raw_slippage_bps = 50

    clamped_slippage_bps = max(0, min(int(raw_slippage_bps), 5000))
    if clamped_slippage_bps != raw_slippage_bps:
        logger.warning(
            "max_slippage_bps out of range; clamped to %s bps",
            clamped_slippage_bps,
            extra={
                "event": "config_clamped_max_slippage_bps",
                "config_path": str(config_path),
                "requested": raw_slippage_bps,
                "clamped": clamped_slippage_bps,
            },
        )

    max_plan_age_seconds = _validate_config_int(
        execution_data.get("max_plan_age_seconds"),
        ExecutionConfig().max_plan_age_seconds,
        "config_execution_max_plan_age_seconds",
        config_path,
    )

    execution_config = ExecutionConfig(
        mode=execution_mode,
        default_order_type=execution_data.get("default_order_type", "limit"),
        max_slippage_bps=clamped_slippage_bps,
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
        max_plan_age_seconds=max_plan_age_seconds,
    )

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

    default_auto_migrate = True
    if execution_mode in ("live", "paper"):
        default_auto_migrate = False

    auto_migrate_schema = portfolio_data.get(
        "auto_migrate_schema", default_auto_migrate
    )

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
        auto_migrate_schema=auto_migrate_schema,
    )

    if execution_config.mode == "live":
        if not execution_config.allow_live_trading:
            logger.warning(
                "Live trading mode requested but allow_live_trading is False; "
                "forcing validate_only=True to prevent real order submission",
                extra={
                    "event": "config_live_trading_disabled",
                    "config_path": str(config_path),
                },
            )
            execution_config.validate_only = True
        if not execution_config.paper_tests_completed:
            logger.warning(
                "Live trading mode requested but paper tests not marked completed",
                extra={
                    "event": "config_paper_tests_incomplete",
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

    ui_config = _parse_ui_config(ui_data, default_ui, is_live_env, config_path)

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
        ml=ml_config,
    )


def load_config(
    config_path: Optional[Path] = None, env: Optional[str] = None
) -> AppConfig:
    """
    Loads and merges application configuration from multiple layers.

    Configuration loading follows this precedence order (later layers override earlier ones):
    1. Base Config (e.g., ``config.yaml``)
    2. Environment Overlay (e.g., ``config.paper.yaml``)
    3. Active Profile Config (if ``session.profile_name`` is set in the accumulated config)
    4. Runtime Overrides (``config.runtime.yaml``, scoped to profile if active)

    Args:
        config_path: Path to the base config file. Defaults to the user config directory.
        env: Environment overlay selector ('dev', 'paper', 'live'). Defaults to 'paper' if not
             provided and KRAKKED_ENV is missing/invalid.

    Returns:
        AppConfig: The fully merged, validated, and typed configuration object.
    """
    if config_path is None:
        config_path = get_config_dir() / "config.yaml"

    config_path = config_path.expanduser()
    effective_env = _resolve_effective_env(env, str(config_path))
    from krakked.utils.io import deep_merge_dicts as _deep_merge_dicts

    # 1. Load Base Config
    raw_config = _load_yaml_mapping(config_path)

    # 2. Merge Environment Overlay
    env_config_path = config_path.parent / f"config.{effective_env}.yaml"
    env_config = _load_yaml_mapping(env_config_path)
    if env_config:
        raw_config = _deep_merge_dicts(raw_config, env_config)

    config_dir = get_config_dir()
    has_declared_strategy_config = _has_nonempty_strategy_config(raw_config)

    # 3. Merge Active Profile
    session_data = raw_config.get("session") or {}
    active_profile = session_data.get("profile_name")
    profiles_registry = raw_config.get("profiles") or {}

    if active_profile and active_profile in profiles_registry:
        profile_entry = profiles_registry[active_profile]
        profile_path_str = profile_entry.get("config_path")
        if profile_path_str:
            profile_path = Path(profile_path_str)
            if not profile_path.is_absolute():
                profile_path = config_dir / profile_path

            profile_config = _load_yaml_mapping(profile_path)
            if profile_config:
                raw_config = _deep_merge_dicts(raw_config, profile_config)
                has_declared_strategy_config = has_declared_strategy_config or (
                    _has_nonempty_strategy_config(profile_config)
                )

    # 4. Merge Runtime Overrides
    # If active profile, look for profiles/<profile>/config.runtime.yaml
    if active_profile:
        overrides_path = (
            config_dir / "profiles" / active_profile / RUNTIME_OVERRIDES_FILENAME
        )
    else:
        overrides_path = config_dir / RUNTIME_OVERRIDES_FILENAME

    runtime_overrides = _load_yaml_mapping(overrides_path)
    if runtime_overrides:
        if (
            not has_declared_strategy_config
            and isinstance(runtime_overrides.get("strategies"), dict)
            and not _has_nonempty_strategy_config(runtime_overrides)
        ):
            runtime_overrides = dict(runtime_overrides)
            runtime_overrides.pop("strategies", None)
        raw_config = _deep_merge_dicts(raw_config, runtime_overrides)

    # 5. Parse and Validate
    return parse_app_config(
        raw_config, config_path=config_path, effective_env=effective_env
    )
