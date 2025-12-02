from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
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
from kraken_bot.execution.models import LocalOrder
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.metrics import SystemMetrics
from kraken_bot.strategy.models import ExecutionPlan, RiskAdjustedAction
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext


@pytest.fixture
def risk_context(client: TestClient):
    return client.context  # type: ignore[attr-defined]


class RecordingAdapter:
    def __init__(self, config: ExecutionConfig | None = None):
        self.config = config or ExecutionConfig()
        self.submit_order_calls: list[Any] = []
        self.cancel_order_calls: list[Any] = []
        self.cancel_all_calls: int = 0
        self.client = SimpleNamespace(
            get_open_orders=lambda params=None: {},
            get_closed_orders=lambda: {},
        )

    def submit_order(self, order: LocalOrder) -> LocalOrder:
        self.submit_order_calls.append(order)
        return order

    def cancel_order(self, order: LocalOrder) -> None:
        self.cancel_order_calls.append(order)

    def cancel_all_orders(self) -> None:
        self.cancel_all_calls += 1


class StubStrategyEngine:
    def __init__(self, risk_config):
        self._kill_switch_active = False
        self._manual_kill_switch_active = False
        self.risk_engine = SimpleNamespace(config=risk_config)
        self.strategy_states = {}

    def set_manual_kill_switch(self, active: bool) -> None:
        self._manual_kill_switch_active = active

    def get_risk_status(self) -> SimpleNamespace:
        return SimpleNamespace(
            kill_switch_active=self._kill_switch_active
            or self._manual_kill_switch_active,
            daily_drawdown_pct=0.0,
            drift_flag=False,
            total_exposure_pct=0.0,
            manual_exposure_pct=0.0,
            per_asset_exposure_pct={},
            per_strategy_exposure_pct={},
        )

    def get_strategy_state(self):
        return []


def _build_action(pair: str) -> RiskAdjustedAction:
    return RiskAdjustedAction(
        pair=pair,
        strategy_id="ui_strategy",
        action_type="open",
        target_base_size=1.0,
        target_notional_usd=100.0,
        current_base_size=0.0,
        reason="",
        blocked=False,
        blocked_reasons=[],
        strategy_tag="ui_strategy",
        risk_limits_snapshot={},
    )


def _build_app_config_for_risk() -> AppConfig:
    region = RegionProfile(
        code="TEST",
        capabilities=RegionCapabilities(
            supports_margin=False,
            supports_futures=False,
            supports_staking=False,
        ),
        default_quote="USD",
    )

    return AppConfig(
        region=region,
        universe=UniverseConfig(
            include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={},
            ohlc_store={},
            backfill_timeframes=[],
            ws_timeframes=[],
            metadata_path=None,
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
            auth=UIAuthConfig(enabled=False, token="token"),
            read_only=False,
            refresh_intervals=UIRefreshConfig(),
        ),
    )


def _build_live_risk_context():
    config = _build_app_config_for_risk()
    adapter = RecordingAdapter(config.execution)
    strategy_engine = StubStrategyEngine(config.risk)
    execution_service = ExecutionService(
        adapter=adapter,
        config=config.execution,
        risk_status_provider=strategy_engine.get_risk_status,
    )

    context = AppContext(
        config=config,
        client=MagicMock(name="rest_client"),
        market_data=MagicMock(name="market_data"),
        portfolio=MagicMock(name="portfolio_service"),
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=SystemMetrics(),
    )

    context.portfolio.store = MagicMock()
    return context, adapter


def test_get_risk_status_enveloped(client, risk_context):
    risk_context.strategy_engine.get_risk_status.return_value = SimpleNamespace(
        kill_switch_active=False,
        daily_drawdown_pct=1.0,
        drift_flag=False,
        total_exposure_pct=2.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={"BTC": 1.0},
        per_strategy_exposure_pct={"alpha": 2.0},
    )

    response = client.get("/api/risk/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["per_strategy_exposure_pct"] == {"alpha": 2.0}


def test_get_risk_config_enveloped(client, risk_context):
    response = client.get("/api/risk/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert (
        payload["data"]["max_open_positions"]
        == risk_context.config.risk.max_open_positions
    )


@pytest.mark.parametrize("ui_read_only", [False])
def test_update_risk_config_mutates_context(client, risk_context):
    body = {"max_open_positions": 42, "max_daily_drawdown_pct": 3.0}

    response = client.patch("/api/risk/config", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert risk_context.config.risk.max_open_positions == 42
    assert risk_context.strategy_engine.risk_engine.config.max_daily_drawdown_pct == 3.0
    assert payload["data"]["max_daily_drawdown_pct"] == 3.0


@pytest.mark.parametrize("ui_read_only", [True])
def test_update_risk_config_blocked_in_read_only(client, risk_context):
    original = risk_context.config.risk.max_open_positions

    response = client.patch(
        "/api/risk/config", json={"max_open_positions": original + 1}
    )

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    assert risk_context.config.risk.max_open_positions == original


@pytest.mark.parametrize("ui_read_only", [False])
def test_kill_switch_updates_mock(client, risk_context):
    risk_context.strategy_engine.get_risk_status.return_value = SimpleNamespace(
        kill_switch_active=True,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
    )

    response = client.post("/api/risk/kill_switch", json={"active": True})

    assert response.status_code == 200
    payload = response.json()
    risk_context.strategy_engine.set_manual_kill_switch.assert_called_once_with(True)
    assert payload["error"] is None
    assert payload["data"]["kill_switch_active"] is True


@pytest.mark.parametrize("ui_read_only", [True])
def test_kill_switch_blocked_when_read_only(client, risk_context):
    response = client.post("/api/risk/kill_switch", json={"active": True})

    assert response.status_code == 200
    assert response.json() == {"data": None, "error": "UI is in read-only mode"}
    risk_context.strategy_engine.set_manual_kill_switch.assert_not_called()


def test_ui_kill_switch_blocks_execution_and_allows_cancels():
    context, adapter = _build_live_risk_context()
    app = create_api(context)
    client = TestClient(app)
    client.context = context  # type: ignore[attr-defined]

    response = client.post("/api/risk/kill_switch", json={"active": True})

    assert response.status_code == 200
    plan = ExecutionPlan(
        plan_id="ui_kill_switch_plan",
        generated_at=datetime.now(UTC),
        actions=[_build_action("XBTUSD"), _build_action("ETHUSD")],
    )

    result = context.execution_service.execute_plan(plan)

    assert not result.success
    assert adapter.submit_order_calls == []
    assert all("kill_switch" in (order.last_error or "") for order in result.orders)

    cancel_order = LocalOrder(
        local_id="cancel-1",
        plan_id="ui_kill_switch_plan",
        strategy_id="ui_strategy",
        pair="XBTUSD",
        side="buy",
        order_type="market",
    )

    context.execution_service.cancel_order(cancel_order)
    context.execution_service.cancel_all()

    assert adapter.cancel_order_calls == [cancel_order]
    assert adapter.cancel_all_calls == 1
