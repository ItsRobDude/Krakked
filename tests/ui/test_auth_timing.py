import pytest
from starlette.testclient import TestClient

@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["s3cr3t"], indirect=True)
def test_auth_enforcement(client: TestClient):
    """
    Verify authentication logic for the UI API.
    Although this test does not measure timing, it ensures that
    the switch to constant-time comparison preserves correctness.
    """

    # 1. No Authorization header -> 401
    resp = client.get("/api/config/runtime")
    assert resp.status_code == 401, "Should reject missing auth header"

    # 2. Malformed Authorization header -> 401
    resp = client.get("/api/config/runtime", headers={"Authorization": "s3cr3t"})
    assert resp.status_code == 401, "Should reject malformed auth header"

    # 3. Wrong token -> 401
    resp = client.get("/api/config/runtime", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401, "Should reject incorrect token"

    # 4. Partial token (prefix) -> 401
    resp = client.get("/api/config/runtime", headers={"Authorization": "Bearer s3cr3"})
    assert resp.status_code == 401, "Should reject partial token"

    # 5. Correct token -> 200
    resp = client.get("/api/config/runtime", headers={"Authorization": "Bearer s3cr3t"})
    assert resp.status_code == 200, "Should accept correct token"
