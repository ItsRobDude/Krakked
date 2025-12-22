"""Tests for multi-account support routes."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kraken_bot.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    RiskConfig,
    SessionConfig,
    StrategiesConfig,
    UIConfig,
    UniverseConfig,
)
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext, SessionState

# --- Fixtures & Helpers ---


@pytest.fixture
def mock_config_dir(tmp_path):
    """
    Patches get_config_dir in ALL modules that might use it to ensure
    tests never touch the real user config directory.
    """
    with (
        patch("kraken_bot.config_loader.get_config_dir", return_value=tmp_path),
        patch("kraken_bot.ui.routes.system.get_config_dir", return_value=tmp_path),
        patch("kraken_bot.secrets.get_config_dir", return_value=tmp_path),
        patch("kraken_bot.config.get_config_dir", return_value=tmp_path),
        patch("kraken_bot.ui.routes.config.get_config_dir", return_value=tmp_path),
        patch("kraken_bot.accounts.get_config_dir", return_value=tmp_path),
        patch("kraken_bot.main.get_config_dir", return_value=tmp_path),
    ):
        yield tmp_path


@pytest.fixture
def mock_keyring():
    """In-memory keyring mock."""
    store = {}

    def get_password(service, username):
        return store.get((service, username))

    def set_password(service, username, password):
        store[(service, username)] = password

    def delete_password(service, username):
        if (service, username) in store:
            del store[(service, username)]

    with (
        patch("keyring.get_password", side_effect=get_password),
        patch("keyring.set_password", side_effect=set_password),
        patch("keyring.delete_password", side_effect=delete_password),
    ):
        yield store


@pytest.fixture
def mock_validation():
    """Mocks credential validation to always succeed."""
    result = MagicMock()
    result.validated = True
    result.status.name = "LOADED"
    with patch(
        "kraken_bot.connection.validation.validate_credentials", return_value=result
    ):
        yield


def _create_context(session_active=False, account_id="default"):
    """Creates a minimal AppContext for testing."""
    config = AppConfig(
        region=RegionProfile(
            code="US", capabilities=RegionCapabilities(False, False, False)
        ),
        universe=UniverseConfig(
            include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]
        ),
        portfolio=PortfolioConfig(),
        execution=ExecutionConfig(),
        risk=RiskConfig(),
        strategies=StrategiesConfig(),
        ui=UIConfig(
            enabled=True, host="127.0.0.1", port=8000, base_path="/krakked"
        ),  # Set base_path to match tests
        profiles={},
        session=SessionConfig(active=session_active, account_id=account_id),
    )

    session = SessionState(
        active=session_active, account_id=account_id, emergency_flatten=False
    )

    return AppContext(
        config=config,
        client=MagicMock(),
        market_data=MagicMock(),
        portfolio_service=MagicMock(),
        portfolio=MagicMock(),
        strategy_engine=MagicMock(),
        execution_service=MagicMock(),
        metrics=MagicMock(),
        session=session,
        is_setup_mode=False,
    )


# --- Tests ---


def test_list_accounts_returns_default(mock_config_dir, mock_keyring):
    ctx = _create_context()
    app = create_api(ctx)
    client = TestClient(app)

    # 1. First call should create default account
    resp = client.get("/krakked/api/system/accounts/list")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["selected_account_id"] == "default"
    assert len(data["accounts"]) == 1
    acc = data["accounts"][0]
    assert acc["id"] == "default"
    assert acc["name"] == "Default"
    assert acc["secrets_exist"] is False  # File not written yet
    assert acc["remembered"] is False


def test_create_account_success(mock_config_dir, mock_keyring, mock_validation):
    ctx = _create_context()
    app = create_api(ctx)
    client = TestClient(app)

    payload = {
        "name": "My Trading Account",
        "apiKey": "key123",
        "apiSecret": "secret123",
        "password": "strongpassword",
        "region": "US",
        "remember": True,
    }

    resp = client.post("/krakked/api/system/accounts/create", json=payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["success"] is True
    account_id = data["account_id"]
    assert account_id == "mytradingaccount"

    # Verify file structure
    secrets_path = mock_config_dir / f"accounts/{account_id}/secrets.enc"
    assert secrets_path.exists()
    assert secrets_path.parent.is_dir()

    # Verify Context Updated
    assert ctx.session.account_id == account_id
    assert ctx.reinitialize_event.is_set()

    # Verify Keyring
    key = f"master_password:{account_id}"
    assert mock_keyring.get(("Krakked", key)) == "strongpassword"


def test_select_account_validates_and_switches(
    mock_config_dir, mock_keyring, mock_validation
):
    ctx = _create_context(account_id="default")
    app = create_api(ctx)
    client = TestClient(app)

    # Pre-create a second account via API (easiest way to set up state)
    client.post(
        "/krakked/api/system/accounts/create",
        json={
            "name": "Alt",
            "apiKey": "k",
            "apiSecret": "s",
            "password": "p",
            "remember": False,
        },
    )
    alt_id = "alt"

    # Switch back to default manually for test setup (since create switches automatically)
    ctx.session.account_id = "default"
    ctx.reinitialize_event.clear()

    # Valid Selection
    resp = client.post(
        "/krakked/api/system/accounts/select", json={"account_id": alt_id}
    )
    assert resp.status_code == 200

    assert ctx.session.account_id == alt_id
    assert ctx.is_setup_mode is True  # Locked

    # Invalid Selection
    resp = client.post(
        "/krakked/api/system/accounts/select", json={"account_id": "ghost"}
    )
    assert resp.json()["error"] is not None


def test_select_fails_if_active(mock_config_dir, mock_keyring):
    ctx = _create_context(session_active=True)
    app = create_api(ctx)
    client = TestClient(app)

    resp = client.post(
        "/krakked/api/system/accounts/select", json={"account_id": "any"}
    )
    assert resp.json()["error"] == "Cannot switch accounts while session is active"


def test_unlock_account_explicit_password(
    mock_config_dir, mock_keyring, mock_validation
):
    # Setup: Create account
    ctx = _create_context()
    app = create_api(ctx)
    client = TestClient(app)

    client.post(
        "/krakked/api/system/accounts/create",
        json={
            "name": "Target",
            "apiKey": "k",
            "apiSecret": "s",
            "password": "pass",
            "remember": False,
        },
    )
    target_id = "target"

    # Lock it (simulate fresh start)
    ctx.session.account_id = target_id
    from kraken_bot.secrets import set_session_master_password

    set_session_master_password(target_id, None)

    # Unlock
    payload = {"password": "pass", "remember": True}
    resp = client.post("/krakked/api/system/accounts/unlock", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True

    # Verify remember me
    key = f"master_password:{target_id}"
    assert mock_keyring.get(("Krakked", key)) == "pass"


def test_unlock_account_saved_password_loopback_only(mock_config_dir, mock_keyring):
    ctx = _create_context()
    # Force non-loopback config
    ctx.config.ui.host = "0.0.0.0"

    app = create_api(ctx)
    client = TestClient(app)

    # Mock saved password existence
    mock_keyring[("Krakked", "master_password:default")] = "saved_pass"

    payload = {"use_saved_password": True}
    resp = client.post("/krakked/api/system/accounts/unlock", json=payload)

    # Should fail due to non-loopback
    assert "loopback interface" in resp.json()["error"]


def test_unlock_account_saved_password_success(
    mock_config_dir, mock_keyring, mock_validation
):
    ctx = _create_context(account_id="default")
    app = create_api(ctx)
    client = TestClient(app)

    # Setup secrets file
    from kraken_bot.secrets import persist_api_keys

    persist_api_keys(
        "k", "s", "saved_pass", validated=True, force_save_unvalidated=True
    )

    # Setup saved password
    mock_keyring[("Krakked", "master_password:default")] = "saved_pass"

    payload = {"use_saved_password": True}
    resp = client.post("/krakked/api/system/accounts/unlock", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True


def test_delete_account_logic(mock_config_dir, mock_keyring, mock_validation):
    ctx = _create_context()
    app = create_api(ctx)
    client = TestClient(app)

    # Create account to delete
    client.post(
        "/krakked/api/system/accounts/create",
        json={"name": "DelMe", "apiKey": "k", "apiSecret": "s", "password": "p"},
    )
    del_id = "delme"

    # Ensure it's selected
    assert ctx.session.account_id == del_id

    # Delete
    resp = client.delete(f"/krakked/api/system/accounts/{del_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True

    # Assert Reset Behavior
    assert ctx.session.account_id == "default"
    assert ctx.is_setup_mode is True

    # Assert Cleanup
    assert not (mock_config_dir / f"accounts/{del_id}").exists()


def test_cannot_delete_default_account(mock_config_dir, mock_keyring):
    ctx = _create_context(account_id="default")
    app = create_api(ctx)
    client = TestClient(app)

    # Ensure default exists
    client.get("/krakked/api/system/accounts/list")

    resp = client.delete("/krakked/api/system/accounts/default")
    assert resp.json()["error"] == "Cannot delete default account"
