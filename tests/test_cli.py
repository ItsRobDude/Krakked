from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from krakked import cli
from krakked.backtest.runner import (
    BacktestCoverageItem,
    BacktestPreflight,
    BacktestPreflightResult,
    BacktestResult,
    BacktestSummary,
)
from krakked.config import load_config
from krakked.credentials import CredentialResult, CredentialStatus
from krakked.portfolio.exceptions import PortfolioSchemaError
from krakked.portfolio.store import CURRENT_SCHEMA_VERSION


class _DummyClient:
    def __init__(self, **_: Any) -> None:
        self.called = False

    def get_private(self, endpoint: str) -> None:  # noqa: ARG002
        self.called = True


def _seed_schema_version(db_path: str, version: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )
        conn.commit()


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


def test_smoke_test_uses_credentials_and_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_smoke_test_handles_missing_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
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
        execution=SimpleNamespace(
            mode="live", validate_only=False, allow_live_trading=True
        ),
        market_data=SimpleNamespace(backfill_timeframes=["1h"]),
    )
    captured_execution_config: dict[str, Any] = {}

    def fake_bootstrap(*_: Any, **__: Any) -> tuple[object, SimpleNamespace, object]:
        return object(), original_config, object()

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
    monkeypatch.setattr(
        cli.run_strategy_once, "ExecutionService", _DummyExecutionService
    )

    exit_code = cli.main(["run-once"])

    assert exit_code == 0
    safe_execution_config = captured_execution_config["config"]
    assert safe_execution_config.mode == "paper"
    assert safe_execution_config.validate_only is True
    assert safe_execution_config.allow_live_trading is False
    assert original_config.execution.mode == "live"
    assert original_config.execution.validate_only is False
    assert original_config.execution.allow_live_trading is True


