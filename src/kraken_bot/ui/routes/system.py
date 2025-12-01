"""System and health endpoints."""

from __future__ import annotations

import binascii
import logging
from dataclasses import asdict

from fastapi import APIRouter, Request
from pydantic import BaseModel

from kraken_bot import APP_VERSION
from kraken_bot.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    ServiceUnavailableError,
)
from kraken_bot.connection import rest_client
from kraken_bot.market_data.api import MarketDataStatus
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope, SystemHealthPayload, SystemMetricsPayload

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
            market_data_max_staleness = getattr(market_data_health, "max_staleness", None)

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
            market_data_reason = None if market_data_ok else (
                "data_stale" if market_data_stale else "connection_issue"
            )

        metrics_market_data_ok = metrics_snapshot.get("market_data_ok")
        metrics_market_data_stale = metrics_snapshot.get("market_data_stale")
        metrics_market_data_reason = metrics_snapshot.get("market_data_reason")
        metrics_market_data_max_staleness = metrics_snapshot.get("market_data_max_staleness")

        metrics_has_update = bool(
            metrics_market_data_reason is not None
            or metrics_market_data_max_staleness is not None
            or metrics_market_data_ok
            or metrics_market_data_stale
        )

        if metrics_has_update:
            market_data_ok = bool(metrics_market_data_ok)
            market_data_stale = bool(metrics_market_data_stale)
            market_data_reason = metrics_market_data_reason
            market_data_max_staleness = metrics_market_data_max_staleness

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


@router.get("/metrics", response_model=ApiEnvelope[SystemMetricsPayload])
async def system_metrics(request: Request) -> ApiEnvelope[SystemMetricsPayload]:
    try:
        metrics = _context(request).metrics
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

    if ctx.config.ui.read_only:
        logger.warning(
            "Mode change blocked: UI read-only",
            extra=build_request_log_extra(
                request, event="mode_change_blocked", requested_mode=payload.mode
            ),
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
            extra=build_request_log_extra(
                request, event="mode_change_blocked", requested_mode=new_mode
            ),
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

    ctx = _context(request)
    auth_config = ctx.config.ui.auth
    expected_auth = f"Bearer {auth_config.token}" if auth_config.token else ""
    auth_header = request.headers.get("Authorization", "")

    if auth_config.enabled and auth_header != expected_auth:
        logger.warning(
            "Unauthorized credential validation attempt",
            extra=build_request_log_extra(request, event="credential_validation_unauthorized"),
        )
        return ApiEnvelope(data={"valid": False}, error="Unauthorized")

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

    client = rest_client.KrakenRESTClient(
        api_key=payload.apiKey.strip(), api_secret=payload.apiSecret.strip()
    )

    try:
        # Balance is a safe, read-only private endpoint that verifies signing without
        # mutating user state.
        client.get_private("Balance")
        return ApiEnvelope(data={"valid": True}, error=None)
    except AuthError as exc:
        logger.warning(
            "Credential validation failed",
            extra=build_request_log_extra(
                request, event="credential_validation_auth_error", error=str(exc)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
        )
    except ServiceUnavailableError as exc:
        logger.warning(
            "Credential validation unavailable",
            extra=build_request_log_extra(
                request, event="credential_validation_unavailable", error=str(exc)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error=("Kraken is unavailable or could not be reached. Please retry."),
        )
    except KrakenAPIError as exc:
        logger.warning(
            "Credential validation failed with API error",
            extra=build_request_log_extra(
                request, event="credential_validation_api_error", error=str(exc)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
        )
    except binascii.Error as exc:
        logger.warning(
            "Credential validation failed",
            extra=build_request_log_extra(
                request, event="credential_validation_auth_error", error=str(exc)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
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
