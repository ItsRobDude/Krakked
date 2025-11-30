from datetime import datetime, timedelta, timezone

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


def test_run_loop_iteration_executes_scheduled_work_and_updates_metrics():
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    strategy_interval = 1
    portfolio_interval = 2

    portfolio = StubPortfolioService()
    strategy_engine = StubStrategyEngine()
    execution_service = StubExecutionService()
    market_data = StubMarketData()
    metrics = StubSystemMetrics()

    refresh_metrics = lambda: _refresh_metrics_state(portfolio, execution_service, metrics)

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
    assert metrics.state_updates[-1] == {
        "equity": 1200.0,
        "realized": 15.0,
        "unrealized": 25.0,
        "orders": 2,
        "positions": 1,
    }
    assert len(metrics.state_updates) == 5, "metrics should refresh after syncs and strategy cycles"


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

    refresh_metrics = lambda: _refresh_metrics_state(portfolio, execution_service, metrics)

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

    refresh_metrics = lambda: _refresh_metrics_state(portfolio, execution_service, metrics)

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

    refresh_metrics = lambda: _refresh_metrics_state(portfolio, execution_service, metrics)

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
