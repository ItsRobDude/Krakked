from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_compare_digest_compatibility(monkeypatch):
    """
    Verifies that the auth middleware correctly handles various token states
    (valid, invalid, empty) ensuring compatibility with secrets.compare_digest.
    """
    context = build_test_context(
        auth_enabled=True, auth_token="supersecret", read_only=False
    )
    # Mock checks that might fail in isolation
    context.is_setup_mode = False

    app = create_api(context)
    client = TestClient(app)

    # Use a protected endpoint: /api/system/config
    endpoint = "/api/system/config"

    # 1. Valid Token
    resp = client.get(endpoint, headers={"Authorization": "Bearer supersecret"})
    assert (
        resp.status_code == 200
    ), f"Valid token should be accepted. Got {resp.status_code} {resp.text}"

    # 2. Invalid Token
    resp = client.get(endpoint, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401, "Invalid token should be rejected"

    # 3. Empty Header
    resp = client.get(endpoint, headers={"Authorization": ""})
    assert resp.status_code == 401, "Empty header should be rejected"

    # 4. Missing Header
    resp = client.get(endpoint)
    assert resp.status_code == 401, "Missing header should be rejected"

    # 5. Malformed Header (no Bearer)
    resp = client.get(endpoint, headers={"Authorization": "supersecret"})
    assert resp.status_code == 401, "Malformed header should be rejected"


def test_auth_middleware_empty_token_config():
    """
    Verifies behavior when the configured token is empty (middleware skipped).
    """
    context = build_test_context(auth_enabled=True, auth_token="", read_only=False)
    context.is_setup_mode = False

    app = create_api(context)
    client = TestClient(app)

    # Should be accessible because middleware is not added
    resp = client.get("/api/system/config")
    assert resp.status_code == 200
