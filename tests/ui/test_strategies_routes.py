from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from kraken_bot.config import StrategyConfig
from kraken_bot.strategy.models import StrategyState


@pytest.fixture
def strategy_context(client: TestClient):
    return client.context  # type: ignore[attr-defined]


def test_get_strategies_enveloped(client, strategy_context):
    now = datetime.now(UTC)
    strategy_context.strategy_engine.get_strategy_state.return_value = [
        StrategyState(
            strategy_id="alpha",
            enabled=True,
            last_intents_at=now,
            last_actions_at=None,
            current_positions=[],
            pnl_summary={"pnl": 1.0},
        )
    ]

    response = client.get("/api/strategies/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"][0]["strategy_id"] == "alpha"


@pytest.mark.parametrize("ui_read_only", [False])
def test_set_strategy_enabled_updates_state(client, strategy_context):
    strategy_context.strategy_engine.strategy_states["alpha"] = SimpleNamespace(
        enabled=False
    )
    strategy_context.config.strategies.enabled = []

    response = client.patch("/api/strategies/alpha/enabled", json={"enabled": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"] == {"strategy_id": "alpha", "enabled": True}
    assert strategy_context.strategy_engine.strategy_states["alpha"].enabled is True
    assert "alpha" in strategy_context.config.strategies.enabled


@pytest.mark.parametrize("ui_read_only", [True])
def test_set_strategy_enabled_blocked_read_only(client, strategy_context):
    strategy_context.strategy_engine.strategy_states["alpha"] = SimpleNamespace(
        enabled=False
    )

    response = client.patch("/api/strategies/alpha/enabled", json={"enabled": True})

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    assert strategy_context.strategy_engine.strategy_states["alpha"].enabled is False


@pytest.mark.parametrize("ui_read_only", [False])
def test_update_strategy_config_mutates_config(client, strategy_context):
    strategy_context.config.strategies.configs["alpha"] = StrategyConfig(
        name="Alpha", type="grid", enabled=True, params={"foo": "bar"}
    )

    response = client.patch(
        "/api/strategies/alpha/config",
        json={"enabled": False, "params": {"baz": 1}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["enabled"] is False
    assert payload["data"]["params"] == {"foo": "bar", "baz": 1}


@pytest.mark.parametrize("ui_read_only", [True])
def test_update_strategy_config_blocked(client, strategy_context):
    strategy_context.config.strategies.configs["alpha"] = StrategyConfig(
        name="Alpha", type="grid", enabled=True
    )

    response = client.patch("/api/strategies/alpha/config", json={"enabled": False})

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    assert strategy_context.config.strategies.configs["alpha"].enabled is True
