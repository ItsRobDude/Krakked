from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def risk_context(client: TestClient):
    return client.context  # type: ignore[attr-defined]


def test_get_risk_status_enveloped(client, risk_context):
    risk_context.strategy_engine.get_risk_status.return_value = SimpleNamespace(
        kill_switch_active=False,
        daily_drawdown_pct=1.0,
        drift_flag=False,
        total_exposure_pct=2.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={"BTC": 1.0},
        per_strategy_exposure_pct={"alpha": 2.0},
    )

    response = client.get("/api/risk/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["per_strategy_exposure_pct"] == {"alpha": 2.0}


def test_get_risk_config_enveloped(client, risk_context):
    response = client.get("/api/risk/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["max_open_positions"] == risk_context.config.risk.max_open_positions


@pytest.mark.parametrize("ui_read_only", [False])
def test_update_risk_config_mutates_context(client, risk_context):
    body = {"max_open_positions": 42, "max_daily_drawdown_pct": 3.0}

    response = client.patch("/api/risk/config", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert risk_context.config.risk.max_open_positions == 42
    assert risk_context.strategy_engine.risk_engine.config.max_daily_drawdown_pct == 3.0
    assert payload["data"]["max_daily_drawdown_pct"] == 3.0


@pytest.mark.parametrize("ui_read_only", [True])
def test_update_risk_config_blocked_in_read_only(client, risk_context):
    original = risk_context.config.risk.max_open_positions

    response = client.patch("/api/risk/config", json={"max_open_positions": original + 1})

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    assert risk_context.config.risk.max_open_positions == original


@pytest.mark.parametrize("ui_read_only", [False])
def test_kill_switch_updates_mock(client, risk_context):
    risk_context.strategy_engine.get_risk_status.return_value = SimpleNamespace(
        kill_switch_active=True,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )

    response = client.post("/api/risk/kill_switch", json={"active": True})

    assert response.status_code == 200
    payload = response.json()
    risk_context.strategy_engine.set_manual_kill_switch.assert_called_once_with(True)
    assert payload["error"] is None
    assert payload["data"]["kill_switch_active"] is True


@pytest.mark.parametrize("ui_read_only", [True])
def test_kill_switch_blocked_when_read_only(client, risk_context):
    response = client.post("/api/risk/kill_switch", json={"active": True})

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    risk_context.strategy_engine.set_manual_kill_switch.assert_not_called()
