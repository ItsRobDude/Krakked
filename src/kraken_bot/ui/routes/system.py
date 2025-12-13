"""System and health endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Literal, Optional

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
from kraken_bot.password_store import (
    delete_master_password,
    save_master_password,
)
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


class ProfileSummaryPayload(BaseModel):
    """Simplified profile metadata for UI selection."""

    name: str
    description: str


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

        if payload.remember:
            save_master_password(payload.password)

        if ctx.is_setup_mode:
            logger.info(
                "Unlock successful, signaling re-initialization",
                extra=build_request_log_extra(request, event="setup_unlock_success"),
            )
            ctx.reinitialize_event.set()

        return ApiEnvelope(data={"success": True}, error=None)

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
                extra=build_request_log_extra(request, event="reset_keyring_error", error=str(exc))
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
        logger.warning(
            "Session start blocked: UI read-only",
            extra=build_request_log_extra(request, event="session_start_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    execution_config = ctx.config.execution
    new_mode = payload.mode

    if new_mode == "live" and not getattr(
        execution_config, "allow_live_trading", False
    ):
        logger.warning(
            "Session start blocked: live trading not permitted by configuration",
            extra=build_request_log_extra(
                request,
                event="session_start_blocked_live",
                requested_mode=new_mode,
            ),
        )
        return ApiEnvelope(
            data=None, error="Live trading not permitted by configuration"
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

    # Sync ML strategies with session-level ml_enabled flag when it changes.
    # Any strategy whose type starts with "machine_learning" is treated as part of the
    # ML group, including alternate model variants.
    if payload.ml_enabled != old_ml_enabled:
        ml_enabled = bool(payload.ml_enabled)

        for sid, strat_cfg in ctx.config.strategies.configs.items():
            if not getattr(strat_cfg, "type", "").startswith("machine_learning"):
                continue

            strat_cfg.enabled = ml_enabled

            if ml_enabled:
                if sid not in ctx.config.strategies.enabled:
                    ctx.config.strategies.enabled.append(sid)
            else:
                if sid in ctx.config.strategies.enabled:
                    ctx.config.strategies.enabled.remove(sid)

            if sid in ctx.strategy_engine.strategy_states:
                ctx.strategy_engine.strategy_states[sid].enabled = ml_enabled

    execution_config.mode = new_mode
    execution_config.validate_only = new_mode != "live"
    ctx.execution_service.adapter.config.mode = new_mode
    ctx.execution_service.adapter.config.validate_only = execution_config.validate_only

    if new_mode == "live" and hasattr(
        ctx.execution_service, "_emit_live_readiness_checklist"
    ):
        ctx.execution_service._emit_live_readiness_checklist()

    dump_runtime_overrides(ctx.config, session=ctx.session)

    logger.info(
        "Session started",
        extra=build_request_log_extra(
            request,
            event="session_started",
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
        logger.warning(
            "Session stop blocked: UI read-only",
            extra=build_request_log_extra(request, event="session_stop_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    ctx.session.active = False

    if hasattr(ctx.config, "session"):
        ctx.config.session.active = False

    dump_runtime_overrides(ctx.config, session=ctx.session)

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
        logger.warning(
            "Mode change blocked: UI read-only",
            extra=build_request_log_extra(
                request, event="mode_change_blocked", requested_mode=payload.mode
            ),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

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

    if new_mode == "live" and not getattr(
        execution_config, "allow_live_trading", False
    ):
        logger.warning(
            "Live mode change blocked: allow_live_trading is False",
            extra=build_request_log_extra(
                request, event="mode_change_blocked", requested_mode=new_mode
            ),
        )
        return ApiEnvelope(
            data=None, error="Live trading not permitted by configuration"
        )

    execution_config.mode = new_mode
    execution_config.validate_only = new_mode != "live"
    ctx.execution_service.adapter.config.mode = new_mode
    ctx.execution_service.adapter.config.validate_only = execution_config.validate_only

    ctx.session.mode = new_mode
    if hasattr(ctx.config, "session"):
        ctx.config.session.mode = new_mode

    if new_mode == "live" and hasattr(
        ctx.execution_service, "_emit_live_readiness_checklist"
    ):
        ctx.execution_service._emit_live_readiness_checklist()

    logger.info(
        "Execution mode updated",
        extra=build_request_log_extra(
            request,
            event="mode_changed",
            old_mode=current_mode,
            new_mode=new_mode,
            validate_only=execution_config.validate_only,
        ),
    )

    return ApiEnvelope(
        data={"mode": new_mode, "validate_only": execution_config.validate_only},
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
