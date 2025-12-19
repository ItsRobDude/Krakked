import pytest
import yaml
from fastapi.testclient import TestClient

from kraken_bot.config_loader import (
    RUNTIME_OVERRIDES_FILENAME,
    dump_runtime_overrides,
    load_config,
)
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext, SessionState


@pytest.fixture
def config_dir(tmp_path):
    """Fixture providing a temporary config directory."""
    return tmp_path / "kraken_bot_config"


@pytest.fixture
def mock_config_dirs(monkeypatch, config_dir):
    """Monkeypatch config directories to point to tmp_path."""
    config_dir.mkdir()

    def _mock_get_config_dir():
        return config_dir

    monkeypatch.setattr("kraken_bot.config_loader.get_config_dir", _mock_get_config_dir)
    monkeypatch.setattr(
        "kraken_bot.ui.routes.system.get_config_dir", _mock_get_config_dir
    )
    return config_dir


def test_boot_never_resumes_running(mock_config_dirs):
    """Test A: Boot never resumes running even if config says active: true."""
    config_path = mock_config_dirs / "config.yaml"

    # Create legacy config with active: true
    config_data = {
        "region": {"code": "US_CA", "default_quote": "USD"},
        "universe": {"include_pairs": []},
        "session": {
            "active": True,  # Legacy state
            "mode": "paper",
            "loop_interval_sec": 15.0,
        },
    }

    with open(config_path, "w") as f:
        yaml.safe_dump(config_data, f)

    # Load config and verify coercion
    config = load_config(config_path)
    assert config.session.active is False


def test_start_stop_does_not_persist_active(mock_config_dirs):
    """Test B: Start/Stop session updates runtime state but never persists 'active'."""

    # 1. Setup minimal valid environment for API
    config_path = mock_config_dirs / "config.yaml"
    initial_config = {
        "region": {"code": "US_CA", "default_quote": "USD"},
        "universe": {"include_pairs": []},
        "execution": {"mode": "paper", "allow_live_trading": False},
        "ui": {"enabled": True, "auth": {"enabled": False}},
        "session": {"active": False, "mode": "paper"},
    }
    with open(config_path, "w") as f:
        yaml.safe_dump(initial_config, f)

    # Bootstrap app context
    config = load_config(config_path)
    # Ensure manual override of setup mode for test context
    ctx = AppContext(
        config=config,
        client=None,
        market_data=None,
        portfolio=None,
        portfolio_service=None,
        strategy_engine=None,
        execution_service=None,
        metrics=None,
        session=SessionState(),
        is_setup_mode=False,
    )
    # Inject context into app.state
    app = create_api(ctx)
    client = TestClient(app)

    # 2. START SESSION
    profile_name = "default"
    payload = {
        "profile_name": profile_name,
        "mode": "paper",
        "loop_interval_sec": 30.0,
        "ml_enabled": True,
    }
    response = client.post("/api/system/session/start", json=payload)
    assert response.status_code == 200, f"Start failed: {response.text}"
    assert response.json()["data"]["active"] is True
    assert ctx.session.active is True

    # Verify Disk Artifacts (Start)
    with open(config_path, "r") as f:
        saved_main = yaml.safe_load(f)
    assert "active" not in saved_main.get("session", {})

    # Check PROFILE SPECIFIC runtime overrides
    profile_runtime_path = (
        mock_config_dirs / "profiles" / profile_name / RUNTIME_OVERRIDES_FILENAME
    )
    assert profile_runtime_path.exists(), "Profile runtime file should be created"

    with open(profile_runtime_path, "r") as f:
        saved_runtime = yaml.safe_load(f)
    assert "active" not in saved_runtime.get("session", {})

    # 3. STOP SESSION
    response = client.post("/api/system/session/stop")
    assert response.status_code == 200, f"Stop failed: {response.text}"
    assert response.json()["data"]["active"] is False
    assert ctx.session.active is False

    # Verify Disk Artifacts (Stop)
    with open(config_path, "r") as f:
        saved_main = yaml.safe_load(f)
    assert "active" not in saved_main.get("session", {})

    with open(profile_runtime_path, "r") as f:
        saved_runtime = yaml.safe_load(f)
    assert "active" not in saved_runtime.get("session", {})


def test_stale_runtime_file_cleanup(mock_config_dirs):
    """Test C: Stale 'active' key in runtime overrides is actively scrubbed."""
    runtime_path = mock_config_dirs / RUNTIME_OVERRIDES_FILENAME

    # Pre-seed with stale data
    stale_data = {
        "session": {"active": True, "emergency_flatten": False},
        "risk": {"max_open_positions": 5},
    }
    with open(runtime_path, "w") as f:
        yaml.safe_dump(stale_data, f)

    # Create a dummy config to pass to dump
    config_path = mock_config_dirs / "config.yaml"
    config = load_config(config_path)  # defaults

    # Trigger dump on UNRELATED section
    dump_runtime_overrides(config, config_dir=mock_config_dirs, sections={"risk"})

    # Verify scrubbing
    with open(runtime_path, "r") as f:
        data = yaml.safe_load(f)

    session_block = data.get("session", {})
    assert "active" not in session_block
    assert session_block.get("emergency_flatten") is False
    # Logic check: we read existing, scrub active, update requested sections, write back.
    # We did not request session update, so emergency_flatten from existing should remain.


def test_emergency_flatten_persistence(mock_config_dirs):
    """Test D: Emergency flatten state IS persisted, while active is NOT."""
    runtime_path = mock_config_dirs / RUNTIME_OVERRIDES_FILENAME

    # Setup session with emergency flatten
    config_path = mock_config_dirs / "config.yaml"
    config = load_config(config_path)

    session_config = SessionState(
        active=True, emergency_flatten=True  # Should be stripped  # Should be kept
    )

    # Dump session section
    dump_runtime_overrides(
        config,
        config_dir=mock_config_dirs,
        session=session_config,
        sections={"session"},
    )

    # Verify
    with open(runtime_path, "r") as f:
        data = yaml.safe_load(f)

    session_block = data.get("session", {})
    assert session_block.get("emergency_flatten") is True
    assert "active" not in session_block
