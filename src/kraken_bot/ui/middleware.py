"""Middleware for managing application lifecycle access."""

import logging
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from kraken_bot.ui.logging import build_request_log_extra

logger = logging.getLogger(__name__)


class LifecycleMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces access control based on the application lifecycle state.

    In setup/locked mode (ctx.is_setup_mode=True), restricts access to a strict
    allowlist of endpoints necessary for bootstrapping the system. All other
    requests are blocked with a 503 Service Unavailable error.
    """

    def __init__(self, app, base_path: str = ""):
        super().__init__(app)
        normalized_base = base_path.rstrip("/")

        # Exact match allowed paths (method-agnostic in set, but checked specifically)
        # We store them without method here, logic will check method+path
        self._allowed_paths_exact = {
            f"{normalized_base}/api/system/session",
            f"{normalized_base}/api/system/health",
            f"{normalized_base}/api/system/profiles",
            f"{normalized_base}/api/health",
            f"{normalized_base}/api/config/runtime",
        }

        # POST-only exact matches
        self._allowed_post_exact = {
            f"{normalized_base}/api/system/reset",
        }

        # Prefix allowed paths
        self._allowed_prefixes = (
            f"{normalized_base}/api/system/setup/",
            f"{normalized_base}/api/system/accounts/",
        )

        self._api_prefix = f"{normalized_base}/api"

    async def dispatch(self, request: Request, call_next: Callable):
        try:
            ctx = getattr(request.app.state, "context", None)
        except AttributeError:
            # Should not happen if initialized correctly
            return await call_next(request)

        if ctx is None:
             # Safety fallback
             return await call_next(request)

        # If unlocked, allow all
        if not ctx.is_setup_mode:
            return await call_next(request)

        path = request.url.path
        method = request.method

        # Check Allowlist

        # 1. Exact matches (GET usually, but we check method for strictness if needed)
        # Requirement: EXACT PATH + METHOD for session/health/profiles
        if method == "GET" and path in self._allowed_paths_exact:
            return await call_next(request)

        # 2. POST exact matches
        if method == "POST" and path in self._allowed_post_exact:
            return await call_next(request)

        # 3. Prefix matches (Any Method)
        if any(path.startswith(prefix) for prefix in self._allowed_prefixes):
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
                    "error": "Setup required",
                },
                status_code=503,
            )

        # Non-API paths (static files etc) are allowed
        return await call_next(request)
