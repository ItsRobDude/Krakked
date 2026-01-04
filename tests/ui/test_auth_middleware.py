
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from kraken_bot.ui.api import AuthMiddleware

def test_auth_middleware_rejects_invalid_token():
    """Verify that AuthMiddleware rejects requests with incorrect tokens."""
    app = FastAPI()

    @app.get("/api/protected")
    def protected_endpoint():
        return {"data": "secret"}

    # Initialize middleware with a known token
    app.add_middleware(AuthMiddleware, token="valid_token")

    client = TestClient(app)

    # Case 1: No token
    response = client.get("/api/protected")
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}

    # Case 2: Invalid token
    response = client.get("/api/protected", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}

    # Case 3: Valid token
    response = client.get("/api/protected", headers={"Authorization": "Bearer valid_token"})
    assert response.status_code == 200
    assert response.json() == {"data": "secret"}

def test_auth_middleware_allows_health_check():
    """Verify that health check endpoints bypass authentication."""
    app = FastAPI()

    @app.get("/api/health")
    def health_endpoint():
        return {"status": "ok"}

    app.add_middleware(AuthMiddleware, token="valid_token")
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
