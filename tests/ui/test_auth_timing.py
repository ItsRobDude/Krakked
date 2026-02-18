from starlette.testclient import TestClient

from kraken_bot.ui.api import create_api
from tests.ui.conftest import build_test_context


def test_auth_middleware_rejects_timing_attack_simulation():
    """
    Verifies that AuthMiddleware correctly handles valid and invalid tokens.
    While we cannot easily test for timing differences in unit tests,
    this ensures functional correctness remains intact when we switch to
    constant-time comparison.
    """
    token = "secret_token_123"
    context = build_test_context(auth_enabled=True, auth_token=token, read_only=False)
    # Ensure setup mode is off so we don't get 503 from LifecycleMiddleware
    context.is_setup_mode = False

    app = create_api(context)
    client = TestClient(app)

    # Use a protected endpoint (portfolio/summary)
    endpoint = "/api/portfolio/summary"

    # 1. Valid token
    response = client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
    assert (
        response.status_code == 200
    ), f"Expected 200, got {response.status_code}: {response.json()}"
    assert response.json()["error"] is None

    # 2. Invalid token (different length)
    response = client.get(endpoint, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"

    # 3. Invalid token (same length, different content)
    response = client.get(endpoint, headers={"Authorization": f"Bearer {token[:-1]}X"})
    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"

    # 4. Missing header
    response = client.get(endpoint)
    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"

    # 5. Empty header
    response = client.get(endpoint, headers={"Authorization": ""})
    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"

    # 6. Malformed header (no Bearer)
    response = client.get(endpoint, headers={"Authorization": token})
    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"
