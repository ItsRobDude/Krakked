"""Risk monitoring endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


@router.get("/status")
async def get_risk_status(request: Request):
    ctx = _context(request)
    try:
        status = ctx.strategy_engine.get_risk_status()
        return {"data": asdict(status), "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch risk status")
        return {"data": None, "error": str(exc)}


@router.get("/config")
async def get_risk_config(request: Request):
    ctx = _context(request)
    try:
        data = asdict(ctx.config.risk)
        return {"data": data, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch risk config")
        return {"data": None, "error": str(exc)}


@router.patch("/config")
async def update_risk_config(request: Request):
    ctx = _context(request)
    if ctx.config.ui.read_only:
        logger.warning("Risk config update blocked: UI read-only", extra={"event": "risk_config_blocked"})
        return {"data": None, "error": "UI is in read-only mode"}

    try:
        payload = await request.json()
    except Exception:  # pragma: no cover - malformed body
        return {"data": None, "error": "Invalid JSON payload"}

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
                extra={"event": "risk_config_updated", "fields": updated_fields},
            )

        return {"data": asdict(ctx.config.risk), "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to update risk config")
        return {"data": None, "error": str(exc)}
