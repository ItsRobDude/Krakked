"""Shared UI API fixtures for FastAPI route tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from kraken_bot.config import (
    AppConfig,
    ExecutionConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    RiskConfig,
    StrategiesConfig,
    UIAuthConfig,
    UIConfig,
    UIRefreshConfig,
    UniverseConfig,
)
from kraken_bot.metrics import SystemMetrics
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext


def _build_app_config(*, auth_enabled: bool, auth_token: str, read_only: bool) -> AppConfig:
    """Create a lightweight in-memory :class:`AppConfig` for UI tests."""

    region = RegionProfile(
        code="TEST",
        capabilities=RegionCapabilities(
            supports_margin=False,
            supports_futures=False,
            supports_staking=False,
        ),
        default_quote="USD",
    )

    config = AppConfig(
        region=region,
        universe=UniverseConfig(
            include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[], metadata_path=None
        ),
        portfolio=PortfolioConfig(),
        execution=ExecutionConfig(),
        risk=RiskConfig(),
        strategies=StrategiesConfig(configs={}, enabled=[]),
        ui=UIConfig(
            enabled=True,
            host="127.0.0.1",
            port=8080,
            base_path="/",
            auth=UIAuthConfig(enabled=auth_enabled, token=auth_token),
            read_only=read_only,
            refresh_intervals=UIRefreshConfig(),
        ),
    )

    return config


def _mock_equity() -> SimpleNamespace:
    return SimpleNamespace(
        equity_base=0.0,
        cash_base=0.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
        drift_flag=False,
    )


def _mock_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=0,
        equity_base=0.0,
        cash_base=0.0,
        realized_pnl_base_total=0.0,
        unrealized_pnl_base_total=0.0,
    )


def _mock_data_status() -> SimpleNamespace:
    return SimpleNamespace(
        rest_api_reachable=True,
        websocket_connected=True,
        streaming_pairs=0,
        stale_pairs=0,
        subscription_errors=0,
    )


def _mock_risk_status() -> SimpleNamespace:
    return SimpleNamespace(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )


def build_test_context(*, auth_enabled: bool, auth_token: str, read_only: bool) -> AppContext:
    """Construct an :class:`AppContext` populated with mocked services."""

    config = _build_app_config(
        auth_enabled=auth_enabled, auth_token=auth_token, read_only=read_only
    )

    rest_client = MagicMock(name="rest_client")

    market_data = MagicMock(name="market_data")
    market_data.get_latest_price.return_value = None
    market_data.get_data_status.return_value = _mock_data_status()

    portfolio = MagicMock(name="portfolio_service")
    portfolio.get_equity.return_value = _mock_equity()
    portfolio.get_latest_snapshot.return_value = None
    portfolio.get_positions.return_value = []
    portfolio.get_asset_exposure.return_value = []
    portfolio.get_trade_history.return_value = []
    portfolio.create_snapshot.return_value = _mock_snapshot()

    strategy_engine = MagicMock(name="strategy_engine")
    strategy_engine.get_risk_status.return_value = _mock_risk_status()
    strategy_engine.get_strategy_state.return_value = []
    strategy_engine.strategy_states = {}
    strategy_engine.risk_engine = MagicMock()
    strategy_engine.risk_engine.config = config.risk
    strategy_engine.set_manual_kill_switch = MagicMock()

    execution_service = MagicMock(name="execution_service")
    execution_service.adapter = MagicMock()
    execution_service.adapter.config = config.execution

    metrics = SystemMetrics()

    return AppContext(
        config=config,
        client=rest_client,
        market_data=market_data,
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
    )


@pytest.fixture
def ui_auth_token(request: pytest.FixtureRequest) -> str:
    """Per-test override for the UI auth token."""

    return getattr(request, "param", "test-token")


@pytest.fixture
def ui_auth_enabled(request: pytest.FixtureRequest) -> bool:
    """Toggle UI auth middleware for a given test."""

    return bool(getattr(request, "param", False))


@pytest.fixture
def ui_read_only(request: pytest.FixtureRequest) -> bool:
    """Toggle UI read-only mode for mutation endpoints."""

    return bool(getattr(request, "param", False))


@pytest.fixture
def mock_context(ui_auth_enabled: bool, ui_auth_token: str, ui_read_only: bool) -> AppContext:
    """Construct a mocked :class:`AppContext` for UI API tests."""

    return build_test_context(
        auth_enabled=ui_auth_enabled, auth_token=ui_auth_token, read_only=ui_read_only
    )


@pytest.fixture
def client(mock_context: AppContext) -> TestClient:
    """A FastAPI test client wired with a mocked :class:`AppContext`."""

    app = create_api(mock_context)
    client = TestClient(app)
    client.context = mock_context
    return client
