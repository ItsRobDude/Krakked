"""Tests for timing attack protection in UI auth."""

import secrets
from unittest.mock import patch

from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_constant_time_comparison():
    """Verify that AuthMiddleware uses secrets.compare_digest for token validation."""
    context = build_test_context(
        auth_enabled=True, auth_token="supersecret", read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    # We spy on secrets.compare_digest to ensure it is used.
    # If the implementation uses `!=` (variable time), this spy will not be called.
    with patch(
        "secrets.compare_digest", side_effect=secrets.compare_digest
    ) as mock_compare:
        # Send a request to a protected endpoint with an invalid token
        response = client.get(
            "/api/system/config", headers={"Authorization": "Bearer wrongtoken"}
        )

        assert response.status_code == 401

        # Verify compare_digest was called
        assert (
            mock_compare.called
        ), "AuthMiddleware must use secrets.compare_digest to prevent timing attacks"

        # Verify it was called with the correct arguments
        call_args = mock_compare.call_args
        assert call_args is not None
        args = call_args[0]

        # Verify one argument is the provided header and the other is the expected token
        assert "Bearer supersecret" in args
        assert "Bearer wrongtoken" in args
