"""Configuration snapshot endpoints for the UI."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kraken_bot.ui.logging import build_request_log_extra

logger = logging.getLogger(__name__)

router = APIRouter()


def _context(request: Request):
    return request.app.state.context


@router.get("/runtime")
async def get_runtime_config(request: Request) -> JSONResponse:
    """Return the current runtime AppConfig as a JSON attachment."""
    ctx = _context(request)
    try:
        config_dict = asdict(ctx.config)

        return JSONResponse(
            content={"data": config_dict, "error": None},
            headers={
                "Content-Disposition": 'attachment; filename="krakked-config-runtime.json"'
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to dump runtime config",
            extra=build_request_log_extra(request, event="config_runtime_failed"),
        )
        return JSONResponse(
            content={"data": None, "error": str(exc)},
            status_code=500,
        )
