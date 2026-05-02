"""FastAPI application factory for the UI control plane."""

from __future__ import annotations

import logging
import os
import secrets as std_secrets
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from krakked.ui.context import AppContext
from krakked.ui.logging import build_request_log_extra
from krakked.ui.middleware import LifecycleMiddleware
from krakked.ui.routes import (
    config_router,
    execution_router,
    portfolio_router,
    presets_router,
    risk_router,
    strategies_router,
    system_router,
)

logger = logging.getLogger(__name__)


def _resolve_ui_dist_dir() -> Path:
    """Locate the built frontend assets for local dev and packaged runtimes."""

    explicit_dir = os.environ.get("KRAKKED_UI_DIST_DIR") or os.environ.get("UI_DIST_DIR")
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    # We are in src/krakked/ui/api.py. Repo root is 4 levels up.
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "ui" / "dist"


class AuthMiddleware(BaseHTTPMiddleware):
    """Simple bearer-token middleware for UI API endpoints."""

    def __init__(self, app, token: str, base_path: str = ""):
        super().__init__(app)
        self._token = token
        normalized_base = base_path.rstrip("/") or ""
        self._protected_prefixes = {"/api"}
        if normalized_base:
            self._protected_prefixes.add(f"{normalized_base}/api")
        self._health_paths = {
            "/api/health",
            "/api/system/health",
        }
        if normalized_base:
            self._health_paths.update(
                {
                    f"{normalized_base}/api/health",
                    f"{normalized_base}/api/system/health",
                }
            )

    async def dispatch(self, request: Request, call_next: Callable):  # type: ignore[override]
        path = request.url.path

        if (
            any(path.startswith(prefix) for prefix in self._protected_prefixes)
            and path not in self._health_paths
        ):
            auth_header = request.headers.get("Authorization") or ""
            expected = f"Bearer {self._token}" if self._token else ""
            # Aegis: timing attack on token -> compare_digest mitigation (no exploit details)
            if not std_secrets.compare_digest(auth_header.encode("utf-8"), expected.encode("utf-8")):
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

    api_prefixes = [""]
    if base_path:
        api_prefixes.insert(0, base_path)

    for api_prefix in dict.fromkeys(api_prefixes):
        app.include_router(portfolio_router, prefix=f"{api_prefix}/api/portfolio")
        app.include_router(risk_router, prefix=f"{api_prefix}/api/risk")
        app.include_router(strategies_router, prefix=f"{api_prefix}/api/strategies")
        app.include_router(execution_router, prefix=f"{api_prefix}/api/execution")
        app.include_router(system_router, prefix=f"{api_prefix}/api/system")
        app.include_router(config_router, prefix=f"{api_prefix}/api/config")
        app.include_router(presets_router, prefix=f"{api_prefix}/api/presets")

    @app.middleware("http")
    async def inject_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    async def healthcheck():
        return {"data": {"status": "ok"}, "error": None}

    health_router = APIRouter()
    for api_prefix in dict.fromkeys(api_prefixes):
        route_path = f"{api_prefix}/api/health" if api_prefix else "/api/health"
        route_name = "healthcheck" if not api_prefix else f"healthcheck-{api_prefix}"
        health_router.add_api_route(
            route_path,
            healthcheck,
            methods=["GET"],
            name=route_name,
        )

    app.include_router(health_router)

    # Mount UI static files.
    ui_dir = _resolve_ui_dist_dir()

    # IMPORTANT: StaticFiles mount MUST remain the final route registration; do not add routers after this.
    if ui_dir.exists() and (ui_dir / "index.html").exists():
        if base_path:
            app.mount(base_path, StaticFiles(directory=str(ui_dir), html=True), name="ui-base-path")
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
