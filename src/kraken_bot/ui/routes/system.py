"""System and health endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


@router.get("/health")
async def system_health(request: Request):
    try:
        ctx = _context(request)
        data_status = ctx.market_data.get_data_status()
        return {
            "data": {
                "rest_api_reachable": data_status.rest_api_reachable,
                "websocket_connected": data_status.websocket_connected,
                "streaming_pairs": data_status.streaming_pairs,
                "stale_pairs": data_status.stale_pairs,
                "subscription_errors": data_status.subscription_errors,
            },
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to fetch system health")
        return {"data": None, "error": str(exc)}
