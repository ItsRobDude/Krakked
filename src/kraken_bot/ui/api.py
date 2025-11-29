"""FastAPI application factory for the UI control plane."""

from __future__ import annotations

import logging
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from kraken_bot.ui.context import AppContext
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.routes import execution_router, portfolio_router, risk_router, strategies_router, system_router

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Simple bearer-token middleware for UI API endpoints."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: Callable):  # type: ignore[override]
        if request.url.path.startswith("/api"):
            auth_header = request.headers.get("Authorization")
            expected = f"Bearer {self._token}" if self._token else ""
            if auth_header != expected:
                return JSONResponse({"data": None, "error": "Unauthorized"}, status_code=401)
        return await call_next(request)


def create_api(context: AppContext) -> FastAPI:
    """Build a FastAPI app wired with routers and optional auth."""

    middleware = []
    auth_config = context.config.ui.auth
    if auth_config.enabled:
        middleware.append(Middleware(AuthMiddleware, token=auth_config.token))

    app = FastAPI(middleware=middleware)
    app.state.context = context

    base_path = context.config.ui.base_path.rstrip("/") or ""

    app.include_router(portfolio_router, prefix=f"{base_path}/api/portfolio")
    app.include_router(risk_router, prefix=f"{base_path}/api/risk")
    app.include_router(strategies_router, prefix=f"{base_path}/api/strategies")
    app.include_router(execution_router, prefix=f"{base_path}/api/execution")
    app.include_router(system_router, prefix=f"{base_path}/api/system")

    @app.middleware("http")
    async def inject_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get(f"{base_path}/api/health")
    async def healthcheck():
        return {"data": {"status": "ok"}, "error": None}

    logger.info(
        "UI API initialized",
        extra=build_request_log_extra(None, event="ui_initialized", base_path=base_path or "/", auth=auth_config.enabled),
    )
    return app


__all__ = ["create_api"]
