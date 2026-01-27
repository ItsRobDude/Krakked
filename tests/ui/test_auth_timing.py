import secrets
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_uses_compare_digest():
    """
    Verify that AuthMiddleware uses secrets.compare_digest for token validation
    to prevent timing attacks.
    """
    context = build_test_context(
        auth_enabled=True, auth_token="supersecret", read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    # We patch secrets.compare_digest to spy on it.
    # Since we import 'secrets' in api.py, we patch 'kraken_bot.ui.api.secrets.compare_digest'.

    real_compare = secrets.compare_digest
    mock_compare = MagicMock(side_effect=real_compare)

    with patch("kraken_bot.ui.api.secrets.compare_digest", mock_compare):
        # 1. Valid token
        resp = client.get(
            "/api/risk/status",
            headers={"Authorization": "Bearer supersecret"}
        )
        assert resp.status_code == 200
        assert mock_compare.called
        # Check args
        args, _ = mock_compare.call_args
        assert args[0] == "Bearer supersecret"
        assert args[1] == "Bearer supersecret"

        mock_compare.reset_mock()

        # 2. Invalid token
        resp = client.get(
            "/api/risk/status",
            headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status_code == 401
        assert mock_compare.called
        args, _ = mock_compare.call_args
        assert args[0] == "Bearer wrong"
        assert args[1] == "Bearer supersecret"
