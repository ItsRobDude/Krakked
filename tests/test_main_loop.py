import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from kraken_bot.main import _refresh_metrics_state, _run_loop_iteration
from kraken_bot.market_data.api import MarketDataStatus
from kraken_bot.metrics import SystemMetrics
from kraken_bot.portfolio.models import DriftMismatchedAsset, DriftStatus


class StubMarketDataAPI:
    def __init__(self) -> None:
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True

    def shutdown(self) -> None:
        self.initialized = False


class StubEquity:
    def __init__(self, equity: float, realized: float, unrealized: float) -> None:
        self.equity_base = equity
        self.realized_pnl_base_total = realized
        self.unrealized_pnl_base_total = unrealized


class StubPortfolioService:
    def __init__(self) -> None:
        self.sync_calls = 0
        self.equity = StubEquity(1200.0, 15.0, 25.0)
        self.positions = ["BTC/USD"]
        self.drift_status = DriftStatus(
            drift_flag=False,
            expected_position_value_base=0.0,
            actual_balance_value_base=0.0,
            tolerance_base=0.0,
            mismatched_assets=[],
        )

    def initialize(self) -> None:
        ...

    def sync(self) -> None:
        self.sync_calls += 1

    def get_equity(self) -> StubEquity:
        return self.equity

    def get_positions(self):
        return list(self.positions)

    def get_drift_status(self) -> DriftStatus:
        return self.drift_status


class StubAction:
    def __init__(self, blocked: bool = False) -> None:
        self.blocked = blocked


class StubPlan:
    def __init__(self, plan_id: str, actions):
        self.plan_id = plan_id
        self.actions = actions


class StubStrategyEngine:
    calls: list[datetime]
    plan_counter: int

    def __init__(self) -> None:
        self.calls = []
        self.plan_counter = 0

    def initialize(self) -> None:
        ...

    def run_cycle(self, now: datetime) -> StubPlan:
        self.calls.append(now)
        self.plan_counter += 1
        actions = [StubAction(blocked=True), StubAction(blocked=False)]
        return StubPlan(plan_id=f"plan-{self.plan_counter}", actions=actions)


class StubExecutionResult:
    def __init__(self, errors=None, orders=None) -> None:
        self.errors = errors or []
        self.orders = orders or []


class StubExecutionService:
    plans: list[StubPlan]
    open_orders: list[str]

    def __init__(self) -> None:
        self.plans = []
        self.open_orders = ["order-1", "order-2"]

    def execute_plan(self, plan: StubPlan) -> StubExecutionResult:
        self.plans.append(plan)
        return StubExecutionResult()

    def cancel_all(self) -> None:
        ...

    def get_open_orders(self):
        return list(self.open_orders)


class StubMarketData:
    def __init__(self, status: MarketDataStatus | None = None) -> None:
        self.status = status or MarketDataStatus(health="healthy", max_staleness=0.0)

    def get_health_status(self) -> MarketDataStatus:
        return self.status


class StubSystemMetrics(SystemMetrics):
    state_updates: list[dict[str, object]]

    def __init__(self) -> None:
        super().__init__()
        self.state_updates = []

    def update_portfolio_state(
        self,
        *,
        equity_usd,
        realized_pnl_usd,
        unrealized_pnl_usd,
        open_orders_count,
        open_positions_count,
    ) -> None:
        super().update_portfolio_state(
            equity_usd=equity_usd,
            realized_pnl_usd=realized_pnl_usd,
            unrealized_pnl_usd=unrealized_pnl_usd,
            open_orders_count=open_orders_count,
            open_positions_count=open_positions_count,
        )
        self.state_updates.append(
            {
                "equity": equity_usd,
                "realized": realized_pnl_usd,
                "unrealized": unrealized_pnl_usd,
                "orders": open_orders_count,
                "positions": open_positions_count,
            }
        )


class FakeMetrics(SystemMetrics):
    def __init__(self) -> None:
        super().__init__()
        self.market_data_ok = True
        self.market_data_error_messages: list[str] = []
        self.drift_records: list[tuple[bool, str | None]] = []

    def record_market_data_error(self, message: str) -> None:  # type: ignore[override]
        self.market_data_ok = False
        self.market_data_error_messages.append(message)
        super().record_market_data_error(message)

    def record_drift(self, drift_flag: bool, message: str | None = None) -> None:  # type: ignore[override]
        self.drift_records.append((drift_flag, message))
        super().record_drift(drift_flag, message)


