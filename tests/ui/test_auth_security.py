from unittest.mock import patch
from fastapi.testclient import TestClient
from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context

def test_auth_middleware_uses_constant_time_comparison():
    """
    Verifies that AuthMiddleware uses secrets.compare_digest for token validation
    to prevent timing attacks.
    """
    context = build_test_context(auth_enabled=True, auth_token="secret_token", read_only=False)
    app = create_api(context)
    client = TestClient(app)

    # We patch secrets.compare_digest in kraken_bot.ui.api to verify it is called.
    # This ensures that the implementation uses the timing-safe function.
    with patch("kraken_bot.ui.api.secrets.compare_digest") as mock_digest:
        # Configure mock to return True even if tokens don't match.
        mock_digest.return_value = True

        # Send a request with a WRONG token.
        # We use /api/portfolio/summary because /api/system/health bypasses auth.
        response = client.get("/api/portfolio/summary", headers={"Authorization": "Bearer wrong_token"})

        # If the code uses compare_digest, it uses the mock result (True) -> 200 OK.
        # If the code uses '!=', it ignores the mock -> 401 Unauthorized.
        assert response.status_code == 200, "Expected success when compare_digest is mocked to True"

        mock_digest.assert_called_once()
