"""Risk monitoring endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope, KillSwitchPayload, RiskConfigPayload, RiskStatusPayload

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


@router.get("/status", response_model=ApiEnvelope[RiskStatusPayload])
async def get_risk_status(request: Request) -> ApiEnvelope[RiskStatusPayload]:
    ctx = _context(request)
    try:
        status = ctx.strategy_engine.get_risk_status()
        data = RiskStatusPayload(**status.__dict__)
        return ApiEnvelope(data=data, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch risk status",
            extra=build_request_log_extra(request, event="risk_status_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/config", response_model=ApiEnvelope[RiskConfigPayload])
async def get_risk_config(request: Request) -> ApiEnvelope[RiskConfigPayload]:
    ctx = _context(request)
    try:
        data = RiskConfigPayload(**ctx.config.risk.__dict__)
        return ApiEnvelope(data=data, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch risk config",
            extra=build_request_log_extra(request, event="risk_config_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.patch("/config", response_model=ApiEnvelope[RiskConfigPayload])
async def update_risk_config(request: Request) -> ApiEnvelope[RiskConfigPayload]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Risk config update blocked: UI read-only",
            extra=build_request_log_extra(request, event="risk_config_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        payload = await request.json()
    except Exception:  # pragma: no cover - malformed body
        return ApiEnvelope(data=None, error="Invalid JSON payload")

    updated_fields = {}
    try:
        for field, value in payload.items():
            if hasattr(ctx.config.risk, field):
                setattr(ctx.config.risk, field, value)
                setattr(ctx.strategy_engine.risk_engine.config, field, value)
                updated_fields[field] = value

        if updated_fields:
            logger.info(
                "Updated risk config",
                extra=build_request_log_extra(request, event="risk_config_updated", fields=updated_fields),
            )

        return ApiEnvelope(data=RiskConfigPayload(**ctx.config.risk.__dict__), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to update risk config",
            extra=build_request_log_extra(request, event="risk_config_update_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/kill_switch", response_model=ApiEnvelope[RiskStatusPayload])
async def set_kill_switch(request: Request) -> ApiEnvelope[RiskStatusPayload]:
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning(
            "Kill switch update blocked: UI read-only",
            extra=build_request_log_extra(request, event="kill_switch_blocked"),
        )
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    try:
        payload = KillSwitchPayload(**await request.json())
    except Exception:  # pragma: no cover - malformed body
        return ApiEnvelope(data=None, error="Invalid JSON payload")

    try:
        ctx.strategy_engine.set_manual_kill_switch(payload.active)
        status = ctx.strategy_engine.get_risk_status()
        logger.info(
            "Updated manual kill switch",
            extra=build_request_log_extra(
                request, event="kill_switch_updated", active=payload.active
            ),
        )
        return ApiEnvelope(data=RiskStatusPayload(**status.__dict__), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to update kill switch",
            extra=build_request_log_extra(request, event="kill_switch_update_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))
