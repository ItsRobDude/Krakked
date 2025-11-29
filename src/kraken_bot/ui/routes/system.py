"""System and health endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Request
from pydantic import BaseModel

from kraken_bot.connection.exceptions import AuthError, ServiceUnavailableError
from kraken_bot.connection.rest_client import KrakenRESTClient
from kraken_bot.ui.models import ApiEnvelope, SystemHealthPayload

logger = logging.getLogger(__name__)

router = APIRouter()


class CredentialPayload(BaseModel):
    """Payload expected from the UI when validating credentials."""

    apiKey: str
    apiSecret: str
    region: str


class ModeChangePayload(BaseModel):
    """Payload for toggling the execution mode."""

    mode: str


def _context(request: Request):
    return request.app.state.context


def _redacted_config(config) -> dict:
    config_dict = asdict(config)
    ui_config = config_dict.get("ui", {})
    auth_config = ui_config.get("auth")
    if isinstance(auth_config, dict) and "token" in auth_config:
        auth_config["token"] = "***"
    return config_dict


@router.get("/health", response_model=ApiEnvelope[SystemHealthPayload])
async def system_health(request: Request) -> ApiEnvelope[SystemHealthPayload]:
    try:
        ctx = _context(request)
        data_status = ctx.market_data.get_data_status()
        execution_config = ctx.execution_service.adapter.config
        market_data_ok = (
            data_status.rest_api_reachable
            and data_status.websocket_connected
            and data_status.subscription_errors == 0
            and data_status.stale_pairs == 0
        )
        execution_ok = execution_config.mode != "live" or bool(
            getattr(execution_config, "allow_live_trading", False)
        )
        return ApiEnvelope(
            data=SystemHealthPayload(
                rest_api_reachable=data_status.rest_api_reachable,
                websocket_connected=data_status.websocket_connected,
                streaming_pairs=data_status.streaming_pairs,
                stale_pairs=data_status.stale_pairs,
                subscription_errors=data_status.subscription_errors,
                market_data_ok=market_data_ok,
                execution_ok=execution_ok,
                current_mode=execution_config.mode,
                ui_read_only=ctx.config.ui.read_only,
            ),
            error=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch system health")
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/config", response_model=ApiEnvelope[dict])
async def get_config(request: Request) -> ApiEnvelope[dict]:
    try:
        ctx = _context(request)
        return ApiEnvelope(data=_redacted_config(ctx.config), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch config")
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/mode", response_model=ApiEnvelope[dict])
async def set_execution_mode(
    payload: ModeChangePayload, request: Request
) -> ApiEnvelope[dict]:
    ctx = _context(request)

    if ctx.config.ui.read_only:
        logger.warning(
            "Mode change blocked: UI read-only",
            extra={"event": "mode_change_blocked", "requested_mode": payload.mode},
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    new_mode = payload.mode.lower()
    if new_mode not in {"paper", "live"}:
        return ApiEnvelope(data=None, error="Unsupported mode; use 'paper' or 'live'")

    execution_config = ctx.config.execution
    current_mode = execution_config.mode

    if new_mode == current_mode:
        return ApiEnvelope(
            data={"mode": current_mode, "validate_only": execution_config.validate_only},
            error=None,
        )

    if new_mode == "live" and not getattr(execution_config, "allow_live_trading", False):
        logger.warning(
            "Live mode change blocked: allow_live_trading is False",
            extra={"event": "mode_change_blocked", "requested_mode": new_mode},
        )
        return ApiEnvelope(data=None, error="Live trading not permitted by configuration")

    execution_config.mode = new_mode
    execution_config.validate_only = new_mode != "live"
    ctx.execution_service.adapter.config.mode = new_mode
    ctx.execution_service.adapter.config.validate_only = execution_config.validate_only

    if new_mode == "live" and hasattr(ctx.execution_service, "_emit_live_readiness_checklist"):
        ctx.execution_service._emit_live_readiness_checklist()

    logger.info(
        "Execution mode updated",
        extra={
            "event": "mode_changed",
            "old_mode": current_mode,
            "new_mode": new_mode,
            "validate_only": execution_config.validate_only,
        },
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

    ctx = _context(request)
    auth_config = ctx.config.ui.auth
    expected_auth = f"Bearer {auth_config.token}" if auth_config.token else ""

    if auth_config.enabled and request.headers.get("Authorization") != expected_auth:
        logger.warning("Unauthorized credential validation attempt")
        return ApiEnvelope(data={"valid": False}, error="Unauthorized")

    client = KrakenRESTClient(api_key=payload.apiKey, api_secret=payload.apiSecret)

    try:
        client.get_private("Balance")
        return ApiEnvelope(data={"valid": True}, error=None)
    except AuthError as exc:
        logger.warning("Credential validation failed", extra={"error": str(exc)})
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
        )
    except ServiceUnavailableError:
        return ApiEnvelope(
            data={"valid": False},
            error=(
                "Kraken service is unavailable. Try again shortly or continue with caution."
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected error during credential validation")
        return ApiEnvelope(data={"valid": False}, error=str(exc))
