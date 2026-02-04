from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_enforcement():
    """Verify that AuthMiddleware enforces token checks correctly."""
    token = "secret-token-123"
    context = build_test_context(auth_enabled=True, auth_token=token, read_only=False)
    # Ensure setup mode is False so we don't get 503 on config endpoint
    context.is_setup_mode = False

    app = create_api(context)
    client = TestClient(app)

    # Protected endpoint: /api/system/config
    url = "/api/system/config"

    # 1. No header -> 401
    resp = client.get(url)
    assert resp.status_code == 401
    assert resp.json()["error"] == "Unauthorized"

    # 2. Wrong token -> 401
    resp = client.get(url, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401

    # 3. Correct token -> 200
    resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_auth_middleware_timing_safe_compare_logic():
    """
    Functionally verifies that the auth check works with what will be the secure implementation.
    """
    token = "a" * 32
    context = build_test_context(auth_enabled=True, auth_token=token, read_only=False)
    context.is_setup_mode = False

    app = create_api(context)
    client = TestClient(app)

    url = "/api/system/config"

    # Check that a partial match fails
    # This ensures that even if we implement compare_digest, it still rejects invalid tokens
    partial = "Bearer " + ("a" * 31) + "b"
    resp = client.get(url, headers={"Authorization": partial})
    assert resp.status_code == 401

    # Check empty token case (should be unauthorized if auth is enabled)
    resp = client.get(url, headers={"Authorization": "Bearer "})
    assert resp.status_code == 401
