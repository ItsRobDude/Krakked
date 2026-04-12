from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from krakked.backtest import runner
from krakked.config import AppConfig, load_config
from krakked.execution.models import ExecutionResult
from krakked.strategy.models import ExecutionPlan


def test_run_backtest_wires_risk_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _DummyMarketData:
        def __init__(
            self,
            config: AppConfig,
            pairs: list[str],
            frames: list[str],
            start: datetime,
            end: datetime,
        ) -> None:  # noqa: ARG002
            self._timeline = [int(start.timestamp())]

        def iter_timestamps(self) -> list[int]:
            return self._timeline

        def set_time(self, now: datetime) -> None:  # noqa: ARG002
            return None

    class _DummyPortfolioService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            self.store = SimpleNamespace()
            self.portfolio = SimpleNamespace(
                ingest_trades=lambda trades, persist=True: None
            )

        def initialize(self) -> None:
            return None

    class _DummyStrategyEngine:
        def __init__(self, config: AppConfig, market_data: Any, portfolio: Any) -> None:
            self.config = config
            self.market_data = market_data
            self.portfolio = portfolio

        def initialize(self) -> None:
            return None

        def get_risk_status(self) -> Any:
            return SimpleNamespace(kill_switch_active=False)

        def run_cycle(
            self, now: datetime | None = None
        ) -> ExecutionPlan:  # noqa: ARG002
            return ExecutionPlan(
                plan_id="plan-1",
                generated_at=datetime.now(UTC),
                actions=[],
            )

    class _DummyExecutionService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            captured["risk_status_provider"] = kwargs.get("risk_status_provider")

        def execute_plan(self, plan: ExecutionPlan) -> ExecutionResult:  # noqa: ARG002
            return ExecutionResult(
                plan_id=plan.plan_id, started_at=datetime.now(UTC), success=True
            )

    monkeypatch.setattr(runner, "BacktestMarketData", _DummyMarketData)
    monkeypatch.setattr(runner, "BacktestPortfolioService", _DummyPortfolioService)
    monkeypatch.setattr(runner, "StrategyEngine", _DummyStrategyEngine)
    monkeypatch.setattr(runner, "ExecutionService", _DummyExecutionService)

    config = load_config()
    config.universe.include_pairs = ["XBT/USD"]

    result = runner.run_backtest(config, start=datetime.now(UTC), end=datetime.now(UTC))

    assert result.plans
    assert result.executions
    assert captured["risk_status_provider"] is not None
