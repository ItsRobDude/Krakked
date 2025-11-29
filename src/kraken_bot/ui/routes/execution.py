"""Execution-related endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


@router.get("/open_orders")
async def get_open_orders(request: Request):
    ctx = _context(request)
    try:
        open_orders = [asdict(order) for order in ctx.execution_service.get_open_orders()]
        return {"data": open_orders, "error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch open orders")
        return {"data": None, "error": str(exc)}
