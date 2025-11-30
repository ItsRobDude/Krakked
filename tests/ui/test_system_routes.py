from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from kraken_bot.connection.exceptions import AuthError, KrakenAPIError, ServiceUnavailableError
from kraken_bot.connection import rest_client
from kraken_bot.market_data.api import MarketDataStatus
from kraken_bot.metrics import SystemMetrics


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


def test_system_health_reports_config_and_risk_flags(client, system_context):
    metrics = SystemMetrics()
    metrics.update_market_data_status(
        ok=False, stale=True, reason="stream delay", max_staleness=12.5
    )
    system_context.metrics = metrics

    system_context.config.execution.mode = "paper"
    system_context.config.ui.read_only = True

    system_context.market_data.get_data_status.return_value = SimpleNamespace(
        rest_api_reachable=False,
        websocket_connected=True,
        streaming_pairs=2,
        stale_pairs=1,
        subscription_errors=0,
    )
    system_context.market_data.get_health_status.return_value = MarketDataStatus(
        health="stale", max_staleness=12.5, reason="stream delay"
    )
    system_context.strategy_engine.get_risk_status.return_value = SimpleNamespace(
        kill_switch_active=True
    )

    response = client.get("/api/system/health")

    assert response.status_code == 200
    payload = response.json()["data"]

    assert payload["app_version"]
    assert payload["execution_mode"] == "paper"
    assert payload["current_mode"] == "paper"
    assert payload["ui_read_only"] is True
    assert payload["market_data_status"] == "stale"
    assert payload["market_data_reason"] == "stream delay"
    assert payload["market_data_ok"] is False
    assert payload["market_data_stale"] is True
    assert payload["kill_switch_active"] is True

    assert isinstance(payload["rest_api_reachable"], bool)
    assert isinstance(payload["websocket_connected"], bool)
    assert isinstance(payload["streaming_pairs"], int)
    assert isinstance(payload["stale_pairs"], int)
    assert isinstance(payload["subscription_errors"], int)
    assert isinstance(payload["drift_detected"], bool)


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


def test_system_metrics_reports_snapshot_payload(client, system_context):
    metrics = SystemMetrics()
    metrics.record_plan(blocked_actions=3)
    metrics.record_plan_execution(["exec failed"])
    metrics.record_market_data_error("md error")
    metrics.update_portfolio_state(
        equity_usd=1234.5,
        realized_pnl_usd=10.0,
        unrealized_pnl_usd=-2.5,
        open_orders_count=7,
        open_positions_count=1,
    )
    metrics.record_drift(True, "drift detected")
    metrics.update_market_data_status(
        ok=True, stale=False, reason=None, max_staleness=1.25
    )

    system_context.metrics = metrics

    response = client.get("/api/system/metrics")

    assert response.status_code == 200
    payload = response.json()["data"]

    expected_keys = {
        "plans_generated",
        "plans_executed",
        "blocked_actions",
        "execution_errors",
        "market_data_errors",
        "recent_errors",
        "last_equity_usd",
        "last_realized_pnl_usd",
        "last_unrealized_pnl_usd",
        "open_orders_count",
        "open_positions_count",
        "drift_detected",
        "drift_reason",
        "market_data_ok",
        "market_data_stale",
        "market_data_reason",
        "market_data_max_staleness",
    }
    assert expected_keys.issubset(payload.keys())

    assert payload["plans_generated"] == 1
    assert payload["plans_executed"] == 1
    assert payload["blocked_actions"] == 3
    assert payload["execution_errors"] == 1
    assert payload["market_data_errors"] == 1
    assert len(payload["recent_errors"]) == 3
    assert payload["last_equity_usd"] == 1234.5
    assert payload["last_realized_pnl_usd"] == 10.0
    assert payload["last_unrealized_pnl_usd"] == -2.5
    assert payload["open_orders_count"] == 7
    assert payload["open_positions_count"] == 1
    assert payload["drift_detected"] is True
    assert payload["drift_reason"] == "drift detected"
    assert payload["market_data_ok"] is True
    assert payload["market_data_stale"] is False
    assert payload["market_data_reason"] is None
    assert payload["market_data_max_staleness"] == 1.25


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
