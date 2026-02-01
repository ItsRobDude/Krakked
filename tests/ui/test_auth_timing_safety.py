import secrets
from unittest.mock import patch

from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_constant_time_compare():
    """
    Verifies that the AuthMiddleware uses secrets.compare_digest
    instead of standard string comparison to prevent timing attacks.
    """
    context = build_test_context(
        auth_enabled=True, auth_token="supersecret", read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    # We spy on secrets.compare_digest to ensure it is used.
    # We use side_effect=secrets.compare_digest so it actually runs the comparison logic if called.
    with patch(
        "secrets.compare_digest", side_effect=secrets.compare_digest
    ) as mock_compare:
        # Send an invalid token
        response = client.get(
            "/api/portfolio/summary", headers={"Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401

        # Verify compare_digest was called.
        # This assertion will fail if the code uses `!=` or `==`.
        mock_compare.assert_called()
