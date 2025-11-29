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
