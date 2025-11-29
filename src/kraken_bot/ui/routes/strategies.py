"""Strategy state endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


@router.get("/")
async def get_strategies(request: Request):
    ctx = _context(request)
    try:
        strategies = [asdict(state) for state in ctx.strategy_engine.get_strategy_state()]
        return {"data": strategies, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch strategies")
        return {"data": None, "error": str(exc)}
