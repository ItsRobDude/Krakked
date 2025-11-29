from __future__ import annotations

from typing import Any
from types import SimpleNamespace

import pytest

from kraken_bot import cli
from kraken_bot.secrets import CredentialResult, CredentialStatus


class _DummyClient:
    def __init__(self, **_: Any) -> None:
        self.called = False

    def get_private(self, endpoint: str) -> None:  # noqa: ARG002
        self.called = True


def test_setup_runs_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _fake_setup() -> CredentialResult:
        nonlocal called
        called = True
        return CredentialResult("key", "secret", CredentialStatus.LOADED)

    monkeypatch.setattr(cli.secrets, "_interactive_setup", _fake_setup)

    exit_code = cli.main(["setup"])

    assert called is True
    assert exit_code == 0


def test_smoke_test_uses_credentials_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.secrets,
        "load_api_keys",
        lambda allow_interactive_setup=False: CredentialResult(  # noqa: ARG005
            "key",
            "secret",
            CredentialStatus.LOADED,
        ),
    )

    dummy_client = _DummyClient()
    monkeypatch.setattr(cli, "KrakenRESTClient", lambda **kwargs: dummy_client)

    exit_code = cli.main(["smoke-test"])

    assert exit_code == 0
    assert dummy_client.called is True


def test_smoke_test_handles_missing_credentials(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(
        cli.secrets,
        "load_api_keys",
        lambda allow_interactive_setup=False: CredentialResult(  # noqa: ARG005
            None,
            None,
            CredentialStatus.NOT_FOUND,
        ),
    )

    exit_code = cli.main(["smoke-test"])

    captured = capsys.readouterr()
    assert "Credentials not available" in captured.out
    assert exit_code == 1


def test_run_once_forces_paper_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    original_config = SimpleNamespace(
        execution=SimpleNamespace(mode="live", validate_only=False, allow_live_trading=True),
        market_data=SimpleNamespace(backfill_timeframes=["1h"]),
    )
    captured_execution_config: dict[str, Any] = {}

    def fake_bootstrap(*_: Any, **__: Any) -> tuple[object, SimpleNamespace]:
        return object(), original_config

    class _DummyMarketData:
        def __init__(self, config: Any) -> None:
            self.config = config

        def refresh_universe(self) -> None:
            self._universe = ["BTC/USD"]

        def get_universe(self) -> list[str]:
            return ["BTC/USD"]

        def backfill_ohlc(self, pair: str, timeframe: str) -> None:  # noqa: ARG002
            return None

    class _DummyPortfolio:
        def __init__(self, config: Any, market_data: Any) -> None:
            self.config = config
            self.market_data = market_data
            self.rest_client = None

        def initialize(self) -> None:
            return None

    class _DummyPlan:
        plan_id = "plan-1"

    class _DummyStrategyEngine:
        def __init__(self, config: Any, market_data: Any, portfolio: Any) -> None:
            self.config = config
            self.market_data = market_data
            self.portfolio = portfolio

        def initialize(self) -> None:
            return None

        def run_cycle(self) -> _DummyPlan:
            return _DummyPlan()

    class _DummyResult:
        success = True
        errors: list[str] = []

    class _DummyExecutionService:
        def __init__(self, client: Any, config: Any) -> None:  # noqa: ARG002
            captured_execution_config["config"] = config

        def execute_plan(self, plan: Any) -> _DummyResult:  # noqa: ARG002
            return _DummyResult()

    monkeypatch.setattr(cli.run_strategy_once, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(cli.run_strategy_once, "MarketDataAPI", _DummyMarketData)
    monkeypatch.setattr(cli.run_strategy_once, "PortfolioService", _DummyPortfolio)
    monkeypatch.setattr(cli.run_strategy_once, "StrategyEngine", _DummyStrategyEngine)
    monkeypatch.setattr(cli.run_strategy_once, "ExecutionService", _DummyExecutionService)

    exit_code = cli.main(["run-once"])

    assert exit_code == 0
    safe_execution_config = captured_execution_config["config"]
    assert safe_execution_config.mode == "paper"
    assert safe_execution_config.validate_only is True
    assert safe_execution_config.allow_live_trading is False
    assert original_config.execution.mode == "live"
    assert original_config.execution.validate_only is False
    assert original_config.execution.allow_live_trading is True
