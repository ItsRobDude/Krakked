from __future__ import annotations

from typing import Any

import pytest

from krakked.ui import api as api_module
from krakked.ui.api import create_api
from krakked.ui.context import AppContext


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


@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["secret"], indirect=True)
def test_security_headers_are_added_to_auth_failures(mock_context: AppContext) -> None:
    app = create_api(mock_context)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(api_module.std_secrets, "compare_digest", lambda *_: False)
        from starlette.testclient import TestClient

        client = TestClient(app)
        response = client.get(
            "/api/config/runtime",
            headers={"Authorization": "Bearer wrong"},
        )

    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
