import secrets
from unittest.mock import patch
from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_secrets_compare_digest():
    """Verify that AuthMiddleware uses constant-time comparison."""
    context = build_test_context(auth_enabled=True, auth_token="secret-token", read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # We patch secrets.compare_digest to ensure it is called.
    # Use a protected endpoint: /api/config/runtime
    with patch("secrets.compare_digest", wraps=secrets.compare_digest) as mock_digest:
        response = client.get("/api/config/runtime", headers={"Authorization": "Bearer secret-token"})
        # 200 OK means authorized
        assert response.status_code == 200
        assert mock_digest.called, "AuthMiddleware should use secrets.compare_digest for timing safety"


def test_auth_middleware_handles_non_ascii_safely():
    """Verify that non-ASCII headers are handled without crashing."""
    context = build_test_context(auth_enabled=True, auth_token="secret-token", read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # Sending non-ASCII characters in headers
    # We pass bytes to avoid httpx enforcing ASCII on the client side.
    # Uvicorn/Starlette will decode these bytes (typically latin-1).
    # "café" in utf-8 is b'caf\xc3\xa9'
    headers = {"Authorization": "Bearer café".encode("utf-8")}

    # Use a protected endpoint: /api/config/runtime
    response = client.get("/api/config/runtime", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"
