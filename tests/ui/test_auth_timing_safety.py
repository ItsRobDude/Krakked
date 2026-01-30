import secrets
from unittest.mock import patch
from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context

def test_auth_uses_constant_time_comparison():
    """Verify that authentication uses secrets.compare_digest to prevent timing attacks."""
    context = build_test_context(
        auth_enabled=True, auth_token="test-secret-token", read_only=False
    )

    app = create_api(context)
    client = TestClient(app)

    # We patch secrets.compare_digest to ensure it is used.
    with patch("secrets.compare_digest", side_effect=secrets.compare_digest) as mock_compare:
        # 1. Test Valid Auth on a protected endpoint
        # Use /api/config/runtime which exists and is protected
        headers = {"Authorization": "Bearer test-secret-token"}

        response = client.get("/api/config/runtime", headers=headers)
        # We expect 200 because auth passes.
        assert response.status_code == 200

        # Verify compare_digest was called
        assert mock_compare.called, "secrets.compare_digest was not called for valid auth"

        # Check arguments
        args, _ = mock_compare.call_args
        # One arg is from header, one is expected
        assert "Bearer test-secret-token" in args

        mock_compare.reset_mock()

        # 2. Test Invalid Auth
        headers = {"Authorization": "Bearer wrong-token"}
        response = client.get("/api/config/runtime", headers=headers)
        assert response.status_code == 401

        assert mock_compare.called, "secrets.compare_digest was not called for invalid auth"