def test_run_loop_iteration_executes_scheduled_work_and_updates_metrics():
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 2

    portfolio = StubPortfolioService()
    strategy_engine = StubStrategyEngine()
    execution_service = StubExecutionService()
    market_data = StubMarketData()
    metrics = StubSystemMetrics()

    def refresh_metrics() -> None:
        _refresh_metrics_state(portfolio, execution_service, metrics)

    last_portfolio_sync = now - timedelta(seconds=portfolio_interval)
    last_strategy_cycle = now - timedelta(seconds=strategy_interval)

    last_portfolio_sync, last_strategy_cycle = _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    last_portfolio_sync, last_strategy_cycle = _run_loop_iteration(
        now=now + timedelta(seconds=1),
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    last_portfolio_sync, last_strategy_cycle = _run_loop_iteration(
        now=now + timedelta(seconds=2),
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    assert portfolio.sync_calls == 2, "portfolio sync should run according to configured interval"
    assert [call.isoformat() for call in strategy_engine.calls] == [
        now.isoformat(),
        (now + timedelta(seconds=1)).isoformat(),
        (now + timedelta(seconds=2)).isoformat(),
    ]
    assert [plan.plan_id for plan in execution_service.plans] == ["plan-1", "plan-2", "plan-3"]

    assert metrics.plans_generated == 3
    assert metrics.plans_executed == 3
    assert metrics.blocked_actions == 3
    assert metrics.execution_errors == 0
    expected_state = {
        "equity": 1200.0,
        "realized": 15.0,
        "unrealized": 25.0,
        "orders": 2,
        "positions": 1,
    }
    assert metrics.state_updates[-1] == expected_state
    assert all(update == expected_state for update in metrics.state_updates)
    assert len(metrics.state_updates) == 5, "metrics should refresh after syncs and strategy cycles"


def test_run_loop_iteration_updates_market_data_metrics_when_healthy():
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 5

    portfolio = StubPortfolioService()
    strategy_engine = StubStrategyEngine()
    execution_service = StubExecutionService()
    market_data = StubMarketData()
    metrics = FakeMetrics()

    def refresh_metrics() -> None:
        _refresh_metrics_state(portfolio, execution_service, metrics)

    _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=now - timedelta(seconds=strategy_interval),
        last_portfolio_sync=now - timedelta(seconds=portfolio_interval),
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    assert metrics.market_data_ok is True
    assert metrics.market_data_stale is False
    assert metrics.market_data_errors == 0
    assert metrics.market_data_error_messages == []
    assert metrics.drift_records[0] == (False, None)


def test_run_loop_iteration_counts_kill_switch_rejections_as_blocked_actions():
    now = datetime(2024, 2, 1, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 10

    portfolio = StubPortfolioService()
    strategy_engine = StubStrategyEngine()

    class KillSwitchExecutionService(StubExecutionService):
        def __init__(self) -> None:
            super().__init__()
            self.kill_switch_orders = [
                type(
                    "KillSwitchOrder",
                    (),
                    {"status": "rejected", "last_error": "Execution blocked by kill switch (kill_switch_active)"},
                )()
            ]

        def execute_plan(self, plan: StubPlan) -> StubExecutionResult:  # type: ignore[override]
            self.plans.append(plan)
            return StubExecutionResult(
                errors=["Execution blocked by kill switch"],
                orders=list(self.kill_switch_orders),
            )

    execution_service = KillSwitchExecutionService()
    market_data = StubMarketData()
    metrics = StubSystemMetrics()

    def refresh_metrics() -> None:
        _refresh_metrics_state(portfolio, execution_service, metrics)

    last_portfolio_sync = now
    last_strategy_cycle = now - timedelta(seconds=strategy_interval)

    _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    assert metrics.plans_generated == 1
    assert metrics.plans_executed == 1
    assert metrics.blocked_actions == 2
    assert metrics.execution_errors == 1


def test_run_loop_iteration_flags_drift_and_enables_kill_switch():
    now = datetime(2024, 2, 2, tzinfo=timezone.utc)
    strategy_interval = 60
    portfolio_interval = 60

    class DriftPortfolioService(StubPortfolioService):
        def __init__(self) -> None:
            super().__init__()
            self.drift_status = DriftStatus(
                drift_flag=True,
                expected_position_value_base=1500.0,
                actual_balance_value_base=1200.0,
                tolerance_base=10.0,
                mismatched_assets=[
                    DriftMismatchedAsset(
                        asset="BTC",
                        expected_quantity=0.5,
                        actual_quantity=0.4,
                        difference_base=300.0,
                    )
                ],
            )

    class DriftStrategyEngine(StubStrategyEngine):
        def __init__(self) -> None:
            super().__init__()
            self.kill_switch_calls: list[bool] = []
            risk_cfg = type("risk", (), {"kill_switch_on_drift": True})()
            self.config = type("cfg", (), {"risk": risk_cfg})()

        def set_manual_kill_switch(self, active: bool) -> None:  # type: ignore[override]
            self.kill_switch_calls.append(active)

    portfolio = DriftPortfolioService()
    strategy_engine = DriftStrategyEngine()
    execution_service = StubExecutionService()
    market_data = StubMarketData()
    metrics = StubSystemMetrics()

    def refresh_metrics() -> None:
        _refresh_metrics_state(portfolio, execution_service, metrics)

    _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=now,
        last_portfolio_sync=now,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    snapshot = metrics.snapshot()
    assert snapshot["drift_detected"] is True
    assert snapshot["recent_errors"][0]["message"].startswith("Portfolio drift detected")
    assert strategy_engine.kill_switch_calls == [True]


def test_run_loop_iteration_skips_strategy_when_market_data_unhealthy():
    now = datetime(2024, 3, 1, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 10

    portfolio = StubPortfolioService()
    strategy_engine = StubStrategyEngine()
    execution_service = StubExecutionService()
    market_data = StubMarketData(
        MarketDataStatus(health="stale", max_staleness=120.0, reason="data_stale")
    )
    metrics = StubSystemMetrics()

    def refresh_metrics() -> None:
        _refresh_metrics_state(portfolio, execution_service, metrics)

    last_portfolio_sync = now
    last_strategy_cycle = now - timedelta(seconds=strategy_interval)

    updated_portfolio_sync, updated_strategy_cycle = _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    assert updated_strategy_cycle == last_strategy_cycle
    assert updated_portfolio_sync == last_portfolio_sync
    assert strategy_engine.calls == []
    assert execution_service.plans == []
    assert metrics.market_data_errors == 1
    recent_errors = metrics.snapshot()["recent_errors"]
    assert recent_errors[0]["message"].startswith("Market data unavailable")
    assert metrics.state_updates[-1]["equity"] == 1200.0


def test_run_loop_iteration_handles_unavailable_market_data_with_logging_and_metrics(caplog):
    now = datetime(2024, 3, 5, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 10

    stale_status = MarketDataStatus(health="unavailable", max_staleness=45.0, reason="rest_down")
    market_data = MagicMock()
    market_data.get_health_status.return_value = stale_status

    portfolio = MagicMock()
    portfolio.sync.return_value = None
    portfolio.get_drift_status.return_value = DriftStatus(
        drift_flag=False,
        expected_position_value_base=0.0,
        actual_balance_value_base=0.0,
        tolerance_base=0.0,
        mismatched_assets=[],
    )
    portfolio_equity = SimpleNamespace(
        equity_base=900.0,
        realized_pnl_base_total=5.0,
        unrealized_pnl_base_total=10.0,
    )
    portfolio.get_equity.return_value = portfolio_equity
    portfolio.get_positions.return_value = ["ETH/USD"]

    strategy_engine = MagicMock()
    execution_service = MagicMock()
    execution_service.get_open_orders.return_value = []

    metrics = FakeMetrics()

    def refresh_metrics_state() -> None:
        metrics.update_portfolio_state(
            equity_usd=portfolio_equity.equity_base,
            realized_pnl_usd=portfolio_equity.realized_pnl_base_total,
            unrealized_pnl_usd=portfolio_equity.unrealized_pnl_base_total,
            open_orders_count=len(execution_service.get_open_orders()),
            open_positions_count=len(portfolio.get_positions()),
        )

    last_portfolio_sync = now
    last_strategy_cycle = now - timedelta(seconds=strategy_interval)

    with caplog.at_level(logging.WARNING):
        updated_portfolio_sync, updated_strategy_cycle = _run_loop_iteration(
            now=now,
            strategy_interval=strategy_interval,
            portfolio_interval=portfolio_interval,
            last_strategy_cycle=last_strategy_cycle,
            last_portfolio_sync=last_portfolio_sync,
            portfolio=portfolio,
            market_data=market_data,
            strategy_engine=strategy_engine,
            execution_service=execution_service,
            metrics=metrics,
            refresh_metrics_state=refresh_metrics_state,
        )

    assert updated_strategy_cycle == last_strategy_cycle
    assert updated_portfolio_sync == last_portfolio_sync
    strategy_engine.run_cycle.assert_not_called()
    execution_service.execute_plan.assert_not_called()

    warning_records = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any(getattr(record, "event", None) == "market_data_unavailable" for record in warning_records)
    assert any(getattr(record, "reason", None) == "rest_down" for record in warning_records)

    assert metrics.market_data_errors == 1
    assert metrics.market_data_ok is False
    assert metrics.market_data_error_messages[0].startswith("Market data unavailable (rest_down)")
    assert metrics.last_equity_usd == 900.0


def test_run_loop_iteration_skips_strategy_cycle_and_refreshes_metrics_when_stale_market_data():
    now = datetime(2024, 3, 6, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 10

    portfolio = StubPortfolioService()
    strategy_engine = StubStrategyEngine()
    execution_service = StubExecutionService()
    market_data = StubMarketData(
        MarketDataStatus(health="stale", max_staleness=30.0, reason="late_ticks")
    )
    metrics = FakeMetrics()
    refresh_called: list[bool] = []

    def refresh_metrics_state() -> None:
        refresh_called.append(True)
        _refresh_metrics_state(portfolio, execution_service, metrics)

    last_portfolio_sync = now
    last_strategy_cycle = now - timedelta(seconds=strategy_interval)

    updated_portfolio_sync, updated_strategy_cycle = _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=last_strategy_cycle,
        last_portfolio_sync=last_portfolio_sync,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics_state,
    )

    assert updated_strategy_cycle == last_strategy_cycle
    assert updated_portfolio_sync == last_portfolio_sync
    assert refresh_called and refresh_called[-1] is True
    assert metrics.market_data_ok is False
    assert metrics.market_data_stale is True
    assert metrics.market_data_error_messages[0].startswith("Market data unavailable (late_ticks)")
    assert metrics.market_data_errors == 1


class DriftAwarePortfolio:
    def __init__(self, drift_status: DriftStatus) -> None:
        self.drift_status = drift_status
        self.equity = SimpleNamespace(
            equity_base=750.0,
            realized_pnl_base_total=2.0,
            unrealized_pnl_base_total=3.0,
        )
        self.positions = ["SOL/USD"]

    def sync(self) -> None:
        ...

    def get_drift_status(self) -> DriftStatus:
        return self.drift_status

    def get_equity(self):
        return self.equity

    def get_positions(self):
        return list(self.positions)


class DriftAwareStrategyEngine:
    def __init__(self, *, kill_switch_on_drift: bool) -> None:
        risk_cfg = type("risk", (), {"kill_switch_on_drift": kill_switch_on_drift})()
        self.config = type("cfg", (), {"risk": risk_cfg})()
        self.kill_switch_calls: list[bool] = []
        self.kill_switch_active = False
        self.run_cycle_calls: list[datetime] = []

    def set_manual_kill_switch(self, active: bool) -> None:
        self.kill_switch_calls.append(active)
        self.kill_switch_active = active

    def run_cycle(self, now: datetime):
        self.run_cycle_calls.append(now)
        action = SimpleNamespace(blocked=self.kill_switch_active)
        return SimpleNamespace(plan_id="plan-from-strategy", actions=[action])


class DriftAwareExecutionService:
    def __init__(self, strategy_engine: DriftAwareStrategyEngine) -> None:
        self.strategy_engine = strategy_engine
        self.calls: list[str] = []
        self.plans: list[object] = []

    def execute_plan(self, plan):
        self.plans.append(plan)
        if self.strategy_engine.kill_switch_active:
            self.calls.append("blocked")
            blocked_order = SimpleNamespace(
                status="rejected", last_error="Execution blocked by kill switch (kill_switch_active)"
            )
            return SimpleNamespace(errors=["Execution blocked by kill switch"], orders=[blocked_order])
        self.calls.append("executed")
        return SimpleNamespace(errors=[], orders=[])

    def cancel_all(self) -> None:
        ...

    def get_open_orders(self):
        return []


def test_run_loop_iteration_triggers_kill_switch_on_drift_and_blocks_execution():
    now = datetime(2024, 3, 10, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 30

    drift_status = DriftStatus(
        drift_flag=True,
        expected_position_value_base=1500.0,
        actual_balance_value_base=900.0,
        tolerance_base=25.0,
        mismatched_assets=[],
    )
    portfolio = DriftAwarePortfolio(drift_status)
    strategy_engine = DriftAwareStrategyEngine(kill_switch_on_drift=True)
    execution_service = DriftAwareExecutionService(strategy_engine)
    market_data = StubMarketData()
    metrics = FakeMetrics()

    def refresh_metrics() -> None:
        _refresh_metrics_state(portfolio, execution_service, metrics)

    _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=now - timedelta(seconds=strategy_interval),
        last_portfolio_sync=now,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    assert strategy_engine.kill_switch_calls == [True]
    assert metrics.drift_records[0][0] is True
    assert metrics.drift_records[0][1] and metrics.drift_records[0][1].startswith("Portfolio drift detected")
    assert metrics.blocked_actions >= 1
    assert execution_service.calls == ["blocked"]


def test_run_loop_iteration_records_drift_without_triggering_kill_switch_when_disabled():
    now = datetime(2024, 3, 11, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 30

    drift_status = DriftStatus(
        drift_flag=True,
        expected_position_value_base=2500.0,
        actual_balance_value_base=2000.0,
        tolerance_base=50.0,
        mismatched_assets=[],
    )
    portfolio = DriftAwarePortfolio(drift_status)
    strategy_engine = DriftAwareStrategyEngine(kill_switch_on_drift=False)
    execution_service = DriftAwareExecutionService(strategy_engine)
    market_data = StubMarketData()
    metrics = FakeMetrics()

    def refresh_metrics() -> None:
        _refresh_metrics_state(portfolio, execution_service, metrics)

    _run_loop_iteration(
        now=now,
        strategy_interval=strategy_interval,
        portfolio_interval=portfolio_interval,
        last_strategy_cycle=now - timedelta(seconds=strategy_interval),
        last_portfolio_sync=now,
        portfolio=portfolio,
        market_data=market_data,
        strategy_engine=strategy_engine,
        execution_service=execution_service,
        metrics=metrics,
        refresh_metrics_state=refresh_metrics,
    )

    assert strategy_engine.kill_switch_calls == []
    assert metrics.drift_records[0][0] is True
    assert metrics.drift_records[0][1] and metrics.drift_records[0][1].startswith("Portfolio drift detected")
    assert execution_service.calls == ["executed"]


def test_system_metrics_snapshot_contains_all_recent_updates():
    metrics = SystemMetrics()

    metrics.record_plan(blocked_actions=2)
    metrics.record_plan_execution(["execution failed"])
    metrics.record_blocked_actions(3)
    metrics.record_error("unexpected failure")
    metrics.record_market_data_error("market data down")
    metrics.update_portfolio_state(
        equity_usd=1500.0,
        realized_pnl_usd=10.0,
        unrealized_pnl_usd=20.0,
        open_orders_count=4,
        open_positions_count=2,
    )
    metrics.record_drift(True, "drift detected")
    metrics.update_market_data_status(ok=False, stale=True, reason="stale", max_staleness=12.5)

    snapshot = metrics.snapshot()

    assert snapshot["plans_generated"] == 1
    assert snapshot["plans_executed"] == 1
    assert snapshot["blocked_actions"] == 5
    assert snapshot["execution_errors"] == 2
    assert snapshot["market_data_errors"] == 1
    assert snapshot["last_equity_usd"] == 1500.0
    assert snapshot["last_realized_pnl_usd"] == 10.0
    assert snapshot["last_unrealized_pnl_usd"] == 20.0
    assert snapshot["open_orders_count"] == 4
    assert snapshot["open_positions_count"] == 2
    assert snapshot["drift_detected"] is True
    assert snapshot["drift_reason"] == "drift detected"
    assert snapshot["market_data_ok"] is False
    assert snapshot["market_data_stale"] is True
    assert snapshot["market_data_reason"] == "stale"
    assert snapshot["market_data_max_staleness"] == 12.5
    error_messages = [error["message"] for error in snapshot["recent_errors"]]
    assert error_messages[:4] == [
        "drift detected",
        "market data down",
        "unexpected failure",
        "execution failed",
    ]
