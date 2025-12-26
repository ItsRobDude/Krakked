
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from kraken_bot.config import AppConfig, ProfileConfig
from kraken_bot.ui.routes.presets import PresetApplyPayload, ALLOWED_KINDS

@pytest.fixture
def test_client(client, tmp_path):
    """
    Client with patched config directories for both routes.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Create minimal valid config
    (config_dir / "config.yaml").write_text("version: 1.0\n")

    # Create profiles dir
    (config_dir / "profiles").mkdir()

    # Patch get_config_dir in both modules
    with patch("kraken_bot.ui.routes.config.get_config_dir", return_value=config_dir), \
         patch("kraken_bot.ui.routes.presets.get_config_dir", return_value=config_dir), \
         patch("kraken_bot.config.get_config_dir", return_value=config_dir):
        yield client

def test_apply_risk_preset_success(test_client, tmp_path):
    """Verify applying a valid risk preset updates the profile config."""
    config_dir = tmp_path / "config"
    profile_path = config_dir / "profiles" / "test.yaml"

    # 1. Setup Active Profile
    profile_data = {"risk": {"max_risk_per_trade_pct": 1.0}}
    profile_path.write_text(yaml.safe_dump(profile_data))

    ctx = test_client.app.state.context
    ctx.session.profile_name = "test_profile"
    # Create mock profile config in registry
    ctx.config.profiles = {
        "test_profile": ProfileConfig(
            name="test_profile",
            config_path="profiles/test.yaml",
            description="Test Profile"
        )
    }

    # 2. Create Preset
    preset_dir = config_dir / "presets" / "risk"
    preset_dir.mkdir(parents=True)
    preset_data = {
        "name": "Aggressive",
        "kind": "risk",
        "payload": {"max_risk_per_trade_pct": 5.0}
    }
    (preset_dir / "Aggressive.yaml").write_text(yaml.safe_dump(preset_data))

    # 3. Apply Preset
    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "Aggressive", "dry_run": False}
    )

    assert response.status_code == 200
    res_json = response.json()
    assert res_json["error"] is None
    assert res_json["data"]["status"] == "applied"
    assert res_json["data"]["preset"]["kind"] == "risk"

    # 4. Verify Persistence
    updated_profile = yaml.safe_load(profile_path.read_text())
    assert updated_profile["risk"]["max_risk_per_trade_pct"] == 5.0

    # 5. Verify Reload Triggered
    assert ctx.reinitialize_event.is_set()

def test_apply_preset_dry_run(test_client, tmp_path):
    """Verify dry_run validates but does not persist changes."""
    config_dir = tmp_path / "config"
    profile_path = config_dir / "profiles" / "test.yaml"

    # Setup Profile with initial value 1.0
    profile_path.write_text(yaml.safe_dump({"risk": {"max_risk_per_trade_pct": 1.0}}))

    ctx = test_client.app.state.context
    ctx.session.profile_name = "test_profile"
    ctx.config.profiles = {
        "test_profile": ProfileConfig(name="test_profile", config_path="profiles/test.yaml")
    }
    ctx.reinitialize_event.clear()

    # Create Preset with value 5.0
    preset_dir = config_dir / "presets" / "risk"
    preset_dir.mkdir(parents=True)
    (preset_dir / "Aggressive.yaml").write_text(yaml.safe_dump({
        "name": "Aggressive", "kind": "risk", "payload": {"max_risk_per_trade_pct": 5.0}
    }))

    # Apply with dry_run=True
    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "Aggressive", "dry_run": True}
    )

    assert response.status_code == 200
    res_json = response.json()
    assert res_json["error"] is None
    assert res_json["data"]["status"] == "valid"

    # Verify NO changes
    updated_profile = yaml.safe_load(profile_path.read_text())
    assert updated_profile["risk"]["max_risk_per_trade_pct"] == 1.0
    assert not ctx.reinitialize_event.is_set()

def test_apply_preset_no_active_profile(test_client):
    """Verify error when no profile is active."""
    ctx = test_client.app.state.context
    ctx.session.profile_name = None

    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "Any", "dry_run": True}
    )

    assert response.status_code == 200
    assert response.json()["error"] == "No active profile selected"

def test_apply_preset_session_active(test_client):
    """Verify error when session is active."""
    ctx = test_client.app.state.context
    ctx.session.active = True
    # Make sure we're not read-only
    ctx.config.ui.read_only = False

    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "Any", "dry_run": True}
    )

    assert response.status_code == 200
    assert "Cannot apply preset while session is active" in response.json()["error"]

def test_apply_preset_read_only(test_client):
    """Verify error in read-only mode."""
    ctx = test_client.app.state.context
    ctx.config.ui.read_only = True

    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "Any", "dry_run": True}
    )

    assert response.status_code == 200
    assert response.json()["error"] == "UI is in read-only mode"

def test_apply_preset_invalid_payload(test_client, tmp_path):
    """Verify error if preset payload is not a dictionary."""
    config_dir = tmp_path / "config"
    preset_dir = config_dir / "presets" / "risk"
    preset_dir.mkdir(parents=True)

    ctx = test_client.app.state.context
    ctx.session.profile_name = "test_profile"

    # Create invalid preset
    (preset_dir / "BadPayload.yaml").write_text(yaml.safe_dump({
        "name": "BadPayload", "kind": "risk", "payload": ["not", "a", "dict"]
    }))

    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "BadPayload", "dry_run": True}
    )

    assert response.status_code == 200
    assert "Preset payload must be a mapping" in response.json()["error"]

def test_apply_preset_not_found(test_client):
    """Verify error if preset file does not exist."""
    ctx = test_client.app.state.context
    ctx.session.profile_name = "test_profile"

    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "NonExistent", "dry_run": True}
    )

    assert response.status_code == 200
    assert response.json()["error"] == "Preset not found"

def test_apply_preset_kind_mismatch(test_client, tmp_path):
    """Verify error if preset file kind matches payload kind."""
    config_dir = tmp_path / "config"
    preset_dir = config_dir / "presets" / "risk"
    preset_dir.mkdir(parents=True)

    ctx = test_client.app.state.context
    ctx.session.profile_name = "test_profile"

    # Create mismatched preset (strategies kind in risk folder)
    (preset_dir / "Mismatch.yaml").write_text(yaml.safe_dump({
        "name": "Mismatch", "kind": "strategies", "payload": {}
    }))

    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "risk", "name": "Mismatch", "dry_run": True}
    )

    assert response.status_code == 200
    assert response.json()["error"] == "Preset kind mismatch"

def test_apply_preset_invalid_kind(test_client):
    """Verify error when kind is invalid, checking deterministic message."""
    ctx = test_client.app.state.context
    ctx.session.profile_name = "test_profile"

    response = test_client.post(
        "/api/presets/apply",
        json={"kind": "invalid_kind", "name": "Any", "dry_run": True}
    )

    assert response.status_code == 200
    error_msg = response.json()["error"]
    assert "Invalid kind. Allowed:" in error_msg
    # Ensure allowed kinds are sorted in the message
    allowed_sorted = str(sorted(ALLOWED_KINDS))
    assert allowed_sorted in error_msg
