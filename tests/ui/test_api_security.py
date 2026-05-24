from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from krakked.ui import api as api_module
from krakked.ui.api import create_api
from krakked.ui.context import AppContext
from krakked.ui.middleware import SecurityHeadersMiddleware


def _assert_baseline_csp(value: str) -> None:
    assert "default-src 'self'" in value
    assert "frame-ancestors 'none'" in value
    assert "connect-src 'self'" in value
    assert " ws:" not in value
    assert " wss:" not in value


@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["secret"], indirect=True)
def test_auth_middleware_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch, client: Any
) -> None:
    calls: list[tuple[bytes, bytes]] = []

    def _compare_digest(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return False

    monkeypatch.setattr(api_module.std_secrets, "compare_digest", _compare_digest)

    response = client.get(
        "/api/config/runtime",
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401
    assert calls == [(b"Bearer wrong", b"Bearer secret")]


def test_security_headers_are_added_to_api_responses(client: Any) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    _assert_baseline_csp(response.headers["Content-Security-Policy"])


@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["secret"], indirect=True)
def test_security_headers_are_added_to_auth_failures(mock_context: AppContext) -> None:
    app = create_api(mock_context)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(api_module.std_secrets, "compare_digest", lambda *_: False)

        client = TestClient(app)
        response = client.get(
            "/api/config/runtime",
            headers={"Authorization": "Bearer wrong"},
        )

    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    _assert_baseline_csp(response.headers["Content-Security-Policy"])


def test_security_headers_preserve_existing_csp() -> None:
    app = FastAPI(middleware=[Middleware(SecurityHeadersMiddleware)])

    @app.get("/custom")
    def _custom_response() -> JSONResponse:
        return JSONResponse(
            {"ok": True},
            headers={"Content-Security-Policy": "default-src 'none'"},
        )

    response = TestClient(app).get("/custom")

    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"
