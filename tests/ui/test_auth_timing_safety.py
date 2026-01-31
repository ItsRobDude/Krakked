import pytest
from unittest.mock import patch
from starlette.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_compare_digest():
    """Verify that AuthMiddleware uses secrets.compare_digest for constant-time comparison."""
    token = "test_secret_token"
    context = build_test_context(
        auth_enabled=True, auth_token=token, read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    # We patch the secrets module in the api module.
    # If the api module doesn't import secrets, this patch will fail,
    # indicating the security control is missing.
    try:
        with patch("kraken_bot.ui.api.secrets.compare_digest", return_value=True) as mock_compare:
            response = client.get(
                "/api/portfolio/summary",
                headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200
            # Verify the arguments passed to compare_digest
            # It should compare the full auth header with the expected bearer string
            mock_compare.assert_called_once()
            args, _ = mock_compare.call_args
            assert args[0] == f"Bearer {token}"  # user input
            assert args[1] == f"Bearer {token}"  # expected

    except (ImportError, AttributeError):
        pytest.fail("secrets.compare_digest is not being used in kraken_bot.ui.api")


def test_auth_middleware_rejects_invalid_token():
    token = "test_secret_token"
    context = build_test_context(
        auth_enabled=True, auth_token=token, read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    try:
        with patch("kraken_bot.ui.api.secrets.compare_digest", return_value=False) as mock_compare:
            response = client.get(
                "/api/portfolio/summary",
                headers={"Authorization": "Bearer wrong_token"}
            )
            assert response.status_code == 401
            mock_compare.assert_called_once()
    except (ImportError, AttributeError):
        pytest.fail("secrets.compare_digest is not being used in kraken_bot.ui.api")
