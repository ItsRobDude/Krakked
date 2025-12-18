
import pytest
import shutil
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from kraken_bot.ui.api import create_api
from kraken_bot.config import AppConfig, ExecutionConfig, RegionProfile, RegionCapabilities, UniverseConfig, MarketDataConfig, PortfolioConfig
from kraken_bot.config_loader import RUNTIME_OVERRIDES_FILENAME

@pytest.fixture
def mock_context():
    ctx = MagicMock()
    # Populate required fields for AppConfig
    ctx.config = AppConfig(
        region=RegionProfile(
            code="US",
            default_quote="USD",
            capabilities=RegionCapabilities(
                supports_margin=True,
                supports_futures=False,
                supports_staking=True
            )
        ),
        universe=UniverseConfig(
            include_pairs=["XBT/USD"],
            exclude_pairs=[],
            min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={},
            ohlc_store={},
            backfill_timeframes=[],
            ws_timeframes=[]
        ),
        portfolio=PortfolioConfig(db_path="test.db")
    )
    # Ensure execution config is set correctly
    ctx.config.execution = ExecutionConfig(mode="paper", allow_live_trading=False)

    ctx.session.active = False
    ctx.session.profile_name = None
    # Ensure is_setup_mode is False so middleware doesn't block requests
    ctx.is_setup_mode = False

    # Mock market data validation
    ctx.market_data = MagicMock()
    ctx.market_data.validate_pairs.return_value = []
    return ctx

@pytest.fixture
def client(mock_context):
    app = create_api(mock_context)
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

def test_apply_config_restricted_keys_rejected(client, mock_context):
    """Test that changing restricted execution keys returns an error."""
    payload = {
        "config": {
            "execution": {
                "mode": "live",  # Changed from paper
                "allow_live_trading": True
            }
        }
    }

    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "cannot be modified" in data["error"]
    assert "Use /api/system/mode" in data["error"]

def test_apply_config_restricted_keys_stripped_if_same(client, mock_context, temp_config_dir):
    """Test that sending SAME restricted keys is allowed (stripped)."""
    # Mock get_config_dir to return our temp dir
    with patch("kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir):
        payload = {
            "config": {
                "execution": {
                    "mode": "paper",  # Same as current
                    "validate_only": True # Assuming default is True/False match (ExecutionConfig default is True)
                },
                "ui": {"theme": "light"}
            }
        }

        # Ensure context matches payload for this test
        # ExecutionConfig defaults: validate_only=True
        mock_context.config.execution.mode = "paper"
        mock_context.config.execution.validate_only = True

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["error"] is None
        assert data["data"]["status"] == "applied"

        # Verify execution keys were NOT written to file (stripped)
        # But UI key SHOULD be written
        with open(temp_config_dir / "config.yaml") as f:
            saved = yaml.safe_load(f)
            # 'execution' key might persist if merged, but the applied logic should have stripped it from payload
            # Since our temp config.yaml started with execution mode: paper, checking if it changed isn't enough.
            # But deep_merge_dicts works. If payload execution was empty, it merges nothing.
            # Key check: 'validate_only' wasn't in original file. If stripped, it shouldn't be in file.
            execution_section = saved.get("execution", {})
            assert "validate_only" not in execution_section

def test_apply_config_runtime_override_pruning(client, mock_context, temp_config_dir):
    """Test that persistent keys are removed from runtime overrides."""
    with patch("kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir):
        # 1. Setup: runtime overrides has 'ui' section
        overrides_path = temp_config_dir / RUNTIME_OVERRIDES_FILENAME
        assert "ui" in yaml.safe_load(overrides_path.read_text())

        # 2. Apply config containing 'ui' section
        payload = {
            "config": {
                "ui": {"refresh_interval": 10}
            }
        }

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200
        assert response.json()["error"] is None

        # 3. Verify 'ui' section removed from runtime overrides
        if overrides_path.exists():
            overrides = yaml.safe_load(overrides_path.read_text()) or {}
            assert "ui" not in overrides
        else:
            # File deleted if empty is also valid
            pass

def test_apply_config_preserves_unrelated_overrides(client, mock_context, temp_config_dir):
    """Test that applying one section doesn't wipe other sections from overrides."""
    with patch("kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir):
        # 1. Setup: runtime overrides has 'ui' and 'strategies'
        overrides_path = temp_config_dir / RUNTIME_OVERRIDES_FILENAME
        overrides_path.write_text("ui:\n  theme: dark\nstrategies:\n  active: true\n")

        # 2. Apply config containing ONLY 'ui'
        payload = {
            "config": {
                "ui": {"theme": "light"}
            }
        }

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200

        # 3. Verify 'strategies' remains in runtime overrides
        overrides = yaml.safe_load(overrides_path.read_text())
        assert "strategies" in overrides
        assert "ui" not in overrides  # ui should be pruned

def test_split_brain_persistence(client, mock_context, temp_config_dir):
    """Test profile vs main config splitting."""
    with patch("kraken_bot.ui.routes.config.get_config_dir", return_value=temp_config_dir):
        # Setup active profile
        mock_context.session.profile_name = "test_profile"
        mock_context.config.profiles = {"test_profile": MagicMock(config_path="profiles/test.yaml")}

        profile_dir = temp_config_dir / "profiles"
        profile_dir.mkdir()
        (profile_dir / "test.yaml").touch()

        # Setup profile runtime overrides
        profile_overrides = profile_dir / "test_profile" / RUNTIME_OVERRIDES_FILENAME
        profile_overrides.parent.mkdir()
        profile_overrides.write_text("risk:\n  max_drawdown: 0.1\n")

        # Payload with Trading (risk) and Main (ui) keys
        payload = {
            "config": {
                "risk": {"max_drawdown": 0.2},
                "ui": {"theme": "blue"}
            }
        }

        response = client.post("/api/config/apply", json=payload)
        assert response.status_code == 200

        # Verify Risk went to profile config
        with open(profile_dir / "test.yaml") as f:
            profile_cfg = yaml.safe_load(f)
            assert profile_cfg["risk"]["max_drawdown"] == 0.2

        # Verify UI went to main config
        with open(temp_config_dir / "config.yaml") as f:
            main_cfg = yaml.safe_load(f)
            assert main_cfg["ui"]["theme"] == "blue"

        # Verify profile runtime overrides pruned 'risk'
        if profile_overrides.exists():
            po = yaml.safe_load(profile_overrides.read_text())
            assert "risk" not in po
