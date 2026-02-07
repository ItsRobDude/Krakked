
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kraken_bot.ui.api import AuthMiddleware


def test_auth_middleware_blocks_unauthorized():
    app = FastAPI()

    @app.get("/api/protected")
    def protected():
        return {"message": "secret"}

    app.add_middleware(AuthMiddleware, token="secret-token")
    client = TestClient(app)

    # No header
    response = client.get("/api/protected")
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}

    # Wrong header
    response = client.get("/api/protected", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401

    # Correct header
    response = client.get("/api/protected", headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200
    assert response.json() == {"message": "secret"}


def test_auth_middleware_allows_health():
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    app.add_middleware(AuthMiddleware, token="secret-token")
    client = TestClient(app)

    # No header needed for health
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
