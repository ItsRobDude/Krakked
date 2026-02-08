from unittest.mock import patch

from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_constant_time_compare():
    """
    Verifies that the AuthMiddleware uses secrets.compare_digest for token validation
    to prevent timing attacks.
    """
    token = "secret_token"
    # Create context with auth enabled
    context = build_test_context(auth_enabled=True, auth_token=token, read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # We patch secrets.compare_digest to spy on it.
    # We set return_value=True so that if it IS called, it succeeds (we mimic correct token).
    # If the code uses '==' or '!=', this mock won't be called.
    with patch("secrets.compare_digest", return_value=True) as mock_compare:
        # Make a request to a protected endpoint
        # We pass the correct token so that even the non-secure comparison would pass (if it were used).
        # But we care about WHICH comparison function is used.
        response = client.get(
            "/api/risk/status", headers={"Authorization": f"Bearer {token}"}
        )

        # Verify the request succeeded (200 OK) - this ensures we hit the auth logic
        assert response.status_code == 200, "Request should succeed with valid token"

        # Verify that secrets.compare_digest was called
        mock_compare.assert_called()
