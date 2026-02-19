from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from kraken_bot.ui.api import AuthMiddleware


def test_auth_middleware_uses_constant_time_compare():
    """
    Verifies that AuthMiddleware uses secrets.compare_digest for token validation
    to prevent timing attacks.
    """
    app = FastAPI()

    @app.get("/api/protected")
    def protected_endpoint():
        return {"data": "secret"}

    # We need to use the middleware as the app for the client
    # But BaseHTTPMiddleware is an ASGI app itself.
    # However, BaseHTTPMiddleware takes 'app' in init.
    # To test it properly with TestClient, we usually add it to the app.

    app.add_middleware(AuthMiddleware, token="secret-token")

    client = TestClient(app)

    # We want to spy on secrets.compare_digest.
    # Since secrets is a built-in module, we can patch it where it is used.
    # However, if it's imported as `import secrets`, we patch `kraken_bot.ui.api.secrets`.

    with patch("kraken_bot.ui.api.secrets") as mock_secrets:
        # Configure the mock to behave like the real function for the return value
        # but we mainly want to assert it was called.
        mock_secrets.compare_digest.return_value = False

        # Make a request with wrong token
        response = client.get(
            "/api/protected", headers={"Authorization": "Bearer wrong-token"}
        )

        assert response.status_code == 401

        # Assert compare_digest was called
        mock_secrets.compare_digest.assert_called_once()

        # Check arguments: (given, expected) or (expected, given)
        args, _ = mock_secrets.compare_digest.call_args
        # One of them should be the expected bearer token
        assert "Bearer secret-token" in args
        # The other should be the provided one
        assert "Bearer wrong-token" in args


def test_auth_middleware_handles_none_token_safely():
    """
    Verifies that AuthMiddleware handles cases where token might be None
    (though the type hint says str, runtime might vary) or headers are missing.
    """
    app = FastAPI()

    @app.get("/api/protected")
    def protected_endpoint():
        return {"data": "secret"}

    app.add_middleware(AuthMiddleware, token="secret")
    client = TestClient(app)

    with patch("kraken_bot.ui.api.secrets") as mock_secrets:
        mock_secrets.compare_digest.return_value = False

        # Request without auth header
        response = client.get("/api/protected")
        assert response.status_code == 401

        mock_secrets.compare_digest.assert_called_once()
        args, _ = mock_secrets.compare_digest.call_args
        # Should compare empty string with expected bearer
        assert "Bearer secret" in args
        assert "" in args
