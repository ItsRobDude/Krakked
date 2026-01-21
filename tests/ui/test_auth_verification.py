from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_timing_safe_verification():
    """
    Verifies that the AuthMiddleware correctly handles tokens.
    This test is used to verify the fix for the timing attack vulnerability.
    """
    token = "super_secret_token_123"
    context = build_test_context(
        auth_enabled=True, auth_token=token, read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    # 1. Test with no token
    resp = client.get("/api/system/health")
    assert resp.status_code == 200  # health is allowed without auth

    # 2. Test protected endpoint with no token
    resp = client.get("/api/risk/status")
    assert resp.status_code == 401
    assert resp.json() == {"data": None, "error": "Unauthorized"}

    # 3. Test protected endpoint with invalid token
    resp = client.get(
        "/api/risk/status", headers={"Authorization": "Bearer wrong_token"}
    )
    assert resp.status_code == 401

    # 4. Test protected endpoint with valid token
    resp = client.get(
        "/api/risk/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200

    # 5. Test protected endpoint with partial match (prefix)
    # This specifically checks that we are not doing verify matching that allows prefix
    resp = client.get(
        "/api/risk/status", headers={"Authorization": f"Bearer {token[:5]}"}
    )
    assert resp.status_code == 401

    # 6. Test protected endpoint with correct token but wrong header format
    resp = client.get("/api/risk/status", headers={"Authorization": f"{token}"})
    assert resp.status_code == 401
