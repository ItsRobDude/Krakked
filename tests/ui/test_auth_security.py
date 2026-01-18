from __future__ import annotations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
import hmac
from kraken_bot.ui.api import AuthMiddleware


class MockContext:
    def __init__(self):
        self.config = MagicMock()
        self.config.ui.base_path = ""
        self.config.ui.auth.enabled = True
        self.config.ui.auth.token = "secret_token"


def create_app_with_auth(token: str = "secret_token"):
    app = FastAPI()
    app.add_middleware(AuthMiddleware, token=token)

    @app.get("/api/protected")
    def protected_route():
        return {"data": "secret_data"}

    return app


def test_auth_middleware_allow_valid_token():
    """Verify that the middleware allows requests with the correct token."""
    app = create_app_with_auth()
    client = TestClient(app)

    response = client.get("/api/protected", headers={"Authorization": "Bearer secret_token"})
    assert response.status_code == 200
    assert response.json() == {"data": "secret_data"}


def test_auth_middleware_deny_invalid_token():
    """Verify that the middleware denies requests with incorrect token."""
    app = create_app_with_auth()
    client = TestClient(app)

    response = client.get("/api/protected", headers={"Authorization": "Bearer wrong_token"})
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}


def test_auth_middleware_deny_missing_token():
    """Verify that the middleware denies requests with missing token."""
    app = create_app_with_auth()
    client = TestClient(app)

    response = client.get("/api/protected")
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}


def test_auth_uses_hmac_compare_digest(monkeypatch):
    """Verify that hmac.compare_digest is used for token comparison."""
    # We use a wrapped version of compare_digest to verify it was called
    original_compare = hmac.compare_digest

    mock_compare = MagicMock(side_effect=original_compare)
    monkeypatch.setattr(hmac, "compare_digest", mock_compare)

    app = create_app_with_auth()
    client = TestClient(app)

    # Trigger the check
    client.get("/api/protected", headers={"Authorization": "Bearer secret_token"})

    # Assert called
    mock_compare.assert_called()

    # Assert arguments were encoded (bytes)
    call_args = mock_compare.call_args
    assert isinstance(call_args[0][0], bytes)
    assert isinstance(call_args[0][1], bytes)
