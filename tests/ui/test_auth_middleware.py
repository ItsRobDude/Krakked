"""Tests for AuthMiddleware token validation."""

import secrets
from unittest.mock import MagicMock

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
    UIAuthConfig,
    UIConfig,
    UniverseConfig,
)
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext, SessionState


def _mock_config_dirs(monkeypatch, tmp_path):
    """Apply config dir patches to all relevant modules."""
    monkeypatch.setattr("kraken_bot.config_loader.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.ui.routes.system.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.secrets.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.config.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.ui.routes.config.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.accounts.get_config_dir", lambda: tmp_path)


def _create_auth_context(base_path="/krakked", auth_enabled=True, auth_token="s3cret"):
    """Create a minimal context with auth enabled and setup mode OFF."""

    ui_auth = UIAuthConfig(enabled=auth_enabled, token=auth_token or "")
    ui_conf = UIConfig(
        enabled=True,
        host="127.0.0.1",
        port=8000,
        base_path=base_path,
        auth=ui_auth,
        read_only=False,
    )

    region_cap = RegionCapabilities(
        supports_margin=False, supports_futures=False, supports_staking=False
    )
    region = RegionProfile(code="US", capabilities=region_cap)

    universe = UniverseConfig(
        include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0.0
    )

    market_data = MarketDataConfig(
        ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]
    )

    portfolio = PortfolioConfig()
    execution = ExecutionConfig()
    risk = RiskConfig()
    strategies = StrategiesConfig()
    session_config = SessionConfig()

    config = AppConfig(
        region=region,
        universe=universe,
        market_data=market_data,
        portfolio=portfolio,
        execution=execution,
        risk=risk,
        strategies=strategies,
        ui=ui_conf,
        profiles={},
        session=session_config,
    )

    session_state = SessionState()

    return AppContext(
        config=config,
        client=None,
        market_data=MagicMock(),
        portfolio_service=None,
        portfolio=None,
        strategy_engine=None,
        execution_service=None,
        metrics=MagicMock(),
        session=session_state,
        is_setup_mode=False,  # Setup mode OFF to bypass LifecycleMiddleware blocks
    )


def test_auth_middleware_rejects_missing_header(monkeypatch, tmp_path):
    """Verify requests without Authorization header are rejected."""
    _mock_config_dirs(monkeypatch, tmp_path)
    ctx = _create_auth_context(base_path="/krakked")
    app = create_api(ctx)
    client = TestClient(app)

    # Valid endpoint
    resp = client.get("/krakked/api/system/session")
    assert resp.status_code == 401
    assert resp.json() == {"data": None, "error": "Unauthorized"}


def test_auth_middleware_rejects_invalid_token(monkeypatch, tmp_path):
    """Verify requests with wrong token are rejected."""
    _mock_config_dirs(monkeypatch, tmp_path)
    ctx = _create_auth_context(base_path="/krakked", auth_token="correct-token")
    app = create_api(ctx)
    client = TestClient(app)

    resp = client.get(
        "/krakked/api/system/session",
        headers={"Authorization": "Bearer wrong-token"}
    )
    assert resp.status_code == 401


def test_auth_middleware_accepts_valid_token(monkeypatch, tmp_path):
    """Verify requests with correct token are accepted."""
    _mock_config_dirs(monkeypatch, tmp_path)
    ctx = _create_auth_context(base_path="/krakked", auth_token="correct-token")
    app = create_api(ctx)
    client = TestClient(app)

    resp = client.get(
        "/krakked/api/system/session",
        headers={"Authorization": "Bearer correct-token"}
    )
    assert resp.status_code == 200


def test_auth_middleware_timing_attack_mitigation(monkeypatch, tmp_path):
    """
    Verify that compare_digest is used for token validation.

    This is a structural test that patches secrets.compare_digest to ensure it is called.
    """
    _mock_config_dirs(monkeypatch, tmp_path)
    ctx = _create_auth_context(base_path="/krakked", auth_token="correct-token")
    app = create_api(ctx)
    client = TestClient(app)

    # Patch secrets.compare_digest to verify it's used
    # We must wrap the real function because the middleware needs the correct result to proceed
    real_compare_digest = secrets.compare_digest
    mock_compare = MagicMock(side_effect=real_compare_digest)

    # We need to patch where it is used. Since we are going to modify the code
    # to import secrets, we will eventually patch 'secrets.compare_digest'.
    # For now, since the code uses !=, this test would fail if we assert it was called.
    # So this test serves as a verification of the FIX.

    with monkeypatch.context() as m:
        m.setattr(secrets, "compare_digest", mock_compare)

        # This request currently SUCCEEDS but uses !=, so mock_compare won't be called.
        # After we fix the code, this test should pass with the assertion.
        resp = client.get(
            "/krakked/api/system/session",
            headers={"Authorization": "Bearer correct-token"}
        )
        assert resp.status_code == 200

        # In the vulnerable code, this assertion would fail.
        mock_compare.assert_called()
