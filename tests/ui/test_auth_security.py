from unittest.mock import patch
import pytest
from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


@pytest.fixture
def auth_client():
    token = "secret-token"
    # Create context with auth enabled
    context = build_test_context(auth_enabled=True, auth_token=token, read_only=False)
    context.is_setup_mode = False  # Disable setup mode to avoid 503
    app = create_api(context)
    return TestClient(app), token


def test_auth_middleware_rejects_invalid_token(auth_client):
    client, token = auth_client
    # Use /api/config/runtime which is protected
    response = client.get("/api/config/runtime", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}


def test_auth_middleware_accepts_valid_token(auth_client):
    client, token = auth_client
    response = client.get("/api/config/runtime", headers={"Authorization": f"Bearer {token}"})
    # Should not be 401. Likely 200 or 500 depending on mock state, but definitely passed auth.
    assert response.status_code != 401


@patch("secrets.compare_digest")
def test_auth_middleware_uses_compare_digest(mock_compare, auth_client):
    """
    Verifies that the AuthMiddleware uses secrets.compare_digest for token comparison.
    """
    # Force comparison to return False to ensure we can verify it was called
    # even if the token is correct in the request.
    mock_compare.return_value = False

    client, token = auth_client

    # Make a request with the correct token
    response = client.get("/api/config/runtime", headers={"Authorization": f"Bearer {token}"})

    # Should be 401 because we mocked compare_digest to return False
    assert response.status_code == 401

    # Assert compare_digest was called
    assert mock_compare.called, "secrets.compare_digest was not called"

    # Verify arguments
    # We expect one arg to be the user provided header, the other the expected token
    call_args = mock_compare.call_args
    args = call_args[0]

    expected_header = f"Bearer {token}"
    assert expected_header in args
