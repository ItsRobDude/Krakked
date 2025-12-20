"""Tests for LifecycleMiddleware enforcing locked mode access."""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext, SessionState
from kraken_bot.config import AppConfig, UIConfig, UIAuthConfig
from kraken_bot.ui.routes import system

def _mock_config_dirs(monkeypatch, tmp_path):
    """Apply config dir patches to all relevant modules."""
    monkeypatch.setattr("kraken_bot.config_loader.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.ui.routes.system.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.secrets.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.config.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.ui.routes.config.get_config_dir", lambda: tmp_path)

def _create_locked_context(base_path="/krakked", auth_enabled=False, auth_token=None):
    """Create a minimal context in setup mode."""
    # Build actual Config objects to support asdict() usage in routes
    ui_auth = UIAuthConfig(enabled=auth_enabled, token=auth_token)
    ui_conf = UIConfig(enabled=True, host="127.0.0.1", port=8000, base_path=base_path, auth=ui_auth, read_only=False)

    # Minimal skeletons for other sections to satisfy AppConfig
    # We use MagicMock for complex nested structures where asdict isn't called or isn't deep
    # But AppConfig itself must be a dataclass

    # We can rely on defaults for most things
    config = AppConfig(
        region=MagicMock(),
        universe=MagicMock(),
        market_data=MagicMock(),
        portfolio=MagicMock(),
        execution=MagicMock(),
        risk=MagicMock(),
        strategies=MagicMock(),
        ui=ui_conf,
        profiles={},
        # session must be a plain dict or serializable if accessed directly,
        # but here we can use MagicMock if we mock asdict() result or ensure deep serialization works?
        # Actually, MagicMock is not JSON serializable.
        # For config dump, we need basic python types.
        session=MagicMock()
    )

    # Patch asdict to handle MagicMocks gracefully during recursion? No, better to use real dicts/objects where possible.
    # OR, we mock the route handler for runtime config?
    # No, we want to test the middleware, not the route handler implementation details.
    # But the middleware allows the request through, and if the handler fails, we get 500, not 200.
    # So we need the handler to succeed.

    # Let's replace MagicMocks with real dataclasses or simple objects where possible, or just plain dicts for mocked fields if they are not typed strictly?
    # AppConfig fields are typed.

    # Simplest fix: The test just needs to pass 200.
    # We can mock `_redact_auth_token` or `asdict` in the route?
    pass

    session_state = SessionState()

    return AppContext(
        config=config,
        client=None,
        market_data=None,
        portfolio_service=None,
        portfolio=None,
        strategy_engine=None,
        execution_service=None,
        metrics=None,
        session=session_state,
        is_setup_mode=True
    )

def test_lifecycle_middleware_allowlist(monkeypatch, tmp_path):
    """Verify strictly allowed endpoints in locked mode."""
    _mock_config_dirs(monkeypatch, tmp_path)

    # Mock destructive functions in system routes
    mock_delete_secrets = MagicMock()
    mock_delete_pw = MagicMock()
    monkeypatch.setattr(system, "delete_secrets", mock_delete_secrets)
    monkeypatch.setattr(system, "delete_master_password", mock_delete_pw)
    monkeypatch.setattr(system, "set_session_master_password", MagicMock())

    ctx = _create_locked_context(base_path="/krakked")
    # Mock metrics for system health
    ctx.metrics = MagicMock()
    ctx.market_data = MagicMock()

    app = create_api(ctx)
    client = TestClient(app)

    # 1. Allowed Exact Matches
    allowed_gets = [
        "/krakked/api/system/session",
        "/krakked/api/system/health",
        "/krakked/api/system/profiles",
        "/krakked/api/health",
        # "/krakked/api/config/runtime",  <-- This fails due to MagicMock serialization issues in the handler.
        # We can test it separately with a patched handler if needed, or skip it here since
        # the middleware logic is shared with other allowed paths.
        # But to be robust, let's just patch the handler for this specific route in the test.
    ]

    # Special handling for runtime config to avoid serialization errors
    monkeypatch.setattr("kraken_bot.ui.routes.config.asdict", lambda x: {"mock": "config"})
    resp = client.get("/krakked/api/config/runtime")
    assert resp.status_code == 200

    for path in allowed_gets:
        resp = client.get(path)
        assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"

    # Reset (POST)
    resp = client.post("/krakked/api/system/reset")
    assert resp.status_code == 200
    mock_delete_secrets.assert_called_once()

    # 2. Allowed Prefix Matches
    # Setup unlock (POST) - Mocking unlock secrets logic to avoid errors
    monkeypatch.setattr(system, "unlock_secrets", MagicMock(return_value=True))
    resp = client.post(
        "/krakked/api/system/setup/unlock",
        json={"password": "dummy", "remember": False}
    )
    assert resp.status_code == 200

    # Accounts prefix (Future endpoint test - should be 404 not 503)
    resp = client.get("/krakked/api/system/accounts/list")
    assert resp.status_code == 404, "Expected 404 for missing accounts endpoint, got 503 (blocked) or other"

def test_lifecycle_middleware_blocklist(monkeypatch, tmp_path):
    """Verify everything else is blocked with strict 503."""
    _mock_config_dirs(monkeypatch, tmp_path)
    ctx = _create_locked_context(base_path="/krakked")
    app = create_api(ctx)
    client = TestClient(app)

    blocked_paths = [
        ("POST", "/krakked/api/config/apply"),
        ("POST", "/krakked/api/system/session/start"),
        ("GET", "/krakked/api/portfolio/summary"),
        ("GET", "/krakked/api/strategies"),
    ]

    expected_json = {"data": None, "error": "Setup required"}

    for method, path in blocked_paths:
        if method == "POST":
            # Pass dummy data to config/apply to ensure validation doesn't trigger 422 before middleware
            json_body = {"config": {}} if "config" in path else {}
            resp = client.post(path, json=json_body)
        else:
            resp = client.get(path)

        assert resp.status_code == 503, f"Expected 503 for {path}, got {resp.status_code}"
        assert resp.json() == expected_json, f"Invalid error envelope for {path}"

def test_lifecycle_priority_over_auth(monkeypatch, tmp_path):
    """Verify LifecycleMiddleware runs BEFORE AuthMiddleware."""
    _mock_config_dirs(monkeypatch, tmp_path)

    # Create context with Auth Enabled AND Locked Mode
    ctx = _create_locked_context(
        base_path="/krakked",
        auth_enabled=True,
        auth_token="supersecret"
    )

    app = create_api(ctx)
    client = TestClient(app)

    # 1. Blocked Endpoint (Config Apply)
    # Should return 503 (Lifecycle blocked), NOT 401 (Auth blocked)
    # Even though we send NO auth header
    resp = client.post("/krakked/api/config/apply", json={"config": {}})

    assert resp.status_code == 503, f"Expected 503 (Lifecycle), got {resp.status_code} (Likely 401 Auth)"
    assert resp.json() == {"data": None, "error": "Setup required"}

    # 2. Allowed Endpoint (Health)
    # Should return 200 (Publicly accessible)
    # Even though auth is enabled, Health is usually exempt, but we want to ensure
    # Lifecycle allows it AND Auth allows it (or it's exempt from Auth).
    # Since health is exempt from AuthMiddleware in current implementation,
    # this primarily tests that LifecycleMiddleware didn't block it.
    resp = client.get("/krakked/api/system/health")
    assert resp.status_code == 200
