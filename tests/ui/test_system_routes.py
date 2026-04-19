import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml
from starlette.testclient import TestClient

import krakked.connection.validation as validation_mod
from krakked.config import ProfileConfig, StrategyConfig
from krakked.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    ServiceUnavailableError,
)
from krakked.credentials import CredentialResult, CredentialStatus
from krakked.market_data.api import MarketDataStatus
from krakked.metrics import SystemMetrics
from krakked.ui.api import create_api
from tests.ui.conftest import build_test_context

logger = logging.getLogger(__name__)


@pytest.fixture
def system_context(client: TestClient):
    return client.context  # type: ignore[attr-defined]


@pytest.fixture
def temp_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # Create main config
    (config_dir / "config.yaml").write_text("execution:\n  mode: paper\n")
    return config_dir


def test_auth_middleware_respects_base_path():
    context = build_test_context(
        auth_enabled=True, auth_token="secret", read_only=False
    )
    context.config.ui.base_path = "/krakked"

    app = create_api(context)
    client = TestClient(app)

    health = client.get("/krakked/api/system/health")
    assert health.status_code == 200

    unauthorized = client.get("/krakked/api/portfolio/summary")
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"data": None, "error": "Unauthorized"}

    authorized = client.get(
        "/krakked/api/portfolio/summary",
        headers={"Authorization": "Bearer secret"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["error"] is None


def test_health_endpoints_are_public_when_auth_enabled():
    context = build_test_context(
        auth_enabled=True, auth_token="secret", read_only=False
    )
    app = create_api(context)
    client = TestClient(app)

    simple_health = client.get("/api/health")
    assert simple_health.status_code == 200
    assert simple_health.json()["data"]["status"] == "ok"

    system_health = client.get("/api/system/health")
    assert system_health.status_code == 200
    assert system_health.json()["error"] is None


def test_root_health_alias_available_when_base_path_is_set():
    context = build_test_context(
        auth_enabled=True, auth_token="secret", read_only=False
    )
    context.config.ui.base_path = "/krakked"

    app = create_api(context)
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


def test_root_api_alias_respects_auth_when_base_path_is_set():
    context = build_test_context(
        auth_enabled=True, auth_token="secret", read_only=False
    )
    context.config.ui.base_path = "/krakked"

    app = create_api(context)
    client = TestClient(app)

    unauthorized = client.get("/api/portfolio/summary")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/api/portfolio/summary",
        headers={"Authorization": "Bearer secret"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["error"] is None


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
    assert payload["data"]["portfolio_sync_ok"] is True
    assert payload["data"]["portfolio_sync_reason"] is None
    assert payload["data"]["portfolio_last_sync_at"] is None
    assert payload["data"]["portfolio_baseline"] == "ledger_history"


def test_system_health_reports_config_and_risk_flags(client, system_context):
    metrics = SystemMetrics()
    metrics.update_market_data_status(
        ok=False, stale=True, status="degraded", reason="stream delay", max_staleness=12.5
    )
    system_context.metrics = metrics

    system_context.config.execution.mode = "paper"
    system_context.config.ui.read_only = True

    system_context.market_data.get_cached_data_status.return_value = SimpleNamespace(
        rest_api_reachable=False,
        websocket_connected=True,
        streaming_pairs=2,
        stale_pairs=1,
        subscription_errors=0,
    )
    system_context.market_data.get_cached_health_status.return_value = MarketDataStatus(
        health="degraded", max_staleness=12.5, reason="stream delay"
    )
    system_context.strategy_engine.get_risk_status.return_value = SimpleNamespace(
        kill_switch_active=True
    )
    system_context.portfolio.last_sync_ok = False
    system_context.portfolio.last_sync_reason = "Trade ingestion failed during portfolio sync."
    system_context.portfolio.last_sync_at = datetime(2026, 4, 19, 19, 30, tzinfo=timezone.utc)
    system_context.portfolio.baseline_source = "exchange_balances"

    response = client.get("/api/system/health")

    assert response.status_code == 200
    payload = response.json()["data"]

    assert payload["app_version"]
    assert payload["execution_mode"] == "paper"
    assert payload["current_mode"] == "paper"
    assert payload["ui_read_only"] is True
    assert payload["market_data_status"] == "degraded"
    assert payload["market_data_reason"] == "stream delay"
    assert payload["market_data_ok"] is False
    assert payload["market_data_stale"] is True
    assert payload["kill_switch_active"] is True
    assert payload["portfolio_sync_ok"] is False
    assert payload["portfolio_sync_reason"] == "Trade ingestion failed during portfolio sync."
    assert payload["portfolio_last_sync_at"] == "2026-04-19T19:30:00Z"
    assert payload["portfolio_baseline"] == "exchange_balances"

    assert isinstance(payload["rest_api_reachable"], bool)
    assert isinstance(payload["websocket_connected"], bool)
    assert isinstance(payload["streaming_pairs"], int)
    assert isinstance(payload["stale_pairs"], int)
    assert isinstance(payload["subscription_errors"], int)
    assert isinstance(payload["drift_detected"], bool)


def test_system_health_reports_execution_not_ok_when_live_is_blocked(
    client, system_context
):
    system_context.config.execution.mode = "live"
    system_context.config.execution.validate_only = False
    system_context.config.execution.allow_live_trading = True
    system_context.config.execution.paper_tests_completed = False
    system_context.market_data.get_cached_data_status.return_value = SimpleNamespace(
        rest_api_reachable=True,
        websocket_connected=True,
        streaming_pairs=5,
        stale_pairs=0,
        subscription_errors=0,
    )

    response = client.get("/api/system/health")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["current_mode"] == "live"
    assert payload["execution_ok"] is False


def test_system_health_prefers_metrics_snapshot_even_when_false(client, system_context):
    system_context.market_data.get_cached_data_status.return_value = SimpleNamespace(
        rest_api_reachable=True,
        websocket_connected=True,
        streaming_pairs=5,
        stale_pairs=0,
        subscription_errors=0,
    )
    system_context.market_data.get_cached_health_status.return_value = None

    metrics = SystemMetrics()
    metrics.update_market_data_status(
        ok=False, stale=False, status="unavailable", reason=None, max_staleness=None
    )
    system_context.metrics = metrics

    response = client.get("/api/system/health")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["market_data_ok"] is False
    assert payload["market_data_stale"] is False
    assert payload["market_data_status"] == "unavailable"


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
        ok=True, stale=False, status="streaming", reason=None, max_staleness=1.25
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


def test_start_session_reads_ml_from_config(client, system_context):
    """Ensures starting session aligns runtime state with config source of truth."""
    # Setup state: config says disabled, session memory is undefined or default
    system_context.config.ml.enabled = False

    # Pre-configure some strategies
    system_context.config.strategies.configs = {
        "ai_predictor": StrategyConfig(
            name="AI Predictor", type="machine_learning", enabled=True
        ),
    }
    # Manually enable in engine to simulate drift/default
    system_context.strategy_engine.strategy_states = {
        "ai_predictor": SimpleNamespace(enabled=True),
    }

    # Start session
    response = client.post("/api/system/session/start")

    assert response.status_code == 200
    payload = response.json()["data"]

    # Assert session payload reflects config state
    assert payload["ml_enabled"] is False

    # Assert runtime sync happened
    assert system_context.session.ml_enabled is False


def test_session_payload_reports_reloading_flag(client, system_context):
    system_context.reinitialize_event.set()

    response = client.get("/api/system/session")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["reloading"] is True
    assert payload["active"] is False


def test_config_loader_gating_prevents_risk_validation_failure(monkeypatch):
    """
    Ensures that when ml.enabled=False, ML strategies are stripped from enabled list
    so that missing risk limits don't trigger validation errors in live mode.
    """
    from krakked.config_loader import parse_app_config

    # Setup raw config
    raw_config = {
        "execution": {"mode": "live", "allow_live_trading": True},
        "ml": {"enabled": False},
        "strategies": {
            "enabled": ["ai_predictor", "regular_strat"],
            "configs": {
                "ai_predictor": {"type": "machine_learning"},
                "regular_strat": {"type": "regular"},
            },
        },
        "risk": {
            # Only provide limit for regular strat, missing AI one
            "max_per_strategy_pct": {"regular_strat": 5.0}
        },
    }

    # Should NOT raise ValueError because ai_predictor is filtered out
    config = parse_app_config(raw_config, config_path=MagicMock(), effective_env="live")

    assert "ai_predictor" not in config.strategies.enabled
    assert "regular_strat" in config.strategies.enabled
    assert config.ml.enabled is False


def test_config_redacts_auth_token(client, system_context):
    system_context.config.ui.auth.token = "secret"

    response = client.get("/api/system/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["ui"]["auth"]["token"] == "***"


def test_setup_config_updates_existing_bootstrap_config(
    monkeypatch, client, temp_config_dir
):
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )

    response = client.post(
        "/api/system/setup/config",
        json={"region_code": "EU", "universe_pairs": ["BTC/USD"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["success"] is True

    config_data = yaml.safe_load((temp_config_dir / "config.yaml").read_text())
    assert config_data["region"]["code"] == "EU"
    assert config_data["region"]["default_quote"] == "USD"
    assert config_data["universe"]["include_pairs"] == ["BTC/USD"]
    assert config_data["execution"]["mode"] == "paper"
    assert config_data["session"]["account_id"] == "default"
    assert config_data["ml"]["enabled"] is True
    assert config_data["market_data"]["backfill_timeframes"] == ["1h", "4h"]
    assert config_data["market_data"]["ws_timeframes"] == ["1m", "5m"]


def test_setup_config_uses_starter_universe_when_none_provided(
    monkeypatch, client, temp_config_dir
):
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )

    response = client.post(
        "/api/system/setup/config",
        json={"region_code": "US", "universe_pairs": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None

    config_data = yaml.safe_load((temp_config_dir / "config.yaml").read_text())
    assert config_data["universe"]["include_pairs"] == [
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
        "ADA/USD",
    ]


def test_setup_config_preserves_ws_style_pairs(
    monkeypatch, client, temp_config_dir
):
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )

    response = client.post(
        "/api/system/setup/config",
        json={"region_code": "US", "universe_pairs": ["BTC/USD", "ETH/USD"]},
    )

    assert response.status_code == 200
    assert response.json()["error"] is None

    config_data = yaml.safe_load((temp_config_dir / "config.yaml").read_text())
    assert config_data["universe"]["include_pairs"] == ["BTC/USD", "ETH/USD"]
    assert config_data["universe"]["min_24h_volume_usd"] == 100000.0
    assert config_data["market_data"]["backfill_timeframes"] == ["1h", "4h"]
    assert config_data["market_data"]["ws_timeframes"] == ["1m", "5m"]


def test_setup_config_preserves_existing_custom_sections(
    monkeypatch, client, temp_config_dir
):
    (temp_config_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "region": {"code": "US_CA", "default_quote": "USD"},
                "universe": {"include_pairs": [], "exclude_pairs": []},
                "execution": {"mode": "paper", "validate_only": False},
                "session": {"account_id": "custom", "mode": "paper"},
                "ml": {"enabled": False},
                "risk": {"max_open_positions": 7},
            }
        )
    )
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )

    response = client.post(
        "/api/system/setup/config",
        json={"region_code": "GB", "universe_pairs": ["ETH/USD"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None

    config_data = yaml.safe_load((temp_config_dir / "config.yaml").read_text())
    assert config_data["region"]["code"] == "GB"
    assert config_data["universe"]["include_pairs"] == ["ETH/USD"]
    assert config_data["execution"]["validate_only"] is False
    assert config_data["session"]["account_id"] == "custom"
    assert config_data["ml"]["enabled"] is False
    assert config_data["risk"]["max_open_positions"] == 7


def test_setup_config_upgrades_legacy_bootstrap_defaults(
    monkeypatch, client, temp_config_dir
):
    (temp_config_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "region": {"code": "US_CA", "default_quote": "USD"},
                "universe": {
                    "include_pairs": [],
                    "exclude_pairs": [],
                    "min_24h_volume_usd": 0.0,
                },
                "execution": {"mode": "paper"},
                "session": {"account_id": "default"},
                "ml": {"enabled": True},
            }
        )
    )
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )

    response = client.post(
        "/api/system/setup/config",
        json={"region_code": "US", "universe_pairs": []},
    )

    assert response.status_code == 200
    config_data = yaml.safe_load((temp_config_dir / "config.yaml").read_text())
    assert config_data["universe"]["include_pairs"] == [
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
        "ADA/USD",
    ]
    assert config_data["universe"]["min_24h_volume_usd"] == 100000.0
    assert config_data["market_data"]["backfill_timeframes"] == ["1h", "4h"]


@pytest.mark.parametrize("ui_read_only", [False])
def test_mode_change_updates_configs(
    monkeypatch, client, system_context, temp_config_dir
):
    system_context.config.execution.allow_live_trading = True
    system_context.config.execution.paper_tests_completed = True

    # Mock account functions to avoid file IO and keyring access
    monkeypatch.setattr("krakked.ui.routes.system.resolve_secrets_path", MagicMock())
    monkeypatch.setattr("krakked.ui.routes.system.unlock_secrets", MagicMock())
    monkeypatch.setattr(
        "krakked.ui.routes.system.set_session_master_password", MagicMock()
    )

    # Patch get_config_dir to use temp dir
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )

    response = client.post("/api/system/mode", json={"mode": "live"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["mode"] == "live"
    assert payload["data"]["validate_only"] is False
    assert payload["data"]["reloading"] is True

    assert system_context.execution_service.adapter.config.mode == "live"
    assert system_context.session.mode == "live"
    assert system_context.config.session.mode == "live"


def test_mode_change_to_live_requires_paper_tests_completed(client, system_context):
    system_context.config.execution.allow_live_trading = True
    system_context.config.execution.paper_tests_completed = False

    response = client.post("/api/system/mode", json={"mode": "live"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert "paper_tests_completed" in payload["error"]


def test_mode_change_to_live_can_certify_paper_tests_completed(
    monkeypatch, client, system_context, temp_config_dir
):
    system_context.config.execution.allow_live_trading = False
    system_context.config.execution.paper_tests_completed = False

    monkeypatch.setattr("krakked.ui.routes.system.resolve_secrets_path", MagicMock())
    monkeypatch.setattr("krakked.ui.routes.system.unlock_secrets", MagicMock())
    remember_password = MagicMock()
    monkeypatch.setattr(
        "krakked.ui.routes.system.set_session_master_password",
        remember_password,
    )
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )

    response = client.post(
        "/api/system/mode",
        json={
            "mode": "live",
            "password": "secret",
            "confirmation": "ENABLE LIVE TRADING",
            "certify_paper_tests_completed": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["mode"] == "live"
    assert payload["data"]["paper_tests_completed"] is True
    assert system_context.config.execution.paper_tests_completed is True
    assert system_context.execution_service.adapter.config.paper_tests_completed is True
    remember_password.assert_called_once_with("default", "secret")


def test_mode_change_does_not_cache_password_if_persist_fails(
    monkeypatch, client, system_context, temp_config_dir
):
    system_context.config.execution.allow_live_trading = False
    system_context.config.execution.paper_tests_completed = False

    monkeypatch.setattr("krakked.ui.routes.system.resolve_secrets_path", MagicMock())
    monkeypatch.setattr("krakked.ui.routes.system.unlock_secrets", MagicMock())
    remember_password = MagicMock()
    monkeypatch.setattr(
        "krakked.ui.routes.system.set_session_master_password",
        remember_password,
    )
    monkeypatch.setattr(
        "krakked.ui.routes.system.get_config_dir", lambda: temp_config_dir
    )
    monkeypatch.setattr(
        "krakked.ui.routes.system.atomic_write",
        MagicMock(side_effect=OSError("disk full")),
    )

    response = client.post(
        "/api/system/mode",
        json={
            "mode": "live",
            "password": "secret",
            "confirmation": "ENABLE LIVE TRADING",
            "certify_paper_tests_completed": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert "Failed to persist mode" in payload["error"]
    remember_password.assert_not_called()
    assert system_context.config.execution.mode == "paper"
    assert system_context.config.execution.paper_tests_completed is False


def test_start_session_live_mode_requires_paper_tests_completed(client, system_context):
    system_context.config.execution.mode = "live"
    system_context.config.execution.validate_only = False
    system_context.config.execution.allow_live_trading = True
    system_context.config.execution.paper_tests_completed = False
    system_context.session.mode = "live"

    response = client.post("/api/system/session/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert "paper_tests_completed" in payload["error"]
    assert system_context.session.active is False


def test_start_session_live_mode_refreshes_dead_man_switch(client, system_context):
    system_context.config.execution.mode = "live"
    system_context.config.execution.validate_only = False
    system_context.config.execution.allow_live_trading = True
    system_context.config.execution.paper_tests_completed = True
    system_context.session.mode = "live"
    system_context.execution_service.refresh_dead_man_switch = MagicMock()

    response = client.post("/api/system/session/start")

    assert response.status_code == 200
    assert response.json()["error"] is None
    system_context.execution_service.refresh_dead_man_switch.assert_called_once_with(
        force=True
    )


def test_patch_session_config_blocked_if_active(client, system_context):
    system_context.session.active = True
    response = client.patch(
        "/api/system/session/config",
        json={"loop_interval_sec": 10.0},
    )
    assert response.status_code == 200
    assert "active" in response.json()["error"]


def test_create_profile_updates_registry_and_allows_immediate_selection(
    client, system_context, tmp_path, monkeypatch
):
    import krakked.ui.routes.system as system_routes

    config_dir = tmp_path / "config"
    profiles_dir = config_dir / "profiles"
    profiles_dir.mkdir(parents=True)

    main_config_path = config_dir / "config.yaml"
    main_config_path.write_text("profiles: {}\nsession: {}\n")
    (config_dir / "config.test.yaml").write_text("{}\n")

    monkeypatch.setattr(system_routes, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(system_routes, "_resolve_effective_env", lambda *_: "test")
    monkeypatch.setattr(system_routes, "parse_app_config", lambda *args, **kwargs: None)

    response = client.post("/api/system/profiles", json={"name": "Swing Alpha"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["name"] == "SwingAlpha"
    assert system_context.reinitialize_event.is_set() is True

    created_profile = system_context.config.profiles["SwingAlpha"]
    assert isinstance(created_profile, ProfileConfig)
    assert created_profile.description == ""
    assert Path(created_profile.config_path) == Path("profiles") / "SwingAlpha.yaml"

    profiles_response = client.get("/api/system/profiles")
    assert profiles_response.status_code == 200
    assert profiles_response.json()["data"] == [
        {"name": "SwingAlpha", "description": ""}
    ]

    select_response = client.patch(
        "/api/system/session/config",
        json={"profile_name": "SwingAlpha"},
    )
    assert select_response.status_code == 200
    select_payload = select_response.json()
    assert select_payload["error"] is None
    assert select_payload["data"]["profile_name"] == "SwingAlpha"
    assert system_context.session.profile_name == "SwingAlpha"


def test_create_profile_preserves_existing_registry_entries_in_memory(
    client, system_context, tmp_path, monkeypatch
):
    import krakked.ui.routes.system as system_routes

    config_dir = tmp_path / "config"
    profiles_dir = config_dir / "profiles"
    profiles_dir.mkdir(parents=True)

    (profiles_dir / "existing.yaml").write_text("{}\n")
    main_config_path = config_dir / "config.yaml"
    main_config_path.write_text(
        "profiles:\n"
        "  existing:\n"
        "    name: existing\n"
        "    description: Existing profile\n"
        "    config_path: profiles/existing.yaml\n"
        "    credentials_path: ''\n"
        "    default_mode: paper\n"
    )
    (config_dir / "config.test.yaml").write_text("{}\n")

    system_context.config.profiles["existing"] = ProfileConfig(
        name="existing",
        description="Existing profile",
        config_path="profiles/existing.yaml",
        credentials_path="",
        default_mode="paper",
    )

    monkeypatch.setattr(system_routes, "get_config_dir", lambda: config_dir)
    monkeypatch.setattr(system_routes, "_resolve_effective_env", lambda *_: "test")
    monkeypatch.setattr(system_routes, "parse_app_config", lambda *args, **kwargs: None)

    response = client.post(
        "/api/system/profiles",
        json={"name": "Momentum Desk", "description": "Rotates faster"},
    )

    assert response.status_code == 200
    assert response.json()["error"] is None
    assert sorted(system_context.config.profiles.keys()) == ["MomentumDesk", "existing"]


def test_start_session_blocked_during_reload(client, system_context):
    system_context.reinitialize_event.set()
    response = client.post("/api/system/session/start")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert "reloading" in payload["error"].lower()
    assert system_context.session.active is False


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
def test_credential_validation_auth_and_missing_fields(
    monkeypatch, client, ui_auth_token
):
    headers = {"Authorization": "Bearer wrong"}

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

    def make_result(exc):
        return CredentialResult(
            api_key="k",
            api_secret="s",
            status=(
                CredentialStatus.LOADED
                if exc is None
                else CredentialStatus.SERVICE_ERROR
            ),
            source="validation",
            validated=exc is None,
            can_force_save=True,
            validation_error=str(exc) if exc else None,
            error=exc,
        )

    monkeypatch.setattr(
        validation_mod, "validate_credentials", lambda *_, **__: make_result(None)
    )
    success = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "k", "apiSecret": "s", "region": "r"},
        headers=headers,
    )
    assert success.json() == {"data": {"valid": True}, "error": None}

    monkeypatch.setattr(
        validation_mod,
        "validate_credentials",
        lambda *_, **__: make_result(AuthError("bad")),
    )
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
        validation_mod,
        "validate_credentials",
        lambda *_, **__: make_result(ServiceUnavailableError("down")),
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

    monkeypatch.setattr(
        validation_mod,
        "validate_credentials",
        lambda *_, **__: make_result(KrakenAPIError("err")),
    )
    api_error = client.post(
        "/api/system/credentials/validate",
        json={"apiKey": "k", "apiSecret": "s", "region": "r"},
        headers=headers,
    )
    assert api_error.json() == {
        "data": {"valid": False},
        "error": "Authentication failed. Please verify your API key/secret.",
    }


@pytest.mark.parametrize("ui_auth_enabled", [True])
def test_ui_credential_validation_logs_do_not_include_secrets(
    monkeypatch, client, ui_auth_token, caplog
):
    caplog.set_level(logging.WARNING, logger="krakked.ui.routes.system")

    fake_key = "FAKE_API_KEY_123"
    fake_secret = "FAKE_API_SECRET_456"

    def fake_validate(api_key, api_secret, *, region=None):
        assert api_key == fake_key
        assert api_secret == fake_secret
        assert region == "r"
        return CredentialResult(
            api_key=api_key,
            api_secret=api_secret,
            status=CredentialStatus.SERVICE_ERROR,
            source="validation",
            validated=False,
            can_force_save=True,
            validation_error="down",
            error=ServiceUnavailableError("down"),
        )

    monkeypatch.setattr(validation_mod, "validate_credentials", fake_validate)

    headers = {"Authorization": f"Bearer {ui_auth_token}"}

    client.post(
        "/api/system/credentials/validate",
        json={"apiKey": fake_key, "apiSecret": fake_secret, "region": "r"},
        headers=headers,
    )

    assert caplog.records
    for record in caplog.records:
        msg = record.getMessage()
        assert fake_key not in msg
        assert fake_secret not in msg
        for value in record.__dict__.values():
            if isinstance(value, str):
                assert fake_key not in value
                assert fake_secret not in value


def test_setup_unlock_remember_failure_is_best_effort(
    monkeypatch, client, system_context
):
    import krakked.ui.routes.system as system_routes

    system_context.is_setup_mode = True
    system_context.reinitialize_event.clear()

    # Mock all necessary functions to isolate test from FS/Keyring
    monkeypatch.setattr(
        system_routes, "unlock_secrets", lambda _pw, secrets_path=None: {"ok": True}
    )
    monkeypatch.setattr(
        system_routes, "set_session_master_password", lambda _aid, _pw: None
    )
    monkeypatch.setattr(system_routes, "resolve_secrets_path", MagicMock())

    # Mock save_master_password to raise
    def fail_save(_aid, _pw):
        raise RuntimeError("keyring down")

    monkeypatch.setattr(system_routes, "save_master_password", fail_save)

    resp = client.post(
        "/api/system/setup/unlock", json={"password": "pw", "remember": True}
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["error"] is None
    assert payload["data"]["success"] is True
    assert payload["data"]["remember_saved"] is False
    assert "keyring down" in payload["data"]["remember_error"]
    assert system_context.reinitialize_event.is_set() is True


def test_setup_unlock_remember_success_sets_flag(monkeypatch, client, system_context):
    import krakked.ui.routes.system as system_routes

    system_context.is_setup_mode = True
    system_context.reinitialize_event.clear()

    monkeypatch.setattr(
        system_routes, "unlock_secrets", lambda _pw, secrets_path=None: {"ok": True}
    )
    monkeypatch.setattr(
        system_routes, "set_session_master_password", lambda _aid, _pw: None
    )
    monkeypatch.setattr(system_routes, "resolve_secrets_path", MagicMock())

    saved = {}

    def _save(aid, pw) -> None:
        saved[aid] = pw

    monkeypatch.setattr(system_routes, "save_master_password", _save)

    resp = client.post(
        "/api/system/setup/unlock", json={"password": "pw", "remember": True}
    )
    payload = resp.json()
    assert payload["error"] is None
    assert payload["data"]["success"] is True
    assert payload["data"]["remember_saved"] is True
    assert payload["data"]["remember_error"] is None
    # Assuming default account for setup
    assert (
        saved.get("default") == "pw"
        or saved.get(system_context.session.account_id) == "pw"
    )


def test_system_reset_aborts_on_resolve_failure(monkeypatch, client, system_context):
    """Ensure system_reset does not delete default secrets if resolution fails."""
    import krakked.ui.routes.system as system_routes

    # Setup context with non-default account
    system_context.session.account_id = "nondefault"

    # Mock resolve_secrets_path to raise
    monkeypatch.setattr(
        system_routes,
        "resolve_secrets_path",
        MagicMock(side_effect=ValueError("Resolution failed")),
    )

    # Mock delete_secrets to spy on calls
    mock_delete = MagicMock()
    monkeypatch.setattr(system_routes, "delete_secrets", mock_delete)

    # Call reset
    resp = client.post("/api/system/reset")

    # Assert failure response
    assert resp.status_code == 200  # API returns 200 with error envelope
    payload = resp.json()
    assert payload["error"] == "Failed to resolve secrets path for selected account"
    assert payload["data"] is None

    # Assert delete_secrets was NOT called
    mock_delete.assert_not_called()
