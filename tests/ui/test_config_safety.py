import threading
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from kraken_bot.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    SessionConfig,
    UIConfig,
    UniverseConfig,
)
from kraken_bot.config_loader import RUNTIME_OVERRIDES_FILENAME
from kraken_bot.connection.exceptions import ServiceUnavailableError
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext


@pytest.fixture
def safe_context():
    # Use real objects for data containers to satisfy strict type checks (asdict, etc.)
    config = AppConfig(
        region=RegionProfile(
            code="US",
            default_quote="USD",
            capabilities=RegionCapabilities(
                supports_margin=True, supports_futures=False, supports_staking=True
            ),
        ),
        universe=UniverseConfig(
            include_pairs=["XBT/USD"], exclude_pairs=[], min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]
        ),
        portfolio=PortfolioConfig(db_path="test.db"),
        execution=ExecutionConfig(mode="paper", allow_live_trading=False),
        ui=UIConfig(base_path="/", read_only=False, enabled=True),
        # Default profiles dict
        profiles={},
    )

    session = SessionConfig(
        active=False,
        profile_name=None,
        mode="paper",
        loop_interval_sec=60,
        ml_enabled=True,
        emergency_flatten=False,
    )

    # Create real AppContext with mocked services
    ctx = AppContext(
        config=config,
        session=session,
        is_setup_mode=False,
        reinitialize_event=threading.Event(),
        market_data=MagicMock(spec=MarketDataAPI),
        client=MagicMock(),
        portfolio_service=MagicMock(),
        execution_service=MagicMock(),
        strategy_engine=MagicMock(),
        metrics=MagicMock(),
        portfolio=MagicMock(),  # Alias
    )

    # Setup market data mock default behavior
    ctx.market_data.validate_pairs.return_value = []
    ctx.market_data.get_health_status.return_value = None

    return ctx


@pytest.fixture
def client(safe_context):
    app = create_api(safe_context)
    return TestClient(app)


@pytest.fixture
def temp_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Create main config
    (config_dir / "config.yaml").write_text("execution:\n  mode: paper\n")

    # Create runtime overrides
    (config_dir / RUNTIME_OVERRIDES_FILENAME).write_text("ui:\n  theme: dark\n")

    return config_dir


def test_config_apply_rejects_execution_mode(client, safe_context):
    payload = {"config": {"execution": {"mode": "live"}}, "dry_run": True}
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Execution 'mode' cannot be modified" in data["error"]


def test_config_apply_rejects_allow_live_trading(client, safe_context):
    payload = {"config": {"execution": {"allow_live_trading": True}}, "dry_run": True}
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Execution 'allow_live_trading' cannot be modified" in data["error"]


def test_create_profile_rejects_base_config_execution_mode(client, safe_context):
    payload = {
        "name": "dangerous_profile",
        "default_mode": "paper",
        "base_config": {"execution": {"allow_live_trading": True}},
    }
    response = client.post("/api/system/profiles", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Execution 'allow_live_trading' cannot be set" in data["error"]


def test_config_apply_blocks_on_universe_validation_failure(client, safe_context):
    # Setup mock side effect
    safe_context.market_data.validate_pairs.side_effect = ServiceUnavailableError(
        "Kraken down"
    )

    payload = {"config": {"universe": {"include_pairs": ["XBTUSD"]}}, "dry_run": True}
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Universe validation unavailable" in data["error"]


def test_config_apply_succeeds_on_valid_universe(client, safe_context):
    safe_context.market_data.validate_pairs.return_value = []  # No invalid pairs

    payload = {"config": {"universe": {"include_pairs": ["XBTUSD"]}}, "dry_run": True}
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is None
    assert data["data"]["status"] == "valid"


def test_apply_config_restricted_keys_stripped_if_same(
    client, safe_context, temp_config_dir
):
    """Test that sending SAME restricted keys is allowed (stripped)."""
    # Mock get_config_dir to return our temp dir
    with patch(
        "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
    ):
        payload = {
            "config": {
                "execution": {
                    "mode": "paper",  # Same as current default
                    "validate_only": True,  # Same as current default
                },
                "ui": {"theme": "light"},
            }
        }

        # Ensure context matches payload for this test
        # ExecutionConfig defaults: validate_only=True, mode="paper"
        safe_context.config.execution.mode = "paper"
        safe_context.config.execution.validate_only = True

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is None
        assert data["data"]["status"] == "applied"

        # Verify execution keys were NOT written to file (stripped)
        # But UI key SHOULD be written
        with open(temp_config_dir / "config.yaml") as f:
            saved = yaml.safe_load(f)
            execution_section = saved.get("execution", {})
            # 'validate_only' is not in default config.yaml we created, so if stripped, it won't be there
            assert "validate_only" not in execution_section


def test_profile_runtime_override_pruning_with_main_key(
    client, safe_context, temp_config_dir
):
    """Test that applying a main-config key (ui) also prunes it from PROFILE overrides."""
    with patch(
        "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
    ):
        # Setup active profile
        safe_context.session.profile_name = "test_profile"
        # We need to add the profile to the config for it to be found
        safe_context.config.profiles["test_profile"] = MagicMock(
            config_path="profiles/test.yaml"
        )

        profile_dir = temp_config_dir / "profiles"
        profile_dir.mkdir(exist_ok=True)
        (profile_dir / "test.yaml").touch()

        # Setup profile runtime overrides containing 'ui' (which is technically a main key but overridden in profile context)
        profile_overrides_dir = profile_dir / "test_profile"
        profile_overrides_dir.mkdir(exist_ok=True)
        profile_overrides = profile_overrides_dir / RUNTIME_OVERRIDES_FILENAME

        # Initial content: UI override exists
        initial_overrides = {"ui": {"theme": "overridden_in_profile"}}
        with open(profile_overrides, "w") as f:
            yaml.safe_dump(initial_overrides, f)

        # Apply 'ui' config (should write to main config, but MUST prune from profile override)
        payload = {"config": {"ui": {"theme": "new_main_theme"}}}

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200

        # Verify UI written to main config
        with open(temp_config_dir / "config.yaml") as f:
            main_cfg = yaml.safe_load(f)
            assert main_cfg.get("ui", {}).get("theme") == "new_main_theme"

        # Verify 'ui' pruned from PROFILE overrides
        # The file might be deleted if empty, or just missing the key
        if profile_overrides.exists():
            po = yaml.safe_load(profile_overrides.read_text())
            assert "ui" not in po, f"UI key not pruned from profile overrides: {po}"
        else:
            # If file is gone, that's also valid pruning (empty dict -> delete)
            pass
