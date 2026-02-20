"""FastAPI application factory for the UI control plane."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from kraken_bot.ui.context import AppContext
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.middleware import LifecycleMiddleware
from kraken_bot.ui.routes import (
    config_router,
    execution_router,
    portfolio_router,
    presets_router,
    risk_router,
    strategies_router,
    system_router,
)

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Simple bearer-token middleware for UI API endpoints."""

    def __init__(self, app, token: str, base_path: str = ""):
        super().__init__(app)
        self._token = token
        normalized_base = base_path.rstrip("/") or ""
        self._protected_prefix = f"{normalized_base}/api" or "/api"
        self._health_paths = {
            f"{self._protected_prefix}/health",
            f"{self._protected_prefix}/system/health",
        }

    async def dispatch(self, request: Request, call_next: Callable):  # type: ignore[override]
        path = request.url.path

        if path.startswith(self._protected_prefix) and path not in self._health_paths:
            auth_header = request.headers.get("Authorization") or ""
            expected = f"Bearer {self._token}" if self._token else ""
            # Aegis: Prevent timing attacks on token comparison
            if not secrets.compare_digest(auth_header, expected):
                logger.warning(
                    "Unauthorized UI API request",
                    extra=build_request_log_extra(
                        request,
                        event="ui_auth_unauthorized",
                    ),
                )
                return JSONResponse(
                    {"data": None, "error": "Unauthorized"}, status_code=401
                )
        return await call_next(request)


def create_api(context: AppContext) -> FastAPI:
    """Build a FastAPI app wired with routers and optional auth."""

    base_path = context.config.ui.base_path.rstrip("/") or ""

    middleware = [
        Middleware(LifecycleMiddleware, base_path=base_path),
    ]
    auth_config = context.config.ui.auth
    if auth_config.enabled and auth_config.token:
        middleware.append(
            Middleware(AuthMiddleware, token=auth_config.token, base_path=base_path)
        )

    app = FastAPI(middleware=middleware)
    app.state.context = context

    app.include_router(portfolio_router, prefix=f"{base_path}/api/portfolio")
    app.include_router(risk_router, prefix=f"{base_path}/api/risk")
    app.include_router(strategies_router, prefix=f"{base_path}/api/strategies")
    app.include_router(execution_router, prefix=f"{base_path}/api/execution")
    app.include_router(system_router, prefix=f"{base_path}/api/system")
    app.include_router(config_router, prefix=f"{base_path}/api/config")
    app.include_router(presets_router, prefix=f"{base_path}/api/presets")

    @app.middleware("http")
    async def inject_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    async def healthcheck():
        return {"data": {"status": "ok"}, "error": None}

    health_router = APIRouter()
    health_router.add_api_route(
        f"{base_path}/api/health", healthcheck, methods=["GET"], name="healthcheck"
    )

    app.include_router(health_router)

    # Mount UI static files
    # We are in src/kraken_bot/ui/api.py. Repo root is 4 levels up.
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    ui_dir = repo_root / "ui" / "dist"

    # IMPORTANT: StaticFiles mount MUST remain the final route registration; do not add routers after this.
    if ui_dir.exists() and (ui_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    else:
        logger.warning(
            "UI build not found at %s; serving API only.",
            ui_dir,
            extra={"event": "ui_build_missing", "ui_dir": str(ui_dir)},
        )

    logger.info(
        "UI API initialized",
        extra=build_request_log_extra(
            None,
            event="ui_initialized",
            base_path=base_path or "/",
            auth=auth_config.enabled,
        ),
    )
    return app


__all__ = ["create_api"]
