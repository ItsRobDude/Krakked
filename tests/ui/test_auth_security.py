from starlette.testclient import TestClient
from kraken_bot.ui.api import AuthMiddleware
from fastapi import FastAPI
import secrets
from unittest.mock import patch


def test_auth_middleware_uses_compare_digest_with_client():
    app = FastAPI()
    app.add_middleware(AuthMiddleware, token="secret-token")

    @app.get("/api/protected")
    def protected():
        return {"status": "ok"}

    client = TestClient(app)

    # We patch the secrets module where it is imported.
    # Since it's not imported yet in api.py, we can just patch 'secrets.compare_digest' globally
    # as long as the implementation uses `secrets.compare_digest`.
    # Let's assume `import secrets` usage.

    with patch("secrets.compare_digest", side_effect=secrets.compare_digest) as mock_compare:
        response = client.get("/api/protected", headers={"Authorization": "Bearer secret-token"})
        assert response.status_code == 200
        # This assertion should fail before the fix
        mock_compare.assert_called()
