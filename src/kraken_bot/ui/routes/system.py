"""System and health endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Optional

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import kraken_bot.connection.validation as validation_mod
from kraken_bot import APP_VERSION
from kraken_bot.config import dump_runtime_overrides, get_config_dir
from kraken_bot.config_loader import write_initial_config
from kraken_bot.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    ServiceUnavailableError,
)
from kraken_bot.credentials import CredentialStatus
from kraken_bot.market_data.api import MarketDataStatus
from kraken_bot.password_store import delete_master_password, save_master_password
from kraken_bot.secrets import (
    SECRETS_FILE_NAME,
    SecretsDecryptionError,
    delete_secrets,
    persist_api_keys,
    set_session_master_password,
    unlock_secrets,
)
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope, SystemHealthPayload, SystemMetricsPayload
from kraken_bot.utils.io import atomic_write, backup_file, sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter()


class CredentialPayload(BaseModel):
    """Payload expected from the UI when validating credentials."""

    apiKey: str
    apiSecret: str
    region: str


class SetupCredentialsPayload(BaseModel):
    """Payload for saving credentials during setup."""

    apiKey: str
    apiSecret: str
    password: str
    region: Optional[str] = "US"


class SetupUnlockPayload(BaseModel):
    """Payload for unlocking secrets."""

    password: str
    remember: bool = False


class SetupConfigPayload(BaseModel):
    """Payload for creating initial configuration."""

    region_code: str
    universe_pairs: list[str] = Field(default_factory=list)


class SetupStatusPayload(BaseModel):
    """Status of the setup/onboarding process."""

    configured: bool
    secrets_exist: bool
    unlocked: bool


class ModeChangePayload(BaseModel):
    """Payload for toggling the execution mode."""

    mode: Literal["paper", "live"]
    # Optional guard fields for live mode transition
    password: Optional[str] = None
    confirmation: Optional[str] = None


class SessionConfigPayload(BaseModel):
    """Payload for starting or updating a trading session."""

    profile_name: str
    mode: Literal["paper", "live"]
    loop_interval_sec: float = Field(15.0, ge=1.0, le=300.0)
    ml_enabled: bool = True


class SessionStatePayload(BaseModel):
    """Response payload describing the current session state."""

    active: bool
    mode: str
    loop_interval_sec: float
    profile_name: Optional[str]
    ml_enabled: bool
    emergency_flatten: bool = False


class ProfileSummaryPayload(BaseModel):
    """Simplified profile metadata for UI selection."""

    name: str
    description: str


class ProfileCreatePayload(BaseModel):
    """Payload for creating a new profile."""

    name: str
    description: str = ""
    default_mode: str = "paper"
    base_config: Optional[dict] = None


def _context(request: Request):
    return request.app.state.context


def _redacted_config(config) -> dict:
    config_dict = asdict(config)
    ui_config = config_dict.get("ui", {})
    auth_config = ui_config.get("auth")
    if isinstance(auth_config, dict) and "token" in auth_config:
        auth_config["token"] = "***"
    return config_dict


def _session_payload(ctx) -> SessionStatePayload:
    session = ctx.session
    return SessionStatePayload(
        active=session.active,
        mode=session.mode,
        loop_interval_sec=session.loop_interval_sec,
        profile_name=session.profile_name,
        ml_enabled=session.ml_enabled,
        emergency_flatten=getattr(session, "emergency_flatten", False),
    )


def _check_setup_mode(ctx):
    """Raises 503 if the system is in setup mode."""
    if ctx.is_setup_mode:
        raise HTTPException(
            status_code=503,
            detail="System is in setup mode. Please complete configuration.",
        )


@router.get("/setup/status", response_model=ApiEnvelope[SetupStatusPayload])
async def setup_status(request: Request) -> ApiEnvelope[SetupStatusPayload]:
    """Returns the current setup status (config present? secrets present? unlocked?)."""
    ctx = _context(request)
    config_dir = get_config_dir()
    config_path = config_dir / "config.yaml"
    secrets_path = config_dir / SECRETS_FILE_NAME

    configured = config_path.exists()
    secrets_exist = secrets_path.exists()
    # If secrets exist but we are still in setup mode, it means they are locked
    # (or config is missing, but 'configured' flag covers that).
    # If ctx.is_setup_mode is False, we are unlocked.
    unlocked = not ctx.is_setup_mode if secrets_exist else False

    # Edge case: If secrets exist but we are in setup mode, we are locked.
    # If secrets don't exist, 'unlocked' is False (conceptually locked/missing).

    return ApiEnvelope(
        data=SetupStatusPayload(
            configured=configured, secrets_exist=secrets_exist, unlocked=unlocked
        ),
        error=None,
    )


@router.post("/setup/config", response_model=ApiEnvelope[dict])
async def setup_config(
    payload: SetupConfigPayload, request: Request
) -> ApiEnvelope[dict]:
    """Writes the initial configuration file."""
    try:
        config_data = {
            "region": {"code": payload.region_code, "default_quote": "USD"},
            "universe": {"include_pairs": payload.universe_pairs},
            # Default minimal structure
            "execution": {"mode": "paper"},
            "ui": {"enabled": True, "port": 8000},
        }
        write_initial_config(config_data)
        logger.info(
            "Initial configuration written",
            extra=build_request_log_extra(request, event="setup_config_written"),
        )
        return ApiEnvelope(data={"success": True}, error=None)
    except Exception as exc:
        logger.exception(
            "Failed to write configuration",
            extra=build_request_log_extra(request, event="setup_config_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/setup/credentials", response_model=ApiEnvelope[dict])
async def setup_credentials(
    payload: SetupCredentialsPayload, request: Request
) -> ApiEnvelope[dict]:
    """Validates and saves encrypted credentials."""
    try:
        # 1. Validate against Kraken
        result = validation_mod.validate_credentials(
            payload.apiKey, payload.apiSecret, region=payload.region
        )

        if not result.validated:
            return ApiEnvelope(
                data={"valid": False},
                error=f"Validation failed: {result.validation_error or result.error}",
            )

        # 2. Persist
        persist_api_keys(
            api_key=payload.apiKey,
            api_secret=payload.apiSecret,
            password=payload.password,
            validated=True,
        )

        logger.info(
            "Credentials saved via setup",
            extra=build_request_log_extra(request, event="setup_credentials_saved"),
        )
        return ApiEnvelope(data={"success": True}, error=None)

    except Exception as exc:
        logger.exception(
            "Failed to save credentials",
            extra=build_request_log_extra(request, event="setup_credentials_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/setup/unlock", response_model=ApiEnvelope[dict])
async def setup_unlock(
    payload: SetupUnlockPayload, request: Request
) -> ApiEnvelope[dict]:
    """Attempts to unlock the system with the master password."""
    ctx = _context(request)
    try:
        # Verify password by attempting decryption
        _ = unlock_secrets(payload.password)

        # Set session password for re-bootstrap
        set_session_master_password(payload.password)

        remember_saved = False
        remember_error: str | None = None

        if payload.remember:
            try:
                save_master_password(payload.password)
                remember_saved = True
            except Exception as exc:
                remember_error = str(exc)
                logger.warning(
                    "Remember-me save failed (ignoring): %s",
                    exc,
                    extra=build_request_log_extra(
                        request,
                        event="setup_unlock_remember_failed",
                        error=remember_error,
                    ),
                )

        if ctx.is_setup_mode:
            logger.info(
                "Unlock successful, signaling re-initialization",
                extra=build_request_log_extra(request, event="setup_unlock_success"),
            )
            ctx.reinitialize_event.set()

        return ApiEnvelope(
            data={
                "success": True,
                "remember_saved": remember_saved,
                "remember_error": remember_error,
            },
            error=None,
        )

    except SecretsDecryptionError:
        logger.warning(
            "Unlock failed: invalid password",
            extra=build_request_log_extra(request, event="setup_unlock_failed"),
        )
        return ApiEnvelope(data=None, error="Invalid password")
    except Exception as exc:
        logger.exception(
            "Unlock failed with error",
            extra=build_request_log_extra(request, event="setup_unlock_error"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/setup/forget", response_model=ApiEnvelope[dict])
async def system_forget(request: Request) -> ApiEnvelope[dict]:
    """Forgets the master password from this device."""
    try:
        set_session_master_password(None)
        delete_master_password()

        # Clean up env var if it exists from legacy flow
        import os

        os.environ.pop("KRAKEN_BOT_SECRET_PW", None)

        logger.info(
            "Master password forgotten from device",
            extra=build_request_log_extra(request, event="system_forget"),
        )
        return ApiEnvelope(data={"success": True}, error=None)
    except Exception as exc:
        logger.exception(
            "Failed to forget master password",
            extra=build_request_log_extra(request, event="system_forget_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/reset", response_model=ApiEnvelope[dict])
async def system_reset(request: Request) -> ApiEnvelope[dict]:
    """Resets the system by deleting credentials and entering setup mode."""
    ctx = _context(request)
    try:
        delete_secrets()
        # Also forget the password since the file it unlocks is gone
        set_session_master_password(None)

        try:
            delete_master_password()
        except Exception as exc:
            logger.warning(
                "Failed to delete master password from keyring during reset (ignoring)",
                extra=build_request_log_extra(
                    request, event="reset_keyring_error", error=str(exc)
                ),
            )

        ctx.is_setup_mode = True

        logger.info(
            "System reset requested: credentials deleted",
            extra=build_request_log_extra(request, event="system_reset"),
        )
        return ApiEnvelope(data={"success": True}, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "System reset failed",
            extra=build_request_log_extra(request, event="system_reset_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/health", response_model=ApiEnvelope[SystemHealthPayload])
async def system_health(request: Request) -> ApiEnvelope[SystemHealthPayload]:
    try:
        ctx = _context(request)
        if ctx.is_setup_mode:
            # Return a limited health payload in setup mode
            return ApiEnvelope(
                data=SystemHealthPayload(
                    app_version=APP_VERSION,
                    execution_mode="setup",
                    rest_api_reachable=False,
                    websocket_connected=False,
                    streaming_pairs=0,
                    stale_pairs=0,
                    subscription_errors=0,
                    market_data_ok=False,
                    market_data_status="unavailable",
                    market_data_reason="setup_required",
                    market_data_stale=False,
                    execution_ok=False,
                    current_mode="setup",
                    ui_read_only=False,
                    kill_switch_active=False,
                    drift_detected=False,
                    market_data_max_staleness=None,
                ),
                error=None,
            )

        data_status = ctx.market_data.get_data_status()
        metrics_snapshot = ctx.metrics.snapshot()
        execution_config = ctx.config.execution
        market_data_health = ctx.market_data.get_health_status()
        if not isinstance(market_data_health, MarketDataStatus):
            market_data_health = None

        market_data_ok = None
        market_data_stale = None
        market_data_reason = None
        market_data_max_staleness = None

        if market_data_health:
            market_data_ok = getattr(market_data_health, "health", "") == "healthy"
            market_data_stale = getattr(market_data_health, "health", "") == "stale"
            market_data_reason = getattr(market_data_health, "reason", None)
            market_data_max_staleness = getattr(
                market_data_health, "max_staleness", None
            )

        if market_data_ok is None:
            market_data_ok = (
                data_status.rest_api_reachable
                and data_status.websocket_connected
                and data_status.subscription_errors == 0
                and data_status.stale_pairs == 0
            )
        if market_data_stale is None:
            market_data_stale = data_status.stale_pairs > 0
        if market_data_reason is None:
            market_data_reason = (
                None
                if market_data_ok
                else ("data_stale" if market_data_stale else "connection_issue")
            )

        metrics_has_update = bool(metrics_snapshot.get("market_data_status_updated"))

        if metrics_has_update:
            market_data_ok = bool(
                metrics_snapshot.get("market_data_ok", market_data_ok)
            )
            market_data_stale = bool(
                metrics_snapshot.get("market_data_stale", market_data_stale)
            )
            market_data_reason = metrics_snapshot.get(
                "market_data_reason", market_data_reason
            )
            market_data_max_staleness = metrics_snapshot.get(
                "market_data_max_staleness", market_data_max_staleness
            )

        market_data_status = "healthy"
        if not market_data_ok:
            market_data_status = "stale" if market_data_stale else "unavailable"

        execution_ok = execution_config.mode != "live" or bool(
            getattr(execution_config, "allow_live_trading", False)
        )
        risk_status = ctx.strategy_engine.get_risk_status()
        health_payload = SystemHealthPayload(
            app_version=APP_VERSION,
            execution_mode=getattr(execution_config, "mode", None),
            rest_api_reachable=data_status.rest_api_reachable,
            websocket_connected=data_status.websocket_connected,
            streaming_pairs=data_status.streaming_pairs,
            stale_pairs=data_status.stale_pairs,
            subscription_errors=data_status.subscription_errors,
            market_data_ok=bool(market_data_ok),
            market_data_status=market_data_status,
            market_data_reason=market_data_reason,
            market_data_stale=market_data_stale,
            market_data_max_staleness=market_data_max_staleness,
            execution_ok=execution_ok,
            current_mode=execution_config.mode,
            ui_read_only=ctx.config.ui.read_only,
            kill_switch_active=getattr(risk_status, "kill_switch_active", None),
            drift_detected=bool(metrics_snapshot.get("drift_detected")),
            drift_reason=metrics_snapshot.get("drift_reason"),
        )
        return ApiEnvelope(data=health_payload, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch system health",
            extra=build_request_log_extra(request, event="system_health_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/session", response_model=ApiEnvelope[SessionStatePayload])
async def get_session_state(request: Request) -> ApiEnvelope[SessionStatePayload]:
    try:
        ctx = _context(request)
        return ApiEnvelope(data=_session_payload(ctx), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch session state",
            extra=build_request_log_extra(request, event="session_state_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/session/start", response_model=ApiEnvelope[SessionStatePayload])
async def start_session(
    payload: SessionConfigPayload, request: Request
) -> ApiEnvelope[SessionStatePayload]:
    ctx = _context(request)
    _check_setup_mode(ctx)

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    execution_config = ctx.config.execution
    new_mode = payload.mode

    is_update = bool(getattr(ctx.session, "active", False))

    # When the session is already active, we treat this endpoint as a session update.
    # We intentionally disallow changing profile or mode while active to avoid hot-swapping
    # services mid-cycle without a full reinitialization.
    if is_update:
        if payload.profile_name != getattr(ctx.session, "profile_name", None):
            return ApiEnvelope(
                data=None,
                error="Cannot change profile while session is active. Stop the session first.",
            )
        if new_mode != getattr(ctx.session, "mode", None):
            return ApiEnvelope(
                data=None,
                error="Cannot change mode while session is active. Stop the session first.",
            )

    # Implicit guard: If starting/updating in LIVE mode, verify allow_live_trading is already set.
    # We DO NOT allow switching to live via session start if not already configured.
    # The user must use /mode to switch to live first (which has the guard).
    if new_mode == "live":
        if not getattr(execution_config, "allow_live_trading", False):
            return ApiEnvelope(
                data=None,
                error="Live trading not enabled. Use system mode switch with authentication first.",
            )
        if execution_config.mode != "live":
            return ApiEnvelope(
                data=None,
                error="System execution mode is not 'live'. Update mode first.",
            )

    old_ml_enabled = ctx.config.session.ml_enabled

    session = ctx.session
    session.active = True
    session.mode = new_mode
    session.loop_interval_sec = payload.loop_interval_sec
    session.profile_name = payload.profile_name
    session.ml_enabled = payload.ml_enabled

    ctx.config.session.active = True
    ctx.config.session.mode = new_mode
    ctx.config.session.loop_interval_sec = payload.loop_interval_sec
    ctx.config.session.profile_name = payload.profile_name
    ctx.config.session.ml_enabled = payload.ml_enabled

    # Sync ML strategies...
    if payload.ml_enabled != old_ml_enabled:
        ml_enabled = bool(payload.ml_enabled)
        for sid, strat_cfg in ctx.config.strategies.configs.items():
            if getattr(strat_cfg, "type", "").startswith("machine_learning"):
                strat_cfg.enabled = ml_enabled
                if ml_enabled:
                    if sid not in ctx.config.strategies.enabled:
                        ctx.config.strategies.enabled.append(sid)
                else:
                    if sid in ctx.config.strategies.enabled:
                        ctx.config.strategies.enabled.remove(sid)
                if sid in ctx.strategy_engine.strategy_states:
                    ctx.strategy_engine.strategy_states[sid].enabled = ml_enabled

    # Persistence: Write session state to main config so loaders can restore it on restart.
    config_dir = get_config_dir()
    main_config_path = config_dir / "config.yaml"

    try:
        if main_config_path.exists():
            with open(main_config_path, "r") as f:
                main_data = yaml.safe_load(f) or {}
        else:
            main_data = {}

        session_data = main_data.get("session", {})
        session_data["profile_name"] = payload.profile_name
        session_data["mode"] = new_mode
        session_data["loop_interval_sec"] = payload.loop_interval_sec
        session_data["ml_enabled"] = payload.ml_enabled
        session_data["active"] = True
        main_data["session"] = session_data

        backup_file(main_config_path)
        atomic_write(main_config_path, main_data, dump_func=yaml.safe_dump)
    except Exception as e:
        logger.error(f"Failed to persist session state to main config: {e}")
        # Proceeding despite error because runtime state is valid, but restart might lose it.

    if new_mode == "live" and hasattr(
        ctx.execution_service, "_emit_live_readiness_checklist"
    ):
        ctx.execution_service._emit_live_readiness_checklist()

    dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})

    logger.info(
        "Session updated" if is_update else "Session started",
        extra=build_request_log_extra(
            request,
            event="session_updated" if is_update else "session_started",
            profile=payload.profile_name,
            mode=new_mode,
            loop_interval=payload.loop_interval_sec,
            ml_enabled=payload.ml_enabled,
        ),
    )

    return ApiEnvelope(data=_session_payload(ctx), error=None)


@router.post("/session/stop", response_model=ApiEnvelope[SessionStatePayload])
async def stop_session(request: Request) -> ApiEnvelope[SessionStatePayload]:
    ctx = _context(request)
    _check_setup_mode(ctx)

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    ctx.session.active = False

    if hasattr(ctx.config, "session"):
        ctx.config.session.active = False

    # Persist stop so we don't auto-resume on restart.
    config_dir = get_config_dir()
    main_config_path = config_dir / "config.yaml"

    try:
        if main_config_path.exists():
            with open(main_config_path, "r") as f:
                main_data = yaml.safe_load(f) or {}
        else:
            main_data = {}

        session_data = main_data.get("session", {})
        session_data["profile_name"] = getattr(ctx.session, "profile_name", None)
        session_data["mode"] = getattr(ctx.session, "mode", "paper")
        session_data["loop_interval_sec"] = getattr(
            ctx.session, "loop_interval_sec", 15.0
        )
        session_data["ml_enabled"] = getattr(ctx.session, "ml_enabled", True)
        session_data["active"] = False
        main_data["session"] = session_data

        backup_file(main_config_path)
        atomic_write(main_config_path, main_data, dump_func=yaml.safe_dump)
    except Exception as e:
        logger.error(f"Failed to persist stopped session state to main config: {e}")

    dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})

    logger.info(
        "Session stopped",
        extra=build_request_log_extra(request, event="session_stopped"),
    )

    return ApiEnvelope(data=_session_payload(ctx), error=None)


@router.get("/profiles", response_model=ApiEnvelope[list[ProfileSummaryPayload]])
async def list_profiles(request: Request) -> ApiEnvelope[list[ProfileSummaryPayload]]:
    try:
        ctx = _context(request)
        _check_setup_mode(ctx)
        profiles = [
            ProfileSummaryPayload(name=name, description=cfg.description)
            for name, cfg in ctx.config.profiles.items()
        ]
        return ApiEnvelope(data=profiles, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to list profiles",
            extra=build_request_log_extra(request, event="profiles_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/profiles", response_model=ApiEnvelope[dict])
async def create_profile(
    payload: ProfileCreatePayload, request: Request
) -> ApiEnvelope[dict]:
    """
    Creates a new profile.
    """
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot create profile while session is active"
        )

    config_dir = get_config_dir()

    try:
        safe_name = sanitize_filename(payload.name)
    except ValueError as e:
        return ApiEnvelope(data=None, error=str(e))

    profile_filename = f"{safe_name}.yaml"
    profile_path = config_dir / "profiles" / profile_filename

    if profile_path.exists():
        return ApiEnvelope(
            data=None, error=f"Profile file '{profile_filename}' already exists"
        )

    try:
        # 1. Create Profile File
        base_config = payload.base_config or {}

        # Security: Prevent setting restricted execution keys in new profiles
        execution_payload = base_config.get("execution", {})
        restricted_keys = {
            "mode",
            "allow_live_trading",
            "validate_only",
            "paper_tests_completed",
        }
        for key in restricted_keys:
            if key in execution_payload:
                # Special case: 'mode' might be allowed if it matches default_mode AND isn't live?
                # But generally, we should enforce that `default_mode` argument controls the initial mode.
                # If user tries to sneak in `allow_live_trading: true` via base_config, block it.
                return ApiEnvelope(
                    data=None,
                    error=f"Execution '{key}' cannot be set via base_config. It is controlled by system state.",
                )

        # Ensure minimal structure using the declared default mode
        # If execution dict exists (but passed checks), update it. If not, create it.
        if "execution" not in base_config:
            base_config["execution"] = {}

        base_config["execution"]["mode"] = payload.default_mode
        # Force safe defaults
        base_config["execution"]["allow_live_trading"] = False
        base_config["execution"]["validate_only"] = payload.default_mode != "live"
        # Actually, even if default_mode is live (which we might block?), we can't allow live trading without the guard.

        if payload.default_mode == "live":
            return ApiEnvelope(
                data=None,
                error="Cannot create profile with default mode 'live'. Use 'paper' or 'dry_run' and upgrade later.",
            )

        atomic_write(profile_path, base_config, dump_func=yaml.safe_dump)

        # 2. Update Main Config Registry
        main_config_path = config_dir / "config.yaml"
        backup_file(main_config_path)

        with open(main_config_path, "r") as f:
            main_data = yaml.safe_load(f) or {}

        profiles = main_data.get("profiles", {})
        profiles[safe_name] = {
            "name": safe_name,
            "description": payload.description,
            "config_path": str(Path("profiles") / profile_filename),
            "credentials_path": "",
            "default_mode": payload.default_mode,
        }
        main_data["profiles"] = profiles

        atomic_write(main_config_path, main_data, dump_func=yaml.safe_dump)

        # 3. Trigger Reload
        ctx.reinitialize_event.set()

        logger.info(
            "Profile created",
            extra=build_request_log_extra(
                request, event="profile_created", profile_name=safe_name
            ),
        )

        return ApiEnvelope(
            data={"name": safe_name, "path": str(profile_path)}, error=None
        )

    except Exception as exc:
        logger.exception(
            "Failed to create profile",
            extra=build_request_log_extra(request, event="profile_create_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/metrics", response_model=ApiEnvelope[SystemMetricsPayload])
async def system_metrics(request: Request) -> ApiEnvelope[SystemMetricsPayload]:
    try:
        ctx = _context(request)
        _check_setup_mode(ctx)
        metrics = ctx.metrics
        # Thin wrapper around the shared SystemMetrics snapshot to avoid duplicating logic.
        snapshot = metrics.snapshot()
        payload = SystemMetricsPayload(**snapshot)
        return ApiEnvelope(data=payload, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch system metrics",
            extra=build_request_log_extra(request, event="system_metrics_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/config", response_model=ApiEnvelope[dict])
async def get_config(request: Request) -> ApiEnvelope[dict]:
    try:
        ctx = _context(request)
        _check_setup_mode(ctx)
        return ApiEnvelope(data=_redacted_config(ctx.config), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch config",
            extra=build_request_log_extra(request, event="config_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/mode", response_model=ApiEnvelope[dict])
async def set_execution_mode(
    payload: ModeChangePayload, request: Request
) -> ApiEnvelope[dict]:
    ctx = _context(request)
    _check_setup_mode(ctx)

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot change mode while session is active"
        )

    new_mode = payload.mode
    execution_config = ctx.config.execution
    current_mode = execution_config.mode

    if new_mode == current_mode:
        return ApiEnvelope(
            data={
                "mode": current_mode,
                "validate_only": execution_config.validate_only,
            },
            error=None,
        )

    # GUARD: Switching TO live mode
    if new_mode == "live":
        # Only require password + confirmation if we aren't already allowed to trade live.
        # This allows switching back and forth if already authenticated/unlocked.
        if not execution_config.allow_live_trading:
            # Check credentials and phrase
            if not payload.password or not payload.confirmation:
                return ApiEnvelope(
                    data=None,
                    error="Live mode requires password and confirmation phrase",
                )

            if payload.confirmation != "ENABLE LIVE TRADING":
                return ApiEnvelope(data=None, error="Invalid confirmation phrase")

            try:
                unlock_secrets(payload.password)
                # Ensure we persist this for reload
                set_session_master_password(payload.password)
            except Exception:
                logger.warning(
                    "Live mode auth failed",
                    extra=build_request_log_extra(request, event="live_auth_failed"),
                )
                return ApiEnvelope(data=None, error="Invalid password")

            # If we pass guard, we allow live trading
            execution_config.allow_live_trading = True

        # Persist this permission so reload picks it up?
        # Yes, we need to update the config.
        # But wait, config updates should happen via /config/apply or profile?
        # Here we do a localized update to execution config.
        # Ideally we use atomic write here too.
        # Let's piggyback on config logic or duplicate simple update.
        # We need to update 'execution.allow_live_trading' in the config file.

        config_dir = get_config_dir()
        profile_name = ctx.session.profile_name
        target_path = None

        if profile_name:
            profiles_entry = ctx.config.profiles.get(profile_name)
            if profiles_entry:
                p_path = Path(profiles_entry.config_path)
                if not p_path.is_absolute():
                    p_path = config_dir / p_path
                if p_path.exists():
                    target_path = p_path

        if not target_path:
            target_path = config_dir / "config.yaml"

        # Load, update, save
        try:
            with open(target_path, "r") as f:
                data = yaml.safe_load(f) or {}

            exec_sec = data.get("execution", {})
            exec_sec["mode"] = "live"
            exec_sec["validate_only"] = False
            exec_sec["allow_live_trading"] = True
            # NOTE: We DO NOT set paper_tests_completed=True automatically anymore.

            data["execution"] = exec_sec

            backup_file(target_path)
            atomic_write(target_path, data, dump_func=yaml.safe_dump)

        except Exception as e:
            return ApiEnvelope(
                data=None, error=f"Failed to persist live mode settings: {e}"
            )

    # For other modes, we might just update runtime state or config too?
    # Usually mode change persists.
    # Update in-memory state so subsequent calls reflect the change immediately
    execution_config.mode = new_mode
    execution_config.validate_only = new_mode != "live"
    if new_mode == "live":
        execution_config.allow_live_trading = True

    ctx.session.mode = new_mode
    if hasattr(ctx.config, "session"):
        ctx.config.session.mode = new_mode

    # If the adapter is already initialized, update its config reference too
    if ctx.execution_service and hasattr(ctx.execution_service, "adapter"):
        adapter_conf = getattr(ctx.execution_service.adapter, "config", None)
        if adapter_conf:
            adapter_conf.mode = new_mode
            adapter_conf.validate_only = new_mode != "live"
            if new_mode == "live":
                adapter_conf.allow_live_trading = True

    # NOTE: Re-implementing generic persistence for mode change:
    config_dir = get_config_dir()
    profile_name = ctx.session.profile_name
    target_path = None
    if profile_name:
        profiles_entry = ctx.config.profiles.get(profile_name)
        if profiles_entry:
            p_path = Path(profiles_entry.config_path)
            if not p_path.is_absolute():
                p_path = config_dir / p_path
            if p_path.exists():
                target_path = p_path
    if not target_path:
        target_path = config_dir / "config.yaml"

    try:
        with open(target_path, "r") as f:
            data = yaml.safe_load(f) or {}
        exec_sec = data.get("execution", {})
        exec_sec["mode"] = new_mode
        exec_sec["validate_only"] = new_mode != "live"
        if new_mode == "live":
            exec_sec["allow_live_trading"] = True
        data["execution"] = exec_sec
        backup_file(target_path)
        atomic_write(target_path, data, dump_func=yaml.safe_dump)
    except Exception as e:
        return ApiEnvelope(data=None, error=f"Failed to persist mode: {e}")

    # Trigger reload
    ctx.reinitialize_event.set()

    logger.info(
        "Execution mode updated",
        extra=build_request_log_extra(
            request,
            event="mode_changed",
            old_mode=current_mode,
            new_mode=new_mode,
        ),
    )

    return ApiEnvelope(
        data={
            "mode": new_mode,
            "validate_only": execution_config.validate_only,
            "reloading": True,
        },
        error=None,
    )


@router.post("/credentials/validate", response_model=ApiEnvelope[dict])
async def validate_credentials(
    payload: CredentialPayload, request: Request
) -> ApiEnvelope[dict]:
    """Validate API credentials by pinging a lightweight private Kraken endpoint."""

    missing = [
        field_name
        for field_name, value in (
            ("apiKey", payload.apiKey),
            ("apiSecret", payload.apiSecret),
            ("region", payload.region),
        )
        if not value or not value.strip()
    ]

    if missing:
        return ApiEnvelope(
            data={"valid": False},
            error="apiKey, apiSecret, and region are required.",
        )

    try:
        result = validation_mod.validate_credentials(
            payload.apiKey.strip(),
            payload.apiSecret.strip(),
            region=payload.region.strip(),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Unexpected error during credential validation",
            extra=build_request_log_extra(
                request, event="credential_validation_failed", error=str(exc)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error=(
                "Unexpected error while validating credentials. Please retry or check server logs."
            ),
        )

    if result.status is CredentialStatus.LOADED and result.validated:
        return ApiEnvelope(data={"valid": True}, error=None)

    error = result.error

    if isinstance(error, AuthError):
        logger.warning(
            "Credential validation failed",
            extra=build_request_log_extra(
                request, event="credential_validation_auth_error", error=str(error)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
        )

    if isinstance(error, ServiceUnavailableError):
        logger.warning(
            "Credential validation unavailable",
            extra=build_request_log_extra(
                request, event="credential_validation_unavailable", error=str(error)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Kraken is unavailable or could not be reached. Please retry.",
        )

    if isinstance(error, KrakenAPIError):
        logger.warning(
            "Credential validation failed with API error",
            extra=build_request_log_extra(
                request, event="credential_validation_api_error", error=str(error)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
        )

    logger.warning(
        "Credential validation failed with unexpected service error",
        extra=build_request_log_extra(
            request,
            event="credential_validation_unknown_service_error",
            error=str(error),
        ),
    )
    return ApiEnvelope(
        data={"valid": False},
        error=(
            "Unexpected error while validating credentials. Please retry or check server logs."
        ),
    )
