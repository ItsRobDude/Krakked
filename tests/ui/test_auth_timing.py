import secrets
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_constant_time_compare():
    """
    Verifies that AuthMiddleware uses secrets.compare_digest for token validation
    to prevent timing attacks.
    """
    token = "secret_token_123"
    context = build_test_context(auth_enabled=True, auth_token=token, read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # Use a protected endpoint
    endpoint = "/api/portfolio/summary"

    with patch("secrets.compare_digest", wraps=secrets.compare_digest) as mock_compare:
        # 1. Valid token
        response = client.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

        # Verify that secrets.compare_digest was used
        assert mock_compare.called, "secrets.compare_digest should be called to prevent timing attacks"

def test_auth_middleware_rejects_invalid_token():
    """Ensure that even with constant time comparison, invalid tokens are rejected."""
    token = "secret_token_123"
    context = build_test_context(auth_enabled=True, auth_token=token, read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # Use a protected endpoint
    endpoint = "/api/portfolio/summary"

    response = client.get(
        endpoint,
        headers={"Authorization": "Bearer wrong_token"}
    )
    assert response.status_code == 401
