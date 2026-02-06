import secrets
from unittest.mock import patch

from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_basic_functionality():
    """Verify that auth middleware still blocks invalid tokens."""
    context = build_test_context(
        auth_enabled=True, auth_token="secret-token", read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    # invalid token
    response = client.get(
        "/api/portfolio/summary", headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}

    # valid token
    response = client.get(
        "/api/portfolio/summary", headers={"Authorization": "Bearer secret-token"}
    )
    assert response.status_code == 200


def test_auth_middleware_uses_compare_digest():
    """Verify that secrets.compare_digest is used for timing attack protection."""
    context = build_test_context(
        auth_enabled=True, auth_token="secret-token", read_only=False
    )

    # We need to spy on secrets.compare_digest inside kraken_bot.ui.api
    # Since it is imported as 'import secrets', we patch 'kraken_bot.ui.api.secrets.compare_digest'

    with patch(
        "kraken_bot.ui.api.secrets.compare_digest", side_effect=secrets.compare_digest
    ) as mock_compare:
        app = create_api(context)
        client = TestClient(app)

        # Make a request with invalid token
        client.get(
            "/api/portfolio/summary", headers={"Authorization": "Bearer wrong-token"}
        )

        assert mock_compare.called
        # Check arguments: first call, args
        args, _ = mock_compare.call_args
        # args[0] is header, args[1] is expected
        assert args[0] == "Bearer wrong-token"
        assert args[1] == "Bearer secret-token"
