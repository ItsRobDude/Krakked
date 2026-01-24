import logging
import secrets
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context

logger = logging.getLogger(__name__)

def test_auth_middleware_uses_compare_digest():
    """
    Verify that AuthMiddleware uses secrets.compare_digest for timing-safe comparison.
    This test is expected to fail if the implementation uses insecure string comparison.
    """
    context = build_test_context(auth_enabled=True, auth_token="secret_token", read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # We assume the implementation will import secrets.
    # If it doesn't, this patch might target the system secrets module,
    # but the code won't call it, so mock_compare.called will be False.

    with patch("secrets.compare_digest", side_effect=secrets.compare_digest) as mock_compare:
        # Request a protected endpoint with an invalid token
        response = client.get("/api/portfolio/summary", headers={"Authorization": "Bearer invalid_token"})

        # Should be unauthorized
        assert response.status_code == 401

        # Verify compare_digest was called
        # This is the key security assertion
        if not mock_compare.called:
            pytest.fail("secrets.compare_digest was NOT called during token validation. Vulnerable to timing attack.")
