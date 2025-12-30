import os
import threading
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from kraken_bot.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    MLConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    SessionConfig,
    UIConfig,
    UniverseConfig,
)
from kraken_bot.config_loader import RUNTIME_OVERRIDES_FILENAME, parse_app_config
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
        ml=MLConfig(enabled=True),
    )

    session = SessionConfig(
        active=False,
        profile_name=None,
        mode="paper",
        loop_interval_sec=60,
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


def test_config_apply_succeeds_on_valid_universe(client, safe_context, temp_config_dir):
    safe_context.market_data.validate_pairs.return_value = []  # No invalid pairs

    with patch(
        "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
    ):
        payload = {
            "config": {"universe": {"include_pairs": ["XBTUSD"]}},
            "dry_run": True,
        }
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
                "ui": {"host": "127.0.0.1"},
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
        initial_overrides = {"ui": {"host": "0.0.0.0"}}
        with open(profile_overrides, "w") as f:
            yaml.safe_dump(initial_overrides, f)

        # Apply 'ui' config (should write to main config, but MUST prune from profile override)
        payload = {"config": {"ui": {"host": "127.0.0.2"}}}

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is None

        # Verify UI written to main config
        with open(temp_config_dir / "config.yaml") as f:
            main_cfg = yaml.safe_load(f)
            assert main_cfg.get("ui", {}).get("host") == "127.0.0.2"

        # Verify 'ui' pruned from PROFILE overrides
        # The file might be deleted if empty, or just missing the key
        if profile_overrides.exists():
            po = yaml.safe_load(profile_overrides.read_text())
            assert "ui" not in po, f"UI key not pruned from profile overrides: {po}"
        else:
            # If file is gone, that's also valid pruning (empty dict -> delete)
            pass


# --- PR5 NEW TESTS START HERE ---


def test_dry_run_full_validation_failure(client, safe_context, temp_config_dir):
    """Test A: dry_run fails on invalid config (e.g., missing risk limits)."""
    # Simulate LIVE env to force strict checks
    with (
        patch.dict(os.environ, {"KRAKEN_BOT_ENV": "live"}),
        patch(
            "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
        ),
    ):

        # Valid config.yaml content
        with open(temp_config_dir / "config.yaml", "w") as f:
            yaml.safe_dump(
                {
                    "execution": {
                        "mode": "live",
                        "allow_live_trading": True,
                        "paper_tests_completed": True,
                    }
                },
                f,
            )

        # Payload enabling strategy WITHOUT risk limit
        payload = {
            "config": {
                "strategies": {
                    "enabled": ["my_strat"],
                    "configs": {"my_strat": {"type": "momentum"}},
                },
                # MISSING max_per_strategy_pct for 'my_strat'
                "risk": {"max_per_strategy_pct": {}},
            },
            "dry_run": True,
        }

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        data = response.json()

        # Should contain ValueError from loader
        assert data["error"] is not None
        assert "Live trading requires explicit max_per_strategy_pct" in data["error"]


def test_ui_refresh_intervals_profile_bound(client, safe_context, temp_config_dir):
    """Test B: UI refresh_intervals persist to Profile, others to Main."""
    with patch(
        "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
    ):

        # Setup active profile
        safe_context.session.profile_name = "test_profile"

        profile_dir = temp_config_dir / "profiles"
        profile_dir.mkdir(exist_ok=True)
        profile_path = profile_dir / "test.yaml"
        with open(profile_path, "w") as f:
            yaml.safe_dump({}, f)

        safe_context.config.profiles["test_profile"] = MagicMock(
            config_path="profiles/test.yaml"
        )

        # Payload mixing profile-bound UI and main-bound UI
        payload = {
            "config": {
                "ui": {
                    "refresh_intervals": {"dashboard_ms": 9999},
                    "host": "127.0.0.5",  # Should go to main
                }
            }
        }

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        assert response.json()["error"] is None

        # Verify Profile has refresh_intervals
        with open(profile_path) as f:
            p_cfg = yaml.safe_load(f)
            assert p_cfg["ui"]["refresh_intervals"]["dashboard_ms"] == 9999
            assert "host" not in p_cfg["ui"]

        # Verify Main has host
        with open(temp_config_dir / "config.yaml") as f:
            m_cfg = yaml.safe_load(f)
            assert m_cfg["ui"]["host"] == "127.0.0.5"
            # Main config should NOT have refresh_intervals update (it is stripped from main payload)
            if "ui" in m_cfg and "refresh_intervals" in m_cfg["ui"]:
                assert "refresh_intervals" not in m_cfg["ui"]


def test_profile_create_rejects_invalid_ui_keys(client, safe_context, temp_config_dir):
    """Test C: Profile creation rejects invalid UI keys."""
    with patch(
        "kraken_bot.ui.routes.system.get_config_dir", return_value=temp_config_dir
    ):

        payload = {
            "name": "bad_ui_profile",
            "base_config": {"ui": {"host": "0.0.0.0"}},  # NOT ALLOWED in profile
        }

        response = client.post("/api/system/profiles", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is not None
        assert "only ui.refresh_intervals is allowed" in data["error"]


def test_atomic_failure_no_writes(client, safe_context, temp_config_dir):
    """Test D: Validation failure results in NO disk writes."""
    with (
        patch.dict(os.environ, {"KRAKEN_BOT_ENV": "live"}),
        patch(
            "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
        ),
    ):

        # Initial config state
        main_path = temp_config_dir / "config.yaml"
        with open(main_path, "w") as f:
            yaml.safe_dump(
                {
                    "execution": {
                        "mode": "live",
                        "allow_live_trading": True,
                        "paper_tests_completed": True,
                    },
                    "foo": "original",
                },
                f,
            )

        # Invalid payload (missing risk limit in live mode)
        payload = {
            "config": {
                "strategies": {
                    "enabled": ["s1"],
                    "configs": {"s1": {"type": "momentum"}},
                },
                "risk": {"max_per_strategy_pct": {}},
                "foo": "changed",
            },
            "dry_run": False,
        }

        response = client.post("/api/config/apply", json=payload)
        data = response.json()

        assert data["error"] is not None

        # Verify file untouched
        with open(main_path) as f:
            content = yaml.safe_load(f)
            assert content["foo"] == "original"


def test_corrupted_yaml_triggers_validation_failure(
    client, safe_context, temp_config_dir
):
    """Test E: Corrupted YAML file triggers validation failure."""
    with patch(
        "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
    ):

        # Corrupt the main config file
        with open(temp_config_dir / "config.yaml", "w") as f:
            f.write("execution: mode: paper: [BROKEN YAML")

        payload = {"config": {"ui": {"host": "127.0.0.1"}}, "dry_run": True}

        response = client.post("/api/config/apply", json=payload)
        data = response.json()

        assert data["error"] is not None
        assert "Main config corrupted" in data["error"]


def test_apply_refresh_intervals_no_profile_fails(
    client, safe_context, temp_config_dir
):
    """Test F: Applying ui.refresh_intervals requires active profile."""
    with patch(
        "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
    ):

        # Ensure NO profile active
        safe_context.session.profile_name = None

        # Seed overrides to verify they are NOT pruned
        overrides_path = temp_config_dir / RUNTIME_OVERRIDES_FILENAME
        with open(overrides_path, "w") as f:
            yaml.safe_dump({"ui": {"refresh_intervals": {"dashboard_ms": 500}}}, f)

        payload = {"config": {"ui": {"refresh_intervals": {"dashboard_ms": 1000}}}}

        response = client.post("/api/config/apply", json=payload)
        data = response.json()

        assert data["error"] is not None
        assert "ui.refresh_intervals requires an active profile" in data["error"]

        # Verify overrides file untouched
        with open(overrides_path) as f:
            content = yaml.safe_load(f)
            assert content["ui"]["refresh_intervals"]["dashboard_ms"] == 500


def test_apply_ml_config_profile_bound(client, safe_context, temp_config_dir):
    """Test G: ML config changes require an active profile and persist to profile."""
    with patch(
        "kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir
    ):
        # 1. Test without active profile
        safe_context.session.profile_name = None
        payload = {"config": {"ml": {"enabled": False}}}

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        assert "ml settings require an active profile" in response.json()["error"]

        # 2. Test with active profile
        safe_context.session.profile_name = "test_profile"
        profile_dir = temp_config_dir / "profiles"
        profile_dir.mkdir(exist_ok=True)
        profile_path = profile_dir / "test.yaml"
        with open(profile_path, "w") as f:
            yaml.safe_dump({}, f)

        safe_context.config.profiles["test_profile"] = MagicMock(
            config_path="profiles/test.yaml"
        )

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        assert response.json()["error"] is None

        # Verify written to profile config
        with open(profile_path) as f:
            p_cfg = yaml.safe_load(f)
            assert p_cfg["ml"]["enabled"] is False


def test_config_loader_gating_live_mode_safety(client, safe_context, temp_config_dir):
    """
    Test H: In live env, if ml.enabled=False, ML strategies are disabled
    and removed from enabled list, bypassing the risk limit check.
    """
    with patch.dict(os.environ, {"KRAKEN_BOT_ENV": "live"}):

        # Valid base config
        base_config = {
            "execution": {
                "mode": "live",
                "allow_live_trading": True,
                "paper_tests_completed": True,
            },
            "ml": {"enabled": False},
            "strategies": {
                "enabled": ["ai_strat", "basic_strat"],
                "configs": {
                    "ai_strat": {"type": "machine_learning"},
                    "basic_strat": {"type": "basic"},
                },
            },
            "risk": {
                # Only provide limit for basic_strat, intentionally missing ai_strat
                "max_per_strategy_pct": {"basic_strat": 5.0}
            },
        }

        # This call should succeed because ai_strat gets gated off before risk check
        config = parse_app_config(
            base_config,
            config_path=temp_config_dir / "config.yaml",
            effective_env="live",
        )

        assert "ai_strat" not in config.strategies.enabled
        assert "basic_strat" in config.strategies.enabled
        assert config.strategies.configs["ai_strat"].enabled is False
        assert config.ml.enabled is False