def test_run_once_wires_risk_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    original_config = load_config()
    captured: dict[str, Any] = {}

    def fake_bootstrap(*_: Any, **__: Any) -> tuple[object, Any, object]:
        class _DummyClient:
            def __init__(self) -> None:
                self.rest_client = None

        return _DummyClient(), original_config, None

    class _DummyMarketData:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        def refresh_universe(self) -> None:
            return None

        def get_universe(self) -> list[str]:
            return []

        def backfill_ohlc(self, pair: str, timeframe: str) -> None:  # noqa: ARG002
            return None

    class _DummyPortfolio:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            self.store = None
            self.portfolio = None
            self.rate_limiter = None
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

        def get_risk_status(self) -> Any:
            return object()

    class _DummyResult:
        success = True
        errors: list[str] = []

    class _DummyExecutionService:
        def __init__(
            self,
            client: Any,
            config: Any,
            risk_status_provider: Any,
            **_: Any,
        ) -> None:
            captured["risk_status_provider"] = risk_status_provider

        def execute_plan(self, plan: Any) -> _DummyResult:  # noqa: ARG002
            return _DummyResult()

    monkeypatch.setattr(cli.run_strategy_once, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(cli.run_strategy_once, "MarketDataAPI", _DummyMarketData)
    monkeypatch.setattr(cli.run_strategy_once, "PortfolioService", _DummyPortfolio)
    monkeypatch.setattr(cli.run_strategy_once, "StrategyEngine", _DummyStrategyEngine)
    monkeypatch.setattr(
        cli.run_strategy_once, "ExecutionService", _DummyExecutionService
    )

    exit_code = cli.main(["run-once"])

    assert exit_code == 0
    assert captured["risk_status_provider"] is not None


def test_run_subcommand_defaults_to_non_interactive_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_orchestrator(*, allow_interactive_setup: bool) -> int:
        captured["allow_interactive_setup"] = allow_interactive_setup
        return 0

    monkeypatch.setattr(cli, "run_orchestrator", fake_run_orchestrator)

    exit_code = cli.main(["run"])

    assert exit_code == 0
    assert captured["allow_interactive_setup"] is False


def test_migrate_db_subcommand_upgrades_outdated_schema(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "upgrade_cli.db"
    _seed_schema_version(str(db_path), CURRENT_SCHEMA_VERSION - 1)

    exit_code = cli.main(["migrate-db", "--db-path", str(db_path)])

    assert exit_code == 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row is not None
    assert int(row[0]) == CURRENT_SCHEMA_VERSION
    output = capsys.readouterr().out
    assert "Migration completed successfully" in output


def test_db_schema_version_reports_missing_meta(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "missing_meta.db"

    exit_code = cli.main(["db-schema-version", "--db-path", str(db_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Schema version not set" in output


def test_db_schema_version_reports_missing_row(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "missing_row.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()

    exit_code = cli.main(["db-schema-version", "--db-path", str(db_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Schema version not set" in output


def test_migrate_db_subcommand_errors_on_newer_schema(tmp_path, capsys: Any) -> None:
    db_path = tmp_path / "ahead_cli.db"
    _seed_schema_version(str(db_path), CURRENT_SCHEMA_VERSION + 1)

    exit_code = cli.main(["migrate-db", "--db-path", str(db_path)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "incompatible" in output


def test_migrate_db_subcommand_handles_migration_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: Any
) -> None:
    db_path = tmp_path / "migration_failure.db"
    _seed_schema_version(str(db_path), CURRENT_SCHEMA_VERSION - 1)

    def _failing_migrate(db_path: str) -> None:  # noqa: ARG001
        raise PortfolioSchemaError(
            found=CURRENT_SCHEMA_VERSION - 1,
            expected=CURRENT_SCHEMA_VERSION,
        )

    monkeypatch.setattr(cli, "run_migrate_db", _failing_migrate)

    exit_code = cli.main(["migrate-db", "--db-path", str(db_path)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Migration failed" in output


def test_backtest_subcommand_prints_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_backtest(
        config: Any,
        start: Any,
        end: Any,
        timeframes: Any = None,
        *,
        starting_cash_usd: float,
        fee_bps: float,
        db_path: str | None = None,
        strict_data: bool = False,
    ) -> BacktestResult:
        captured["config"] = config
        captured["start"] = start
        captured["end"] = end
        captured["timeframes"] = timeframes
        captured["starting_cash_usd"] = starting_cash_usd
        captured["fee_bps"] = fee_bps
        captured["db_path"] = db_path
        captured["strict_data"] = strict_data
        return BacktestResult(
            plans=[],
            executions=[],
            preflight=BacktestPreflight(
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1h",
                        bar_count=24,
                        first_bar_at=start,
                        last_bar_at=end,
                        status="ok",
                    ),
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="4h",
                        bar_count=0,
                        first_bar_at=None,
                        last_bar_at=None,
                        status="missing",
                    ),
                ],
                usable_series_count=1,
                missing_series=["BTC/USD@4h"],
                partial_series=[],
            ),
            summary=BacktestSummary(
                start=start,
                end=end,
                starting_cash_usd=starting_cash_usd,
                ending_equity_usd=10_250.0,
                absolute_pnl_usd=250.0,
                return_pct=2.5,
                max_drawdown_pct=1.25,
                realized_pnl_usd=100.0,
                unrealized_pnl_usd=150.0,
                pairs=["BTC/USD"],
                timeframes=["1h"],
                total_cycles=12,
                total_actions=4,
                blocked_actions=1,
                total_orders=3,
                filled_orders=2,
                rejected_orders=1,
                execution_errors=0,
                fee_bps=25.0,
                slippage_bps=50.0,
                cost_model="Immediate candle-close fills using configured slippage and flat taker fees.",
                usable_series_count=1,
                missing_series=["BTC/USD@4h"],
                partial_series=[],
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1h",
                        bar_count=24,
                        first_bar_at=start,
                        last_bar_at=end,
                        status="ok",
                    )
                ],
                per_strategy={
                    "majors_mean_rev": {
                        "realized_pnl_usd": 100.0,
                        "trade_count": 2,
                        "winning_trades": 1,
                        "losing_trades": 1,
                    }
                },
                replay_inputs={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD"],
                    "timeframes": ["1h"],
                    "enabled_strategies": ["majors_mean_rev"],
                    "starting_cash_usd": starting_cash_usd,
                    "fee_bps": fee_bps,
                    "slippage_bps": 50.0,
                    "strict_data": strict_data,
                },
                trust_level="limited",
                trust_note="Limited signal: some strategy actions were blocked by guardrails.",
                notable_warnings=["Most strategy actions were blocked by guardrails."],
                blocked_reason_counts={"Max open positions reached (1)": 1},
                assumptions=["Synthetic fills only."],
            ),
        )

    monkeypatch.setattr(cli, "run_backtest", _fake_run_backtest)

    exit_code = cli.main(
        [
            "backtest",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
            "--pair",
            "BTC/USD",
            "--timeframe",
            "1h",
            "--starting-cash-usd",
            "10000",
        ]
    )

    assert exit_code == 0
    assert captured["starting_cash_usd"] == 10_000.0
    assert captured["fee_bps"] == 25.0
    assert captured["timeframes"] == ["1h"]
    output = capsys.readouterr().out
    assert "Backtest completed." in output
    assert "Wallet: start $10,000.00 -> end $10,250.00 (+250.00, +2.50%)" in output
    assert "Replay trust: Limited signal: some strategy actions were blocked by guardrails." in output
    assert "Cost model: 50 bps slippage + 25.00 bps taker fee" in output
    assert "Missing OHLC series:" in output
    assert "Top blocked reason: Max open positions reached (1) (1)" in output


def test_backtest_subcommand_save_report_writes_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)

    def _fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:  # noqa: ARG001
        return BacktestResult(
            plans=[],
            executions=[],
            preflight=BacktestPreflight(
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1h",
                        bar_count=24,
                        first_bar_at=start,
                        last_bar_at=end,
                        status="ok",
                    )
                ],
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
            ),
            summary=BacktestSummary(
                start=start,
                end=end,
                starting_cash_usd=10_000.0,
                ending_equity_usd=10_100.0,
                absolute_pnl_usd=100.0,
                return_pct=1.0,
                max_drawdown_pct=0.5,
                realized_pnl_usd=60.0,
                unrealized_pnl_usd=40.0,
                pairs=["BTC/USD"],
                timeframes=["1h"],
                total_cycles=24,
                total_actions=3,
                blocked_actions=1,
                total_orders=2,
                filled_orders=1,
                rejected_orders=1,
                execution_errors=0,
                fee_bps=25.0,
                slippage_bps=50.0,
                cost_model="Immediate candle-close fills using configured slippage and flat taker fees.",
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1h",
                        bar_count=24,
                        first_bar_at=start,
                        last_bar_at=end,
                        status="ok",
                    )
                ],
                per_strategy={
                    "majors_mean_rev": {
                        "realized_pnl_usd": 60.0,
                        "trade_count": 1,
                        "winning_trades": 1,
                        "losing_trades": 0,
                    }
                },
                replay_inputs={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD"],
                    "timeframes": ["1h"],
                    "enabled_strategies": ["majors_mean_rev"],
                    "starting_cash_usd": 10_000.0,
                    "fee_bps": 25.0,
                    "slippage_bps": 50.0,
                    "strict_data": False,
                },
                trust_level="decision_helpful",
                trust_note="Decision-helpful: coverage was complete and the replay produced filled trades.",
                notable_warnings=[],
                blocked_reason_counts={},
                assumptions=["Synthetic fills only."],
            ),
        )

    monkeypatch.setattr(cli, "run_backtest", _fake_run_backtest)

    report_path = tmp_path / "report.json"
    exit_code = cli.main(
        [
            "backtest",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
            "--save-report",
            str(report_path),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["report_version"] == 1
    assert payload["summary"]["ending_equity_usd"] == pytest.approx(10_100.0)
    assert payload["summary"]["replay_inputs"]["config_path"] is None
    assert payload["preflight"]["usable_series_count"] == 1
    assert payload["provenance"]["app_version"] == cli.APP_VERSION


def test_backtest_subcommand_publish_latest_is_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)

    def _fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:  # noqa: ARG001
        return BacktestResult(
            plans=[],
            executions=[],
            preflight=BacktestPreflight(
                coverage=[],
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                status="ready",
                summary_note="Coverage looks complete for the requested replay window.",
                warnings=[],
            ),
            summary=BacktestSummary(
                start=start,
                end=end,
                starting_cash_usd=10_000.0,
                ending_equity_usd=10_100.0,
                absolute_pnl_usd=100.0,
                return_pct=1.0,
                max_drawdown_pct=0.5,
                realized_pnl_usd=60.0,
                unrealized_pnl_usd=40.0,
                pairs=["BTC/USD"],
                timeframes=["1h"],
                total_cycles=24,
                total_actions=3,
                blocked_actions=0,
                total_orders=2,
                filled_orders=1,
                rejected_orders=0,
                execution_errors=0,
                fee_bps=25.0,
                slippage_bps=50.0,
                cost_model="Immediate candle-close fills using configured slippage and flat taker fees.",
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                coverage=[],
                per_strategy={},
                replay_inputs={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD"],
                    "timeframes": ["1h"],
                    "enabled_strategies": ["majors_mean_rev"],
                    "starting_cash_usd": 10_000.0,
                    "fee_bps": 25.0,
                    "slippage_bps": 50.0,
                    "strict_data": False,
                },
                trust_level="decision_helpful",
                trust_note="Decision-helpful: coverage was complete and the replay produced filled trades.",
                notable_warnings=[],
                blocked_reason_counts={},
                assumptions=["Synthetic fills only."],
            ),
        )

    monkeypatch.setattr(cli, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "get_config_dir", lambda: tmp_path)

    latest_path = tmp_path / "reports" / "backtests" / "latest.json"

    exit_code = cli.main(
        [
            "backtest",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 0
    assert not latest_path.exists()

    exit_code = cli.main(
        [
            "backtest",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
            "--publish-latest",
        ]
    )

    assert exit_code == 0
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    assert payload["report_version"] == 1
    assert payload["summary"]["ending_equity_usd"] == pytest.approx(10_100.0)


def test_backtest_subcommand_can_save_and_publish_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)

    def _fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:  # noqa: ARG001
        return BacktestResult(
            plans=[],
            executions=[],
            preflight=BacktestPreflight(
                coverage=[],
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                status="ready",
                summary_note="Coverage looks complete for the requested replay window.",
                warnings=[],
            ),
            summary=BacktestSummary(
                start=start,
                end=end,
                starting_cash_usd=10_000.0,
                ending_equity_usd=10_050.0,
                absolute_pnl_usd=50.0,
                return_pct=0.5,
                max_drawdown_pct=0.3,
                realized_pnl_usd=35.0,
                unrealized_pnl_usd=15.0,
                pairs=["BTC/USD"],
                timeframes=["1h"],
                total_cycles=24,
                total_actions=2,
                blocked_actions=0,
                total_orders=2,
                filled_orders=2,
                rejected_orders=0,
                execution_errors=0,
                fee_bps=25.0,
                slippage_bps=50.0,
                cost_model="Immediate candle-close fills using configured slippage and flat taker fees.",
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                coverage=[],
                per_strategy={},
                replay_inputs={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD"],
                    "timeframes": ["1h"],
                    "enabled_strategies": ["majors_mean_rev"],
                    "starting_cash_usd": 10_000.0,
                    "fee_bps": 25.0,
                    "slippage_bps": 50.0,
                    "strict_data": False,
                },
                trust_level="decision_helpful",
                trust_note="Decision-helpful: coverage was complete and the replay produced filled trades.",
                notable_warnings=[],
                blocked_reason_counts={},
                assumptions=["Synthetic fills only."],
            ),
        )

    monkeypatch.setattr(cli, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "get_config_dir", lambda: tmp_path)

    report_path = tmp_path / "custom-report.json"
    latest_path = tmp_path / "reports" / "backtests" / "latest.json"

    exit_code = cli.main(
        [
            "backtest",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
            "--save-report",
            str(report_path),
            "--publish-latest",
            "--json",
        ]
    )

    assert exit_code == 0
    assert report_path.exists()
    assert latest_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8")) == json.loads(
        latest_path.read_text(encoding="utf-8")
    )


def test_backtest_preflight_command_prints_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)

    def _fake_build_backtest_preflight(*args: Any, **kwargs: Any) -> BacktestPreflightResult:  # noqa: ARG001
        return BacktestPreflightResult(
            start=start,
            end=end,
            pairs=["BTC/USD"],
            timeframes=["1h"],
            preflight=BacktestPreflight(
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1h",
                        bar_count=24,
                        first_bar_at=start,
                        last_bar_at=end,
                        status="ok",
                    )
                ],
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                status="ready",
                summary_note="Coverage looks complete for the requested replay window.",
                warnings=[],
            ),
        )

    monkeypatch.setattr(cli, "build_backtest_preflight", _fake_build_backtest_preflight)

    exit_code = cli.main(
        [
            "backtest-preflight",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Backtest preflight" in output
    assert "Coverage status: ready" in output
    assert "Replay readiness: Coverage looks complete" in output


def test_refresh_ohlc_command_prints_summary_and_checks_readiness(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.universe.include_pairs = ["BTC/USD"]
    config.market_data.backfill_timeframes = ["1h"]

    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)
    latest_before = int(datetime(2026, 4, 1, 0, 0, tzinfo=UTC).timestamp())
    latest_after = int(datetime(2026, 4, 2, 0, 0, tzinfo=UTC).timestamp())

    class _FakeQueue:
        def join(self) -> None:
            return None

    class _FakeMarketData:
        def __init__(self, cfg: Any) -> None:
            self.config = cfg
            self._latest = {("BTC/USD", "1h"): latest_before}
            self._ohlc_store = SimpleNamespace(_write_queue=_FakeQueue())

        def refresh_universe(self) -> None:
            return None

        def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> list[Any]:  # noqa: ARG002
            timestamp = self._latest[(pair, timeframe)]
            return [SimpleNamespace(timestamp=timestamp)]

        def backfill_ohlc(self, pair: str, timeframe: str, since: int | None = None) -> int:
            assert since == latest_before
            self._latest[(pair, timeframe)] = latest_after
            return 24

        def shutdown(self) -> None:
            return None

    def _fake_build_backtest_preflight(*args: Any, **kwargs: Any) -> BacktestPreflightResult:  # noqa: ARG001
        return BacktestPreflightResult(
            start=start,
            end=end,
            pairs=["BTC/USD"],
            timeframes=["1h"],
            preflight=BacktestPreflight(
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1h",
                        bar_count=24,
                        first_bar_at=start,
                        last_bar_at=end,
                        status="ok",
                    )
                ],
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                status="ready",
                summary_note="Coverage looks complete for the requested replay window.",
                warnings=[],
            ),
        )

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)  # noqa: ARG005
    monkeypatch.setattr(cli, "MarketDataAPI", _FakeMarketData)
    monkeypatch.setattr(cli, "build_backtest_preflight", _fake_build_backtest_preflight)

    exit_code = cli.main(
        [
            "refresh-ohlc",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "OHLC refresh completed." in output
    assert "BTC/USD@1h: fetched 24 bars" in output
    assert "Refresh readiness: ready" in output
    assert "Replay readiness: Coverage looks complete" in output


def test_refresh_ohlc_command_fails_when_window_stays_incomplete(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.universe.include_pairs = ["BTC/USD"]
    config.market_data.backfill_timeframes = ["1d"]

    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)
    latest_before = int(datetime(2026, 4, 1, 0, 0, tzinfo=UTC).timestamp())

    class _FakeQueue:
        def join(self) -> None:
            return None

    class _FakeMarketData:
        def __init__(self, cfg: Any) -> None:
            self.config = cfg
            self._ohlc_store = SimpleNamespace(_write_queue=_FakeQueue())

        def refresh_universe(self) -> None:
            return None

        def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> list[Any]:  # noqa: ARG002
            return [SimpleNamespace(timestamp=latest_before)]

        def backfill_ohlc(self, pair: str, timeframe: str, since: int | None = None) -> int:  # noqa: ARG002
            return 0

        def shutdown(self) -> None:
            return None

    def _fake_build_backtest_preflight(*args: Any, **kwargs: Any) -> BacktestPreflightResult:  # noqa: ARG001
        return BacktestPreflightResult(
            start=start,
            end=end,
            pairs=["BTC/USD"],
            timeframes=["1d"],
            preflight=BacktestPreflight(
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1d",
                        bar_count=1,
                        first_bar_at=start,
                        last_bar_at=start,
                        status="partial_window",
                    )
                ],
                usable_series_count=1,
                missing_series=[],
                partial_series=["BTC/USD@1d"],
                status="limited",
                summary_note="Coverage is limited: some requested series are missing or partial.",
                warnings=["1 requested series only partially cover the requested window."],
            ),
        )

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)  # noqa: ARG005
    monkeypatch.setattr(cli, "MarketDataAPI", _FakeMarketData)
    monkeypatch.setattr(cli, "build_backtest_preflight", _fake_build_backtest_preflight)

    exit_code = cli.main(
        [
            "refresh-ohlc",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Refresh readiness: limited" in output
    assert "OHLC refresh left replay coverage incomplete" in output


def test_refresh_ohlc_command_marks_unchanged_latest_timestamp(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.universe.include_pairs = ["BTC/USD"]
    config.market_data.backfill_timeframes = ["1d"]

    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)
    latest_before = int(datetime(2026, 4, 1, 0, 0, tzinfo=UTC).timestamp())

    class _FakeQueue:
        def join(self) -> None:
            return None

    class _FakeMarketData:
        def __init__(self, cfg: Any) -> None:
            self.config = cfg
            self._ohlc_store = SimpleNamespace(_write_queue=_FakeQueue())

        def refresh_universe(self) -> None:
            return None

        def get_ohlc(self, pair: str, timeframe: str, lookback: int) -> list[Any]:  # noqa: ARG002
            return [SimpleNamespace(timestamp=latest_before)]

        def backfill_ohlc(self, pair: str, timeframe: str, since: int | None = None) -> int:  # noqa: ARG002
            return 1

        def shutdown(self) -> None:
            return None

    def _fake_build_backtest_preflight(*args: Any, **kwargs: Any) -> BacktestPreflightResult:  # noqa: ARG001
        return BacktestPreflightResult(
            start=start,
            end=end,
            pairs=["BTC/USD"],
            timeframes=["1d"],
            preflight=BacktestPreflight(
                coverage=[
                    BacktestCoverageItem(
                        pair="BTC/USD",
                        timeframe="1d",
                        bar_count=2,
                        first_bar_at=start,
                        last_bar_at=end,
                        status="ok",
                    )
                ],
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                status="ready",
                summary_note="Coverage looks complete for the requested replay window.",
                warnings=[],
            ),
        )

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)  # noqa: ARG005
    monkeypatch.setattr(cli, "MarketDataAPI", _FakeMarketData)
    monkeypatch.setattr(cli, "build_backtest_preflight", _fake_build_backtest_preflight)

    exit_code = cli.main(
        [
            "refresh-ohlc",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "(latest unchanged)" in output


def test_replay_ready_command_prints_refresh_and_preflight(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)
    preflight = BacktestPreflightResult(
        start=start,
        end=end,
        pairs=["BTC/USD"],
        timeframes=["1h"],
        preflight=BacktestPreflight(
            coverage=[
                BacktestCoverageItem(
                    pair="BTC/USD",
                    timeframe="1h",
                    bar_count=24,
                    first_bar_at=start,
                    last_bar_at=end,
                    status="ok",
                )
            ],
            usable_series_count=1,
            missing_series=[],
            partial_series=[],
            status="ready",
            summary_note="Coverage looks complete for the requested replay window.",
            warnings=[],
        ),
    )
    payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": ["BTC/USD"],
        "timeframes": ["1h"],
        "refreshed": [
            {
                "pair": "BTC/USD",
                "timeframe": "1h",
                "series_key": "BTC/USD@1h",
                "fetched_bars": 24,
                "previous_latest_ts": int(start.timestamp()),
                "previous_latest_at": start.isoformat(),
                "latest_at": end.isoformat(),
                "latest_changed": True,
            }
        ],
        "preflight": preflight.to_dict(),
    }

    monkeypatch.setattr(cli, "_refresh_ohlc_window", lambda args: (payload, preflight))  # noqa: ARG005

    exit_code = cli.main(
        [
            "replay-ready",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "OHLC refresh completed." in output
    assert "Backtest preflight" in output
    assert "Coverage status: ready" in output


def test_replay_run_command_runs_and_publishes_after_clean_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)
    preflight = BacktestPreflightResult(
        start=start,
        end=end,
        pairs=["BTC/USD"],
        timeframes=["1h"],
        preflight=BacktestPreflight(
            coverage=[
                BacktestCoverageItem(
                    pair="BTC/USD",
                    timeframe="1h",
                    bar_count=24,
                    first_bar_at=start,
                    last_bar_at=end,
                    status="ok",
                )
            ],
            usable_series_count=1,
            missing_series=[],
            partial_series=[],
            status="ready",
            summary_note="Coverage looks complete for the requested replay window.",
            warnings=[],
        ),
    )
    refresh_payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": ["BTC/USD"],
        "timeframes": ["1h"],
        "refreshed": [
            {
                "pair": "BTC/USD",
                "timeframe": "1h",
                "series_key": "BTC/USD@1h",
                "fetched_bars": 24,
                "previous_latest_ts": int(start.timestamp()),
                "previous_latest_at": start.isoformat(),
                "latest_at": end.isoformat(),
                "latest_changed": True,
            }
        ],
        "preflight": preflight.to_dict(),
    }
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    config.universe.include_pairs = ["BTC/USD"]
    config.market_data.backfill_timeframes = ["1h"]
    captured: dict[str, Any] = {}

    def _fake_run_backtest(
        loaded_config: Any,
        start: Any,
        end: Any,
        timeframes: Any = None,
        *,
        starting_cash_usd: float,
        fee_bps: float,
        db_path: str | None = None,
        strict_data: bool = False,
    ) -> BacktestResult:
        captured["config"] = loaded_config
        captured["start"] = start
        captured["end"] = end
        captured["timeframes"] = timeframes
        captured["starting_cash_usd"] = starting_cash_usd
        captured["fee_bps"] = fee_bps
        captured["db_path"] = db_path
        captured["strict_data"] = strict_data
        return BacktestResult(
            plans=[],
            executions=[],
            preflight=preflight.preflight,
            summary=BacktestSummary(
                start=start,
                end=end,
                starting_cash_usd=starting_cash_usd,
                ending_equity_usd=10_100.0,
                absolute_pnl_usd=100.0,
                return_pct=1.0,
                max_drawdown_pct=0.5,
                realized_pnl_usd=100.0,
                unrealized_pnl_usd=0.0,
                pairs=["BTC/USD"],
                timeframes=["1h"],
                total_cycles=24,
                total_actions=2,
                blocked_actions=0,
                total_orders=2,
                filled_orders=2,
                rejected_orders=0,
                execution_errors=0,
                fee_bps=fee_bps,
                slippage_bps=50.0,
                cost_model="Immediate candle-close fills using configured slippage and flat taker fees.",
                usable_series_count=1,
                missing_series=[],
                partial_series=[],
                coverage=preflight.preflight.coverage,
                per_strategy={},
                replay_inputs={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD"],
                    "timeframes": ["1h"],
                    "enabled_strategies": ["majors_mean_rev"],
                    "starting_cash_usd": starting_cash_usd,
                    "fee_bps": fee_bps,
                    "slippage_bps": 50.0,
                    "strict_data": strict_data,
                },
                trust_level="decision_helpful",
                trust_note="Decision-helpful: coverage was complete and the replay produced filled trades.",
                notable_warnings=[],
                blocked_reason_counts={},
                assumptions=["Synthetic fills only."],
            ),
        )

    monkeypatch.setattr(cli, "_refresh_ohlc_window", lambda args: (refresh_payload, preflight))  # noqa: ARG005
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)  # noqa: ARG005
    monkeypatch.setattr(cli, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "get_config_dir", lambda: tmp_path)

    exit_code = cli.main(
        [
            "replay-run",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 0
    assert captured["timeframes"] == ["1h"]
    assert captured["strict_data"] is False
    latest_path = tmp_path / "reports" / "backtests" / "latest.json"
    assert latest_path.exists()
    output = capsys.readouterr().out
    assert "OHLC refresh completed." in output
    assert "Backtest preflight" in output
    assert "Backtest completed." in output
    assert "Replay trust: Decision-helpful: coverage was complete and the replay produced filled trades." in output


def test_replay_run_command_aborts_before_backtest_when_readiness_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)
    preflight = BacktestPreflightResult(
        start=start,
        end=end,
        pairs=["BTC/USD"],
        timeframes=["1d"],
        preflight=BacktestPreflight(
            coverage=[
                BacktestCoverageItem(
                    pair="BTC/USD",
                    timeframe="1d",
                    bar_count=1,
                    first_bar_at=start,
                    last_bar_at=start,
                    status="partial_window",
                )
            ],
            usable_series_count=1,
            missing_series=[],
            partial_series=["BTC/USD@1d"],
            status="limited",
            summary_note="Coverage is limited: some requested series are missing or partial.",
            warnings=["1 requested series only partially cover the requested window."],
        ),
    )
    refresh_payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": ["BTC/USD"],
        "timeframes": ["1d"],
        "refreshed": [
            {
                "pair": "BTC/USD",
                "timeframe": "1d",
                "series_key": "BTC/USD@1d",
                "fetched_bars": 0,
                "previous_latest_ts": int(start.timestamp()),
                "previous_latest_at": start.isoformat(),
                "latest_at": start.isoformat(),
                "latest_changed": False,
            }
        ],
        "preflight": preflight.to_dict(),
    }
    called = False

    def _should_not_run(*args: Any, **kwargs: Any) -> BacktestResult:  # noqa: ARG001
        nonlocal called
        called = True
        raise AssertionError("run_backtest should not be called")

    monkeypatch.setattr(cli, "_refresh_ohlc_window", lambda args: (refresh_payload, preflight))  # noqa: ARG005
    monkeypatch.setattr(cli, "run_backtest", _should_not_run)

    exit_code = cli.main(
        [
            "replay-run",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
        ]
    )

    assert exit_code == 1
    assert called is False
    output = capsys.readouterr().out
    assert "Backtest preflight" in output
    assert "Replay-run aborted: replay coverage is still incomplete" in output


def test_compare_backtests_prints_deltas(tmp_path: Path, capsys: Any) -> None:
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(
        json.dumps(
            {
                "report_version": 1,
                "summary": {
                    "ending_equity_usd": 10_000.0,
                    "return_pct": 0.0,
                    "max_drawdown_pct": 5.0,
                    "filled_orders": 2,
                    "blocked_actions": 1,
                    "execution_errors": 0,
                    "replay_inputs": {},
                    "per_strategy": {
                        "majors_mean_rev": {"realized_pnl_usd": 25.0}
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    candidate_path.write_text(
        json.dumps(
            {
                "report_version": 1,
                "summary": {
                    "ending_equity_usd": 10_120.0,
                    "return_pct": 1.2,
                    "max_drawdown_pct": 4.5,
                    "filled_orders": 3,
                    "blocked_actions": 0,
                    "execution_errors": 0,
                    "replay_inputs": {},
                    "per_strategy": {
                        "majors_mean_rev": {"realized_pnl_usd": 55.0}
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "compare-backtests",
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Backtest comparison" in output
    assert "Ending equity USD: 10,000.00 -> 10,120.00 (+120.00)" in output
    assert "Per-strategy realized PnL delta:" in output
    assert "majors_mean_rev: 25.00 -> 55.00 (+30.00)" in output


def test_compare_backtests_rejects_invalid_report_version(
    tmp_path: Path, capsys: Any
) -> None:
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(
        json.dumps({"report_version": 2, "summary": {}}), encoding="utf-8"
    )
    candidate_path.write_text(
        json.dumps(
            {
                "report_version": 1,
                "summary": {
                    "ending_equity_usd": 10_000.0,
                    "return_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "filled_orders": 0,
                    "blocked_actions": 0,
                    "execution_errors": 0,
                    "per_strategy": {},
                    "replay_inputs": {},
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "compare-backtests",
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Unsupported report version" in output
