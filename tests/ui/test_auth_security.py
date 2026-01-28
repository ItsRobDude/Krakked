import pytest
from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context

def test_auth_middleware_rejects_invalid_token_timing_safe():
    """
    Verify that AuthMiddleware enforces token validation.
    This test ensures that the switch to secrets.compare_digest maintains behavior.
    """
    context = build_test_context(
        auth_enabled=True, auth_token="supersecrettoken", read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    # Note: /api/system/health is whitelisted in AuthMiddleware, so we use
    # a protected endpoint like /api/portfolio/summary to test auth enforcement.
    endpoint = "/api/portfolio/summary"

    # Valid token
    response = client.get(
        endpoint,
        headers={"Authorization": "Bearer supersecrettoken"}
    )
    assert response.status_code == 200

    # Invalid token
    response = client.get(
        endpoint,
        headers={"Authorization": "Bearer wrongtoken"}
    )
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}

    # Empty token
    response = client.get(
        endpoint,
        headers={"Authorization": "Bearer "}
    )
    assert response.status_code == 401

    # Missing header
    response = client.get(endpoint)
    assert response.status_code == 401
