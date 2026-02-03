import pytest
from starlette.testclient import TestClient


@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["secret-token"], indirect=True)
def test_auth_success(client: TestClient):
    """Verify that correct token allows access to protected endpoint."""
    headers = {"Authorization": "Bearer secret-token"}
    # /api/config/runtime is a protected GET endpoint
    response = client.get("/api/config/runtime", headers=headers)
    assert response.status_code == 200


@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["secret-token"], indirect=True)
def test_auth_failure_wrong_token(client: TestClient):
    """Verify that incorrect token denies access to protected endpoint."""
    headers = {"Authorization": "Bearer wrong-token"}
    response = client.get("/api/config/runtime", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"


@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["secret-token"], indirect=True)
def test_auth_failure_missing_header(client: TestClient):
    """Verify that missing header denies access to protected endpoint."""
    response = client.get("/api/config/runtime")
    assert response.status_code == 401
    assert response.json()["error"] == "Unauthorized"


@pytest.mark.parametrize("ui_auth_enabled", [True], indirect=True)
@pytest.mark.parametrize("ui_auth_token", ["secret-token"], indirect=True)
def test_health_check_bypass(client: TestClient):
    """Verify that health check endpoint is accessible without auth."""
    # Health endpoints are explicitly whitelisted
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
