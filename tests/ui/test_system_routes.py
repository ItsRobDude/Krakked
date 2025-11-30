from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from kraken_bot.connection.exceptions import AuthError, KrakenAPIError, ServiceUnavailableError
from kraken_bot.connection import rest_client


@pytest.fixture
def system_context(client: TestClient):
    return client.context  # type: ignore[attr-defined]


def test_system_health_enveloped(client, system_context):
    system_context.market_data.get_data_status.return_value = SimpleNamespace(
        rest_api_reachable=True,
        websocket_connected=True,
        streaming_pairs=5,
        stale_pairs=0,
        subscription_errors=0,
    )
    system_context.execution_service.adapter.config.mode = "paper"

    response = client.get("/api/system/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["market_data_ok"] is True
    assert payload["data"]["current_mode"] == "paper"


def test_system_metrics_endpoint(client, system_context):
    metrics = system_context.metrics
    metrics.record_plan(blocked_actions=2)
    metrics.record_plan_execution(["first error"])
    metrics.record_error("background failure")
    metrics.update_portfolio_state(
        equity_usd=1000.0,
        realized_pnl_usd=25.5,
        unrealized_pnl_usd=-5.5,
        open_orders_count=3,
        open_positions_count=2,
    )

    response = client.get("/api/system/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["plans_generated"] == 1
    assert payload["data"]["plans_executed"] == 1
    assert payload["data"]["blocked_actions"] == 2
    assert payload["data"]["execution_errors"] == 2
    assert payload["data"]["market_data_errors"] == 0
    assert len(payload["data"]["recent_errors"]) == 2
    assert payload["data"]["last_equity_usd"] == 1000.0
    assert payload["data"]["last_realized_pnl_usd"] == 25.5
    assert payload["data"]["last_unrealized_pnl_usd"] == -5.5
    assert payload["data"]["open_orders_count"] == 3
    assert payload["data"]["open_positions_count"] == 2

    metrics.update_portfolio_state(
        equity_usd=2000.0,
        realized_pnl_usd=30.0,
        unrealized_pnl_usd=10.0,
        open_orders_count=1,
        open_positions_count=4,
    )

    refreshed = client.get("/api/system/metrics")
    refreshed_payload = refreshed.json()["data"]
    assert refreshed_payload["last_equity_usd"] == 2000.0
    assert refreshed_payload["last_realized_pnl_usd"] == 30.0
    assert refreshed_payload["last_unrealized_pnl_usd"] == 10.0
    assert refreshed_payload["open_orders_count"] == 1
    assert refreshed_payload["open_positions_count"] == 4


def test_config_redacts_auth_token(client, system_context):
    system_context.config.ui.auth.token = "secret"

    response = client.get("/api/system/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["ui"]["auth"]["token"] == "***"


@pytest.mark.parametrize("ui_read_only", [False])
def test_mode_change_updates_configs(client, system_context):
    system_context.config.execution.allow_live_trading = True

    response = client.post("/api/system/mode", json={"mode": "live"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"] == {"mode": "live", "validate_only": False}
    assert system_context.execution_service.adapter.config.mode == "live"


@pytest.mark.parametrize("ui_read_only", [True])
def test_mode_change_blocked_read_only(client, system_context):
    response = client.post("/api/system/mode", json={"mode": "paper"})

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}


@pytest.mark.parametrize("ui_auth_enabled", [True])
def test_auth_middleware_blocks_missing_token(client):
    response = client.get("/api/risk/status")

    assert response.status_code == 401
    assert response.json() == {"data": None, "error": "Unauthorized"}


@pytest.mark.parametrize("ui_auth_enabled", [True])
def test_auth_middleware_allows_valid_token(client, ui_auth_token):
    headers = {"Authorization": f"Bearer {ui_auth_token}"}

    response = client.get("/api/risk/status", headers=headers)

    assert response.status_code == 200
    assert "error" in response.json()


@pytest.mark.parametrize("ui_auth_enabled", [True])
def test_credential_validation_auth_and_missing_fields(monkeypatch, client, ui_auth_token):
    headers = {"Authorization": f"Bearer wrong"}

    unauthorized = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "", "apiSecret": "", "region": ""},
        headers=headers,
    )
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"data": None, "error": "Unauthorized"}

    headers = {"Authorization": f"Bearer {ui_auth_token}"}
    missing_fields = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "", "apiSecret": "", "region": ""},
        headers=headers,
    )
    assert missing_fields.status_code == 200
    assert missing_fields.json() == {
        "data": {"valid": False},
        "error": "apiKey, apiSecret, and region are required.",
    }

    class FakeClient:
        def __init__(self, exc):
            self.exc = exc

        def get_private(self, *_args, **_kwargs):
            if self.exc:
                raise self.exc
            return {}

    monkeypatch.setattr(rest_client, "KrakenRESTClient", lambda *_, **__: FakeClient(None))
    success = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "k", "apiSecret": "s", "region": "r"},
        headers=headers,
    )
    assert success.json() == {"data": {"valid": True}, "error": None}

    monkeypatch.setattr(rest_client, "KrakenRESTClient", lambda *_, **__: FakeClient(AuthError("bad")))
    auth_failure = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "k", "apiSecret": "s", "region": "r"},
        headers=headers,
    )
    assert auth_failure.json() == {
        "data": {"valid": False},
        "error": "Authentication failed. Please verify your API key/secret.",
    }

    monkeypatch.setattr(
        rest_client, "KrakenRESTClient", lambda *_, **__: FakeClient(ServiceUnavailableError("down"))
    )
    unavailable = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "k", "apiSecret": "s", "region": "r"},
        headers=headers,
    )
    assert unavailable.json() == {
        "data": {"valid": False},
        "error": "Kraken is unavailable or could not be reached. Please retry.",
    }

    monkeypatch.setattr(rest_client, "KrakenRESTClient", lambda *_, **__: FakeClient(KrakenAPIError("err")))
    api_error = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "k", "apiSecret": "s", "region": "r"},
        headers=headers,
    )
    assert api_error.json() == {
        "data": {"valid": False},
        "error": "Authentication failed. Please verify your API key/secret.",
    }
