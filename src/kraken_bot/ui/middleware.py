"""Middleware for blocking service access during setup mode."""

import logging
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from kraken_bot.ui.logging import build_request_log_extra

logger = logging.getLogger(__name__)


class SetupAwareMiddleware(BaseHTTPMiddleware):
    """
    Middleware that inspects the request path and the application state.
    If the application is in setup mode and the path requires services,
    it returns a 503 Service Unavailable error.
    """

    def __init__(self, app, base_path: str = ""):
        super().__init__(app)
        normalized_base = base_path.rstrip("/")
        # Define paths that ARE allowed in setup mode
        self._allowed_paths = {
            f"{normalized_base}/api/system/setup/status",
            f"{normalized_base}/api/system/setup/config",
            f"{normalized_base}/api/system/setup/credentials",
            f"{normalized_base}/api/system/setup/unlock",
            f"{normalized_base}/api/system/setup/forget",
            f"{normalized_base}/api/system/reset",
            f"{normalized_base}/api/system/health",
            f"{normalized_base}/api/health",
            # Fallback for root or docs if needed, but primarily API
        }
        self._api_prefix = f"{normalized_base}/api"

    async def dispatch(self, request: Request, call_next: Callable):  # type: ignore[override]
        # We need to access the app context.
        # Note: request.app.state.context might not be populated if app startup failed?
        # But create_api ensures it.
        try:
            ctx = request.app.state.context
        except AttributeError:
            # Should not happen in normal operation
            return await call_next(request)

        # If not in setup mode, proceed
        if not ctx.is_setup_mode:
            return await call_next(request)

        path = request.url.path
        # If path is allowed, proceed
        if path in self._allowed_paths:
            return await call_next(request)

        # If it's an API call but not allowed -> Block
        if path.startswith(self._api_prefix):
            logger.warning(
                "Request blocked: System is in setup mode",
                extra=build_request_log_extra(request, event="setup_mode_block"),
            )
            return JSONResponse(
                {
                    "data": None,
                    "error": "System is in setup mode. Please complete configuration.",
                },
                status_code=503,
            )

        # For static files or other routes, we might allow or block.
        # Assuming we only care about API protection here.
        return await call_next(request)
