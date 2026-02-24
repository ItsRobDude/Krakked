import secrets
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_compare_digest():
    """
    Verifies that the AuthMiddleware uses secrets.compare_digest for constant-time comparison.
    """
    # Setup context with auth enabled
    context = build_test_context(auth_enabled=True, auth_token="supersecret", read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # Patch secrets.compare_digest to spy on it
    with patch("secrets.compare_digest", side_effect=secrets.compare_digest) as mock_compare:
        # Request with correct token to trigger the check
        response = client.get("/api/portfolio/summary", headers={"Authorization": "Bearer supersecret"})
        assert response.status_code == 200

        # If the code uses ==, this mock won't be called.
        if not mock_compare.called:
            pytest.fail("secrets.compare_digest was not called during authentication verification!")


def test_auth_middleware_functionality():
    """
    Verifies that the AuthMiddleware correctly allows/blocks requests.
    """
    context = build_test_context(auth_enabled=True, auth_token="supersecret", read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # Valid token
    response = client.get("/api/portfolio/summary", headers={"Authorization": "Bearer supersecret"})
    assert response.status_code == 200

    # Invalid token
    response = client.get("/api/portfolio/summary", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401

    # Missing token
    response = client.get("/api/portfolio/summary")
    assert response.status_code == 401
