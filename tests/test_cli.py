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
from krakked.market_data.ohlc_refresh import (
    OHLCTailRefreshSeriesResult,
    OHLCTailRefreshSummary,
)
from krakked.portfolio.exceptions import PortfolioSchemaError
from krakked.portfolio.store import (
    CURRENT_SCHEMA_VERSION,
    MLArtifactGroup,
    SQLitePortfolioStore,
)
from krakked.strategy.ml_pruning import find_stale_ml_artifact_groups


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


def test_refresh_ohlc_subcommand_outputs_json_and_passes_filters(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    captured: dict[str, Any] = {}

    class _DummyMarketData:
        def __init__(self, config: Any) -> None:
            captured["config"] = config
            self.shutdown_called = False

        def refresh_universe(self) -> None:
            captured["refresh_universe"] = True

        def refresh_ohlc_tails(
            self,
            *,
            pairs: list[str] | None,
            timeframes: list[str] | None,
            since: int | None,
        ) -> OHLCTailRefreshSummary:
            captured["pairs"] = pairs
            captured["timeframes"] = timeframes
            captured["since"] = since
            return OHLCTailRefreshSummary(
                generated_at=datetime(2026, 5, 30, tzinfo=UTC),
                pairs=pairs or [],
                timeframes=timeframes or [],
                series=[
                    OHLCTailRefreshSeriesResult(
                        pair="BTC/USD",
                        timeframe="1h",
                        prior_latest_timestamp=1000,
                        since_timestamp=1772323200,
                        new_latest_timestamp=1060,
                        fetched_bars=1,
                        status="refreshed",
                    )
                ],
            )

        def shutdown(self) -> None:
            captured["shutdown"] = True

    monkeypatch.setattr(cli, "load_config", lambda config_path=None: object())
    monkeypatch.setattr(cli, "MarketDataAPI", _DummyMarketData)

    exit_code = cli.main(
        [
            "refresh-ohlc",
            "--pair",
            "BTC/USD",
            "--timeframe",
            "1h",
            "--since",
            "2026-03-01T00:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["refresh_universe"] is True
    assert captured["pairs"] == ["BTC/USD"]
    assert captured["timeframes"] == ["1h"]
    assert captured["since"] == 1772323200
    assert captured["shutdown"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["series"][0]["status"] == "refreshed"


def test_refresh_ohlc_subcommand_exits_nonzero_on_series_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    class _DummyMarketData:
        def __init__(self, config: Any) -> None:  # noqa: ARG002
            pass

        def refresh_universe(self) -> None:
            pass

        def refresh_ohlc_tails(self, **_: Any) -> OHLCTailRefreshSummary:
            return OHLCTailRefreshSummary(
                generated_at=datetime(2026, 5, 30, tzinfo=UTC),
                pairs=["BTC/USD"],
                timeframes=["2h"],
                series=[
                    OHLCTailRefreshSeriesResult(
                        pair="BTC/USD",
                        timeframe="2h",
                        prior_latest_timestamp=None,
                        since_timestamp=None,
                        new_latest_timestamp=None,
                        fetched_bars=0,
                        status="failed",
                        error="Unsupported timeframe",
                    )
                ],
            )

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(cli, "load_config", lambda config_path=None: object())
    monkeypatch.setattr(cli, "MarketDataAPI", _DummyMarketData)

    exit_code = cli.main(["refresh-ohlc", "--timeframe", "2h"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Failed: 1" in output
    assert "Unsupported timeframe" in output


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
        warmup_days: float | None = None,
        config_source: str = "provided_config",
        resolved_config_path: str | None = None,
        config_arg_supplied: bool = False,
    ) -> BacktestResult:
        captured["config"] = config
        captured["start"] = start
        captured["end"] = end
        captured["timeframes"] = timeframes
        captured["starting_cash_usd"] = starting_cash_usd
        captured["fee_bps"] = fee_bps
        captured["db_path"] = db_path
        captured["strict_data"] = strict_data
        captured["warmup_days"] = warmup_days
        captured["config_source"] = config_source
        captured["resolved_config_path"] = resolved_config_path
        captured["config_arg_supplied"] = config_arg_supplied
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
                clamped_actions=1,
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
                clamped_reason_counts={"Max per asset limit (750.00 > 500.00)": 1},
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
            "--warmup-days",
            "7",
        ]
    )

    assert exit_code == 0
    assert captured["starting_cash_usd"] == 10_000.0
    assert captured["fee_bps"] == 25.0
    assert captured["timeframes"] == ["1h"]
    assert captured["warmup_days"] == pytest.approx(7.0)
    output = capsys.readouterr().out
    assert "Backtest completed." in output
    assert "Wallet: start $10,000.00 -> end $10,250.00 (+250.00, +2.50%)" in output
    assert (
        "Replay trust: Limited signal: some strategy actions were blocked by guardrails."
        in output
    )
    assert "Cost model: 50 bps slippage + 25.00 bps taker fee" in output
    assert "Actions: 4 total, 1 blocked, 1 clamped" in output
    assert "Missing OHLC series:" in output
    assert "Top blocked reason: Max open positions reached (1) (1)" in output
    assert "Top clamped reason: Max per asset limit (750.00 > 500.00) (1)" in output
    assert captured["config_source"] == "default_paper_config"
    assert captured["config_arg_supplied"] is False
    assert captured["resolved_config_path"].endswith("config.yaml")


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
            "--strict-data",
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


def test_backtest_subcommand_publish_latest_requires_ready_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
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
                partial_series=["BTC/USD@15m"],
                status="limited",
                summary_note="Coverage is limited.",
                warnings=[
                    "1 requested series only partially cover the requested window."
                ],
            ),
            summary=BacktestSummary(
                start=start,
                end=end,
                starting_cash_usd=10_000.0,
                ending_equity_usd=10_000.0,
                absolute_pnl_usd=0.0,
                return_pct=0.0,
                max_drawdown_pct=0.0,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=0.0,
                pairs=["BTC/USD"],
                timeframes=["15m"],
                total_cycles=24,
                total_actions=0,
                blocked_actions=0,
                total_orders=0,
                filled_orders=0,
                rejected_orders=0,
                execution_errors=0,
                fee_bps=25.0,
                slippage_bps=50.0,
                cost_model="Immediate candle-close fills using configured slippage and flat taker fees.",
                usable_series_count=1,
                missing_series=[],
                partial_series=["BTC/USD@15m"],
                coverage=[],
                per_strategy={},
                replay_inputs={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD"],
                    "timeframes": ["15m"],
                    "enabled_strategies": ["vol_breakout"],
                    "starting_cash_usd": 10_000.0,
                    "fee_bps": 25.0,
                    "slippage_bps": 50.0,
                    "strict_data": False,
                },
                trust_level="limited",
                trust_note="Limited signal: historical coverage is incomplete for part of the requested window.",
                notable_warnings=[
                    "1 requested series only partially cover the requested window."
                ],
                blocked_reason_counts={},
                assumptions=["Synthetic fills only."],
            ),
        )

    monkeypatch.setattr(cli, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(cli, "get_config_dir", lambda: tmp_path)

    latest_path = tmp_path / "reports" / "backtests" / "latest.json"
    base_args = [
        "backtest",
        "--start",
        "2026-04-01T00:00:00Z",
        "--end",
        "2026-04-02T00:00:00Z",
        "--publish-latest",
    ]

    exit_code = cli.main(base_args)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "preflight status is 'limited'" in output
    assert "--allow-non-ready-publish" in output
    assert not latest_path.exists()

    exit_code = cli.main([*base_args, "--allow-non-ready-publish", "--json"])

    assert exit_code == 0
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    assert payload["preflight"]["status"] == "limited"


def test_rs_rotation_v2_research_subcommand_writes_json_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: dict[str, Any] = {}
    config = SimpleNamespace(execution=SimpleNamespace(max_slippage_bps=33))
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)

    class _FakeResearchResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "rs_rotation_v2_research",
                "generated_at": start.isoformat(),
                "summary": {
                    "strategy_id": "rs_rotation_v2",
                    "status": "research_pass",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD", "ETH/USD"],
                    "timeframe": "4h",
                    "starting_cash_usd": 10_000.0,
                    "ending_equity_usd": 10_050.0,
                    "absolute_pnl_usd": 50.0,
                    "return_pct": 0.5,
                    "max_drawdown_pct": 0.2,
                    "filled_orders": 2,
                    "blocked_actions": 0,
                    "execution_errors": 0,
                    "total_cycles": 6,
                    "active_cycles": 4,
                    "cash_cycles": 2,
                    "trade_count": 2,
                    "turnover_usd": 500.0,
                    "fees_usd": 0.0,
                    "slippage_estimate_usd": 0.0,
                    "equal_weight_reference": None,
                    "forward_diagnostics": {"evaluated_cycles": 0},
                    "gates": {
                        "positive_return_after_costs": {"passed": True},
                    },
                    "warnings": [],
                    "per_strategy": {},
                    "replay_inputs": {"params": {}},
                },
                "preflight": {"status": "ready"},
                "cycles": [],
                "trades": [],
            }

    def _fake_run_rs_rotation_v2_research(
        config_arg: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        params: Any,
        strict_data: bool,
    ) -> _FakeResearchResult:
        captured["config"] = config_arg
        captured["start"] = start
        captured["end"] = end
        captured["pairs"] = pairs
        captured["params"] = params
        captured["strict_data"] = strict_data
        return _FakeResearchResult()

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)
    monkeypatch.setattr(
        cli, "run_rs_rotation_v2_research", _fake_run_rs_rotation_v2_research
    )

    report_path = tmp_path / "rs-v2.json"
    exit_code = cli.main(
        [
            "rs-rotation-v2-research",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
            "--pair",
            "BTC/USD",
            "--pair",
            "ETH/USD",
            "--timeframe",
            "4h",
            "--lookback-bars",
            "10",
            "--volatility-lookback-bars",
            "12",
            "--top-n",
            "1",
            "--total-allocation-pct",
            "5",
            "--save-report",
            str(report_path),
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["config"] is config
    assert captured["pairs"] == ["BTC/USD", "ETH/USD"]
    assert captured["strict_data"] is False
    assert captured["params"].timeframe == "4h"
    assert captured["params"].lookback_bars == 10
    assert captured["params"].volatility_lookback_bars == 12
    assert captured["params"].top_n == 1
    assert captured["params"].slippage_bps == 33.0

    stdout_payload = json.loads(capsys.readouterr().out)
    saved_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert stdout_payload["provenance"]["generated_by"] == (
        "krakked rs-rotation-v2-research"
    )
    assert saved_payload["summary"]["replay_inputs"]["config_path"] is None
    assert saved_payload["report_type"] == "rs_rotation_v2_research"


def test_rs_rotation_v2_research_subcommand_returns_nonzero_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config = SimpleNamespace(execution=SimpleNamespace(max_slippage_bps=50))

    def _raise(*_: Any, **__: Any) -> None:
        raise ValueError("missing: BTC/USD@4h")

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)
    monkeypatch.setattr(cli, "run_rs_rotation_v2_research", _raise)

    exit_code = cli.main(
        [
            "rs-rotation-v2-research",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
            "--timeframe",
            "4h",
            "--lookback-bars",
            "10",
            "--top-n",
            "1",
            "--total-allocation-pct",
            "5",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "RS rotation v2 research failed" in output
    assert "missing: BTC/USD@4h" in output


def test_market_regime_research_subcommand_writes_json_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)

    class _FakeRegimeResearchResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "market_regime_research",
                "generated_at": start.isoformat(),
                "summary": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "pairs": ["BTC/USD", "ETH/USD"],
                    "benchmark_pair": "BTC/USD",
                    "timeframe": "4h",
                    "total_cycles": 2,
                    "risk_on_cycles": 1,
                    "neutral_cycles": 1,
                    "risk_off_cycles": 0,
                    "reason_counts": {"btc_momentum_soft": 1},
                },
                "preflight": {"status": "ready"},
                "cycles": [],
            }

    def _fake_run_market_regime_research(
        config: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        params: Any,
        strict_data: bool,
    ) -> _FakeRegimeResearchResult:
        captured["config"] = config
        captured["start"] = start
        captured["end"] = end
        captured["pairs"] = pairs
        captured["params"] = params
        captured["strict_data"] = strict_data
        return _FakeRegimeResearchResult()

    config = object()
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)
    monkeypatch.setattr(
        cli, "run_market_regime_research", _fake_run_market_regime_research
    )

    report_path = tmp_path / "market-regime.json"
    exit_code = cli.main(
        [
            "market-regime-research",
            "--start",
            "2026-05-01T00:00:00Z",
            "--end",
            "2026-05-02T00:00:00Z",
            "--pair",
            "BTC/USD",
            "--pair",
            "ETH/USD",
            "--timeframe",
            "4h",
            "--save-report",
            str(report_path),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["config"] is config
    assert captured["pairs"] == ["BTC/USD", "ETH/USD"]
    assert captured["params"].timeframe == "4h"
    assert captured["params"].benchmark_pair == "BTC/USD"
    assert captured["strict_data"] is True
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "market_regime_research"
    assert saved["provenance"]["generated_by"] == "krakked market-regime-research"


def test_market_regime_overlay_backtest_subcommand_writes_json_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)

    class _FakeOverlayBacktestResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "market_regime_overlay_backtest",
                "generated_at": start.isoformat(),
                "summary": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "baseline": {
                        "return_pct": -0.2,
                        "max_drawdown_pct": 1.0,
                    },
                    "overlay": {
                        "return_pct": -0.1,
                        "max_drawdown_pct": 0.5,
                    },
                    "delta": {
                        "return_pct": 0.1,
                        "max_drawdown_pct": -0.5,
                    },
                    "overlay_interventions": {
                        "overlay_interventions": 2,
                        "overlay_blocked_actions": 1,
                        "overlay_clamped_actions": 1,
                        "state_counts": {"risk_off": 1},
                        "reason_counts": {"btc_momentum_negative": 1},
                    },
                },
                "baseline": {"summary": {"return_pct": -0.2}},
                "overlay": {"summary": {"return_pct": -0.1}},
            }

    def _fake_run_market_regime_overlay_backtest(
        config: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        params: Any,
        timeframes: list[str] | None,
        starting_cash_usd: float,
        fee_bps: float,
        strict_data: bool,
    ) -> _FakeOverlayBacktestResult:
        captured["config"] = config
        captured["start"] = start
        captured["end"] = end
        captured["pairs"] = pairs
        captured["params"] = params
        captured["timeframes"] = timeframes
        captured["starting_cash_usd"] = starting_cash_usd
        captured["fee_bps"] = fee_bps
        captured["strict_data"] = strict_data
        return _FakeOverlayBacktestResult()

    config = object()
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)
    monkeypatch.setattr(
        cli,
        "run_market_regime_overlay_backtest",
        _fake_run_market_regime_overlay_backtest,
    )

    report_path = tmp_path / "market-regime-overlay.json"
    exit_code = cli.main(
        [
            "market-regime-overlay-backtest",
            "--start",
            "2026-05-01T00:00:00Z",
            "--end",
            "2026-05-02T00:00:00Z",
            "--pair",
            "BTC/USD",
            "--replay-timeframe",
            "1h",
            "--replay-timeframe",
            "4h",
            "--starting-cash-usd",
            "12000",
            "--fee-bps",
            "10",
            "--save-report",
            str(report_path),
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["pairs"] == ["BTC/USD"]
    assert captured["timeframes"] == ["1h", "4h"]
    assert captured["starting_cash_usd"] == 12_000.0
    assert captured["fee_bps"] == 10.0
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "market_regime_overlay_backtest"
    assert saved["provenance"]["generated_by"] == (
        "krakked market-regime-overlay-backtest"
    )


def test_market_regime_throttle_backtest_subcommand_writes_json_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)

    class _FakeThrottleBacktestResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "market_regime_throttle_backtest",
                "generated_at": start.isoformat(),
                "summary": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "baseline": {
                        "return_pct": -0.2,
                        "max_drawdown_pct": 1.0,
                        "total_actions": 2,
                        "filled_orders": 1,
                    },
                    "throttle": {
                        "return_pct": -0.1,
                        "max_drawdown_pct": 0.5,
                        "total_actions": 2,
                        "filled_orders": 1,
                    },
                    "delta": {
                        "return_pct": 0.1,
                        "max_drawdown_pct": -0.5,
                    },
                    "throttle_interventions": {
                        "throttled_actions": 1,
                        "blocked_actions": 0,
                        "clamped_actions": 1,
                        "intervention_cycles": 1,
                        "state_counts": {"neutral": 1},
                        "reason_counts": {"btc_momentum_soft": 1},
                    },
                    "promotion_checks": {"passed": True},
                },
                "baseline": {"summary": {"return_pct": -0.2}},
                "throttle": {"summary": {"return_pct": -0.1}},
            }

    def _fake_run_market_regime_throttle_backtest(
        config: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        params: Any,
        timeframes: list[str] | None,
        starting_cash_usd: float,
        fee_bps: float,
        strict_data: bool,
        warmup_days: float | None,
        unavailable_policy: str,
    ) -> _FakeThrottleBacktestResult:
        captured["config"] = config
        captured["start"] = start
        captured["end"] = end
        captured["pairs"] = pairs
        captured["params"] = params
        captured["timeframes"] = timeframes
        captured["starting_cash_usd"] = starting_cash_usd
        captured["fee_bps"] = fee_bps
        captured["strict_data"] = strict_data
        captured["warmup_days"] = warmup_days
        captured["unavailable_policy"] = unavailable_policy
        return _FakeThrottleBacktestResult()

    config = object()
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)
    monkeypatch.setattr(
        cli,
        "run_market_regime_throttle_backtest",
        _fake_run_market_regime_throttle_backtest,
    )

    report_path = tmp_path / "market-regime-throttle.json"
    exit_code = cli.main(
        [
            "market-regime-throttle-backtest",
            "--start",
            "2026-05-01T00:00:00Z",
            "--end",
            "2026-05-02T00:00:00Z",
            "--pair",
            "BTC/USD",
            "--replay-timeframe",
            "1h",
            "--unavailable-policy",
            "allow",
            "--starting-cash-usd",
            "12000",
            "--fee-bps",
            "10",
            "--warmup-days",
            "5",
            "--save-report",
            str(report_path),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["pairs"] == ["BTC/USD"]
    assert captured["params"].momentum_lookback_bars == 63
    assert captured["params"].neutral_allocation_multiplier == pytest.approx(0.75)
    assert captured["params"].risk_off_allocation_multiplier == pytest.approx(0.25)
    assert captured["timeframes"] == ["1h"]
    assert captured["starting_cash_usd"] == 12_000.0
    assert captured["fee_bps"] == 10.0
    assert captured["warmup_days"] == pytest.approx(5.0)
    assert captured["strict_data"] is True
    assert captured["unavailable_policy"] == "allow"
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "market_regime_throttle_backtest"
    assert saved["provenance"]["generated_by"] == (
        "krakked market-regime-throttle-backtest"
    )


def test_market_regime_throttle_backtest_rejects_invalid_timeframe(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())

    exit_code = cli.main(
        [
            "market-regime-throttle-backtest",
            "--start",
            "2026-05-01T00:00:00Z",
            "--end",
            "2026-05-02T00:00:00Z",
            "--timeframe",
            "2h",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Market regime throttle backtest failed" in output
    assert "Unsupported market regime timeframe" in output


def test_market_regime_exposure_research_subcommand_writes_json_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 2, tzinfo=UTC)

    class _FakeExposureResearchResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "market_regime_exposure_research",
                "generated_at": start.isoformat(),
                "summary": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "scenarios": ["trend_proxy"],
                    "overlay_modes": ["target_scale"],
                    "comparison_count": 1,
                    "positive_return_comparisons": 1,
                    "drawdown_improved_comparisons": 1,
                    "not_cash_only_comparisons": 1,
                    "best_by_return": {
                        "scenario_id": "trend_proxy",
                        "overlay_mode": "target_scale",
                        "delta": {"return_pct": 0.1},
                    },
                },
                "preflight": {"status": "ready"},
                "runs": [],
                "comparisons": [],
            }

    def _fake_run_market_regime_exposure_research(
        config: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        regime_params: Any,
        scenario_params: Any,
        scenarios: list[str] | None,
        overlay_modes: list[str] | None,
        strict_data: bool,
    ) -> _FakeExposureResearchResult:
        captured["config"] = config
        captured["start"] = start
        captured["end"] = end
        captured["pairs"] = pairs
        captured["regime_params"] = regime_params
        captured["scenario_params"] = scenario_params
        captured["scenarios"] = scenarios
        captured["overlay_modes"] = overlay_modes
        captured["strict_data"] = strict_data
        return _FakeExposureResearchResult()

    config = object()
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: config)
    monkeypatch.setattr(
        cli,
        "run_market_regime_exposure_research",
        _fake_run_market_regime_exposure_research,
    )

    report_path = tmp_path / "market-regime-exposure.json"
    exit_code = cli.main(
        [
            "market-regime-exposure-research",
            "--start",
            "2026-05-01T00:00:00Z",
            "--end",
            "2026-05-02T00:00:00Z",
            "--pair",
            "BTC/USD",
            "--scenario",
            "trend_proxy",
            "--overlay-mode",
            "target_scale",
            "--allocation-pct",
            "50",
            "--rebalance-interval-bars",
            "3",
            "--fee-bps",
            "10",
            "--target-lookback-bars",
            "63",
            "--min-momentum-bps",
            "150",
            "--max-target-pairs",
            "4",
            "--save-report",
            str(report_path),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["pairs"] == ["BTC/USD"]
    assert captured["scenarios"] == ["trend_proxy"]
    assert captured["overlay_modes"] == ["target_scale"]
    assert captured["scenario_params"].allocation_pct == 50.0
    assert captured["scenario_params"].rebalance_interval_bars == 3
    assert captured["scenario_params"].fee_bps == 10.0
    assert captured["scenario_params"].target_lookback_bars == 63
    assert captured["scenario_params"].min_momentum_bps == 150.0
    assert captured["scenario_params"].max_target_pairs == 4
    assert captured["strict_data"] is True
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "market_regime_exposure_research"
    assert saved["provenance"]["generated_by"] == (
        "krakked market-regime-exposure-research"
    )


def test_market_regime_exposure_sweep_writes_reports_and_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: list[dict[str, Any]] = []

    class _FakeExposureSweepResult:
        def __init__(
            self,
            *,
            start: datetime,
            end: datetime,
            allocation_pct: float,
        ) -> None:
            self.start = start
            self.end = end
            self.allocation_pct = allocation_pct

        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "market_regime_exposure_research",
                "generated_at": self.start.isoformat(),
                "summary": {
                    "start": self.start.isoformat(),
                    "end": self.end.isoformat(),
                    "reason_counts": {"risk_on_conditions_met": 1},
                    "scenario_params": {"allocation_pct": self.allocation_pct},
                },
                "preflight": {
                    "status": "ready",
                    "missing_series": [],
                    "partial_series": [],
                },
                "runs": [],
                "comparisons": [
                    {
                        "scenario_id": "trend_rank_proxy",
                        "overlay_mode": "target_scale",
                        "baseline": {
                            "return_pct": 1.0,
                            "max_drawdown_pct": 2.0,
                            "active_cycle_pct": 100.0,
                            "avg_exposure_pct": 20.0,
                        },
                        "overlay": {
                            "return_pct": 1.5,
                            "max_drawdown_pct": 1.0,
                            "active_cycle_pct": 75.0,
                            "avg_exposure_pct": 10.0,
                        },
                        "delta": {
                            "return_pct": 0.5,
                            "max_drawdown_pct": -1.0,
                        },
                        "overlay_interventions": {
                            "overlay_interventions": 2,
                            "overlay_target_reductions": 2,
                        },
                    }
                ],
            }

    def _fake_run_market_regime_exposure_research(
        config: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        regime_params: Any,
        scenario_params: Any,
        scenarios: list[str] | None,
        overlay_modes: list[str] | None,
        strict_data: bool,
    ) -> _FakeExposureSweepResult:
        captured.append(
            {
                "config": config,
                "start": start,
                "end": end,
                "pairs": pairs,
                "regime_params": regime_params,
                "scenario_params": scenario_params,
                "scenarios": scenarios,
                "overlay_modes": overlay_modes,
                "strict_data": strict_data,
            }
        )
        return _FakeExposureSweepResult(
            start=start,
            end=end,
            allocation_pct=float(scenario_params.allocation_pct),
        )

    monkeypatch.setitem(
        cli.MARKET_REGIME_EXPOSURE_WINDOW_SETS,
        "tiny",
        [
            ("w1", "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z"),
            ("w2", "2026-05-02T00:00:00Z", "2026-05-03T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())
    monkeypatch.setattr(
        cli,
        "run_market_regime_exposure_research",
        _fake_run_market_regime_exposure_research,
    )

    save_dir = tmp_path / "sweep"
    exit_code = cli.main(
        [
            "market-regime-exposure-sweep",
            "--window-set",
            "tiny",
            "--scenario",
            "trend_rank_proxy",
            "--overlay-mode",
            "target_scale",
            "--allocation-pct",
            "5",
            "--allocation-pct",
            "20",
            "--save-dir",
            str(save_dir),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert len(captured) == 4
    assert {call["scenario_params"].allocation_pct for call in captured} == {
        5.0,
        20.0,
    }
    assert all(call["scenarios"] == ["trend_rank_proxy"] for call in captured)
    assert all(call["overlay_modes"] == ["target_scale"] for call in captured)
    aggregate = json.loads((save_dir / "aggregate.json").read_text(encoding="utf-8"))
    output = json.loads(capsys.readouterr().out)
    assert output["report_type"] == "market_regime_exposure_sweep"
    assert aggregate["summary"]["report_count"] == 4
    assert len(aggregate["summary"]["groups"]) == 2
    assert all(
        group["promotion_gate"]["overlay_exposure_ratio"]
        for group in aggregate["summary"]["groups"]
    )


def test_target_source_research_writes_reports_and_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: list[dict[str, Any]] = []

    class _FakeTargetSourceResult:
        def __init__(
            self,
            *,
            start: datetime,
            end: datetime,
            allocation_pct: float,
            scenario_id: str,
        ) -> None:
            self.start = start
            self.end = end
            self.allocation_pct = allocation_pct
            self.scenario_id = scenario_id

        def to_report_dict(self) -> dict[str, Any]:
            is_baseline = self.scenario_id == "rank_top2"
            return {
                "report_version": 1,
                "report_type": "target_source_research",
                "generated_at": self.start.isoformat(),
                "summary": {
                    "research_only": True,
                    "runtime_wiring_approved": False,
                    "start": self.start.isoformat(),
                    "end": self.end.isoformat(),
                    "scenarios": [self.scenario_id],
                    "params": {"allocation_pct": self.allocation_pct},
                    "strict_data_ready": True,
                },
                "preflight": {
                    "status": "ready",
                    "missing_series": [],
                    "partial_series": [],
                },
                "runs": [
                    {
                        "scenario_id": self.scenario_id,
                        "research_only": True,
                        "runtime_wiring_approved": False,
                        "defensive_only": False,
                        "return_pct": 0.0 if is_baseline else 0.2,
                        "max_drawdown_pct": 1.0 if is_baseline else 0.5,
                        "trades": 2,
                        "fees_usd": 1.0,
                        "cash_target_rebalances": 0,
                        "active_cycle_pct": 100.0,
                        "avg_exposure_pct": 10.0,
                        "target_selection_counts": {"BTC/USD": 1},
                        "strict_data_ready": True,
                    }
                ],
            }

    def _fake_run_target_source_research(
        config: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        params: Any,
        scenarios: list[str] | None,
        strict_data: bool,
    ) -> _FakeTargetSourceResult:
        captured.append(
            {
                "config": config,
                "start": start,
                "end": end,
                "pairs": pairs,
                "params": params,
                "scenarios": scenarios,
                "strict_data": strict_data,
            }
        )
        assert scenarios is not None
        return _FakeTargetSourceResult(
            start=start,
            end=end,
            allocation_pct=float(params.allocation_pct),
            scenario_id=scenarios[0],
        )

    monkeypatch.setitem(
        cli.MARKET_REGIME_EXPOSURE_WINDOW_SETS,
        "tiny",
        [("20260510-20260530", "2026-05-10T00:00:00Z", "2026-05-30T00:00:00Z")],
    )
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())
    monkeypatch.setattr(
        cli,
        "run_target_source_research",
        _fake_run_target_source_research,
    )

    save_dir = tmp_path / "target-source"
    exit_code = cli.main(
        [
            "target-source-research",
            "--window-set",
            "tiny",
            "--scenario",
            "rank_top2",
            "--scenario",
            "dual_momentum_top2",
            "--allocation-pct",
            "20",
            "--timeframe",
            "4h",
            "--rebalance-interval-bars",
            "6",
            "--fee-bps",
            "25",
            "--save-dir",
            str(save_dir),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert len(captured) == 2
    assert all(call["strict_data"] is True for call in captured)
    assert all(call["params"].allocation_pct == 20.0 for call in captured)
    assert [call["scenarios"][0] for call in captured] == [
        "rank_top2",
        "dual_momentum_top2",
    ]
    aggregate = json.loads((save_dir / "aggregate.json").read_text(encoding="utf-8"))
    output = json.loads(capsys.readouterr().out)
    assert output["report_type"] == "target_source_research_sweep"
    assert aggregate["summary"]["report_count"] == 2
    assert len(aggregate["summary"]["rows"]) == 2
    assert (
        save_dir
        / "tiny"
        / "allocation-20"
        / "dual_momentum_top2"
        / "20260510-20260530.json"
    ).exists()


def test_target_source_research_invalid_args_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setitem(
        cli.MARKET_REGIME_EXPOSURE_WINDOW_SETS,
        "tiny",
        [("w1", "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z")],
    )
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())

    exit_code = cli.main(
        [
            "target-source-research",
            "--window-set",
            "tiny",
            "--timeframe",
            "1h",
            "--save-dir",
            str(tmp_path / "bad-timeframe"),
        ]
    )

    assert exit_code == 1

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "target-source-research",
                "--window-set",
                "tiny",
                "--scenario",
                "not_a_source",
                "--save-dir",
                str(tmp_path / "bad-scenario"),
            ]
        )
    assert excinfo.value.code == 2


def test_pair_local_source_research_writes_reports_and_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: list[dict[str, Any]] = []

    class _FakePairLocalSourceResult:
        def __init__(
            self,
            *,
            start: datetime,
            end: datetime,
            allocation_pct: float,
            scenario_id: str,
        ) -> None:
            self.start = start
            self.end = end
            self.allocation_pct = allocation_pct
            self.scenario_id = scenario_id

        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "pair_local_source_research",
                "generated_at": self.start.isoformat(),
                "summary": {
                    "research_only": True,
                    "runtime_wiring_approved": False,
                    "start": self.start.isoformat(),
                    "end": self.end.isoformat(),
                    "scenarios": [self.scenario_id],
                    "params": {"allocation_pct": self.allocation_pct},
                    "strict_data_ready": True,
                },
                "preflight": {
                    "status": "ready",
                    "missing_series": [],
                    "partial_series": [],
                },
                "runs": [
                    {
                        "scenario_id": self.scenario_id,
                        "pair": "BTC/USD",
                        "research_only": True,
                        "runtime_wiring_approved": False,
                        "return_pct": 0.2,
                        "gross_return_before_fees_pct": 0.3,
                        "max_drawdown_pct": 0.5,
                        "trades": 2,
                        "fees_usd": 1.0,
                        "fee_drag_pct_of_starting_cash": 0.01,
                        "active_cycle_pct": 50.0,
                        "active_rebalance_pct": 50.0,
                        "avg_exposure_pct": 10.0,
                        "strict_data_ready": True,
                        "diagnostics": {"failure_reasons": []},
                    }
                ],
            }

    def _fake_run_pair_local_source_research(
        config: Any,
        *,
        start: datetime,
        end: datetime,
        pairs: list[str] | None,
        params: Any,
        scenarios: list[str] | None,
        strict_data: bool,
    ) -> _FakePairLocalSourceResult:
        captured.append(
            {
                "config": config,
                "start": start,
                "end": end,
                "pairs": pairs,
                "params": params,
                "scenarios": scenarios,
                "strict_data": strict_data,
            }
        )
        assert scenarios is not None
        return _FakePairLocalSourceResult(
            start=start,
            end=end,
            allocation_pct=float(params.allocation_pct),
            scenario_id=scenarios[0],
        )

    monkeypatch.setitem(
        cli.MARKET_REGIME_EXPOSURE_WINDOW_SETS,
        "tiny",
        [("20260510-20260530", "2026-05-10T00:00:00Z", "2026-05-30T00:00:00Z")],
    )
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())
    monkeypatch.setattr(
        cli,
        "run_pair_local_source_research",
        _fake_run_pair_local_source_research,
    )

    save_dir = tmp_path / "pair-local"
    exit_code = cli.main(
        [
            "pair-local-source-research",
            "--window-set",
            "tiny",
            "--scenario",
            "pair_dual_momentum",
            "--allocation-pct",
            "20",
            "--timeframe",
            "4h",
            "--rebalance-interval-bars",
            "6",
            "--fee-bps",
            "25",
            "--save-dir",
            str(save_dir),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert len(captured) == 1
    assert captured[0]["strict_data"] is True
    assert captured[0]["params"].allocation_pct == 20.0
    aggregate = json.loads((save_dir / "aggregate.json").read_text(encoding="utf-8"))
    output = json.loads(capsys.readouterr().out)
    assert output["report_type"] == "pair_local_source_research_sweep"
    assert aggregate["summary"]["report_count"] == 1
    assert (
        save_dir
        / "tiny"
        / "allocation-20"
        / "pair_dual_momentum"
        / "20260510-20260530.json"
    ).exists()


def test_strategy_activity_sweep_writes_reports_and_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 5, 1, tzinfo=UTC)

    class _FakeStrategyActivityResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "strategy_activity_sweep",
                "generated_at": start.isoformat(),
                "summary": {
                    "research_only": True,
                    "runtime_config_changed": False,
                    "group_count": 1,
                    "run_count": 1,
                    "window_sets": ["tiny"],
                    "groups": [
                        {
                            "group_id": "configured",
                            "strategies": ["trend_core"],
                            "window_count": 1,
                            "ready_windows": 1,
                            "action_windows": 1,
                            "fill_windows": 1,
                            "execution_error_windows": 0,
                            "stage_counts": {"filled": 1},
                            "total_actions": 1,
                            "total_filled_orders": 1,
                            "avg_actions_per_window": 1.0,
                            "avg_fills_per_window": 1.0,
                            "gate2_candidate": True,
                        }
                    ],
                    "gate2_candidate_groups": ["configured"],
                    "best_gate2_candidate_group": "configured",
                    "ready_for_gate2": True,
                },
                "runs": [
                    {
                        "window_set": "tiny",
                        "window_id": "w1",
                        "group_id": "configured",
                        "strategies": ["trend_core"],
                        "stage": "filled",
                        "total_actions": 1,
                        "filled_orders": 1,
                    }
                ],
            }

    def _fake_run_strategy_activity_sweep(
        config: Any,
        *,
        window_sets: Any,
        groups: Any,
        starting_cash_usd: float,
        fee_bps: float,
        strict_data: bool,
        warmup_days: float | None,
    ) -> _FakeStrategyActivityResult:
        captured["config"] = config
        captured["window_sets"] = window_sets
        captured["groups"] = groups
        captured["starting_cash_usd"] = starting_cash_usd
        captured["fee_bps"] = fee_bps
        captured["strict_data"] = strict_data
        captured["warmup_days"] = warmup_days
        return _FakeStrategyActivityResult()

    monkeypatch.setitem(
        cli.STRATEGY_ACTIVITY_WINDOW_SETS,
        "tiny",
        [("w1", "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z")],
    )
    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())
    monkeypatch.setattr(
        cli,
        "build_strategy_activity_groups",
        lambda config, group_ids=None, custom_strategies=None: [
            SimpleNamespace(group_id="configured", strategies=("trend_core",))
        ],
    )
    monkeypatch.setattr(
        cli,
        "run_strategy_activity_sweep",
        _fake_run_strategy_activity_sweep,
    )

    save_dir = tmp_path / "activity"
    exit_code = cli.main(
        [
            "strategy-activity-sweep",
            "--window-set",
            "tiny",
            "--group",
            "configured",
            "--warmup-days",
            "3",
            "--save-dir",
            str(save_dir),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert list(captured["window_sets"]) == ["tiny"]
    assert captured["groups"][0].group_id == "configured"
    assert captured["warmup_days"] == pytest.approx(3.0)
    assert captured["strict_data"] is True
    output = json.loads(capsys.readouterr().out)
    aggregate = json.loads((save_dir / "aggregate.json").read_text(encoding="utf-8"))
    assert output["report_type"] == "strategy_activity_sweep"
    assert aggregate["summary"]["ready_for_gate2"] is True
    assert (save_dir / "tiny" / "configured" / "w1.json").exists()


def test_strategy_evidence_scoreboard_writes_shared_aggregate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 5, 1, tzinfo=UTC)

    class _FakeStrategyActivityResult:
        runs = [
            {
                "window_set": "tiny",
                "window_id": "w1",
                "group_id": "ai_regression",
                "strategies": ["ai_regression"],
                "stage": "filled",
                "return_pct": -0.1,
                "max_drawdown_pct": 0.2,
                "total_actions": 1,
                "filled_orders": 1,
            }
        ]

        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "strategy_activity_sweep",
                "generated_at": start.isoformat(),
                "summary": {
                    "research_only": True,
                    "runtime_config_changed": False,
                    "group_count": 1,
                    "run_count": 1,
                    "window_sets": ["tiny"],
                    "groups": [],
                    "ready_for_gate2": False,
                },
                "runs": [dict(self.runs[0])],
            }

    def _fake_run_strategy_activity_sweep(
        config: Any,
        *,
        window_sets: Any,
        groups: Any,
        starting_cash_usd: float,
        fee_bps: float,
        strict_data: bool,
        warmup_days: float | None,
    ) -> _FakeStrategyActivityResult:
        captured["config"] = config
        captured["window_sets"] = window_sets
        captured["groups"] = groups
        captured["starting_cash_usd"] = starting_cash_usd
        captured["fee_bps"] = fee_bps
        captured["strict_data"] = strict_data
        captured["warmup_days"] = warmup_days
        return _FakeStrategyActivityResult()

    monkeypatch.setitem(
        cli.STRATEGY_ACTIVITY_WINDOW_SETS,
        "tiny",
        [("w1", "2026-05-01T00:00:00Z", "2026-05-02T00:00:00Z")],
    )
    monkeypatch.setattr(
        cli,
        "_load_backtest_config",
        lambda args: SimpleNamespace(universe=SimpleNamespace(include_pairs=["BTC/USD"])),
    )
    monkeypatch.setattr(
        cli,
        "build_strategy_evidence_groups",
        lambda config, group_ids=None, strategy_ids=None: [
            SimpleNamespace(group_id="ai_regression", strategies=("ai_regression",))
        ],
    )
    monkeypatch.setattr(
        cli,
        "run_strategy_activity_sweep",
        _fake_run_strategy_activity_sweep,
    )
    monkeypatch.setattr(
        cli,
        "build_strategy_evidence_baselines",
        lambda *args, **kwargs: {
            "cash": {"return_pct": 0.0, "max_drawdown_pct": 0.0},
            "buy_hold_equal_weight": {
                "window_count": 1,
                "usable_windows": 1,
                "avg_return_pct": -1.0,
                "avg_max_drawdown_pct": 2.0,
            },
        },
    )

    save_dir = tmp_path / "scoreboard"
    exit_code = cli.main(
        [
            "strategy-evidence-scoreboard",
            "--window-set",
            "tiny",
            "--strategy",
            "ai_regression",
            "--fee-bps",
            "30",
            "--save-dir",
            str(save_dir),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["strict_data"] is True
    assert captured["fee_bps"] == pytest.approx(30.0)
    output = json.loads(capsys.readouterr().out)
    aggregate = json.loads((save_dir / "aggregate.json").read_text(encoding="utf-8"))
    assert output["report_type"] == "strategy_evidence_scoreboard"
    assert aggregate["summary"]["scoreboard"]["same_replay_engine"] is True
    assert aggregate["summary"]["scoreboard"]["rows"][0]["group_id"] == "ai_regression"
    assert (save_dir / "tiny" / "ai_regression" / "w1.json").exists()


def test_strategy_action_diagnostics_subcommand_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: Any,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeActionDiagnosticsResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "strategy_action_diagnostics",
                "generated_at": "2026-05-31T00:00:00+00:00",
                "summary": {
                    "research_only": True,
                    "runtime_config_changed": False,
                    "start": "2026-05-10T00:00:00+00:00",
                    "end": "2026-05-30T00:00:00+00:00",
                    "selected_strategies": ["trend_core"],
                    "selected_timeframes": ["1h"],
                    "trust_level": "limited",
                    "trust_note": "test",
                    "warmup_status": "ready",
                    "warmup_days": 30.0,
                    "total_cycles": 1,
                    "total_actions": 1,
                    "blocked_actions": 0,
                    "clamped_actions": 0,
                    "none_actions": 0,
                    "executable_actions": 1,
                    "total_orders": 1,
                    "filled_orders": 1,
                    "rejected_orders": 0,
                    "execution_errors": 0,
                    "return_pct": -0.1,
                    "max_drawdown_pct": 0.2,
                    "realized_pnl_usd": -1.0,
                    "approx_fill_realized_pnl_usd": -1.0,
                    "gross_turnover_usd": 100.0,
                    "blocked_reason_buckets": {},
                    "clamped_reason_buckets": {},
                    "none_reason_buckets": {},
                    "action_type_counts": {"open": 1},
                    "order_status_counts": {"filled": 1},
                    "stage_assessment": "filled",
                },
                "strategy_diagnostics": [],
                "pair_diagnostics": [],
                "fill_tape": [],
                "cycle_diagnostics": {},
                "preflight": None,
            }

    def _fake_run_strategy_action_diagnostics(config: Any, **kwargs: Any) -> Any:
        captured["config"] = config
        captured.update(kwargs)
        return _FakeActionDiagnosticsResult()

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())
    monkeypatch.setattr(
        cli,
        "run_strategy_action_diagnostics",
        _fake_run_strategy_action_diagnostics,
    )

    report_path = tmp_path / "action-diagnostics.json"
    exit_code = cli.main(
        [
            "strategy-action-diagnostics",
            "--start",
            "2026-05-10T00:00:00Z",
            "--end",
            "2026-05-30T00:00:00Z",
            "--strategy",
            "trend_core",
            "--timeframe",
            "1h",
            "--warmup-days",
            "30",
            "--max-fill-rows",
            "25",
            "--save-report",
            str(report_path),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["strategies"] == ["trend_core"]
    assert captured["timeframes"] == ["1h"]
    assert captured["warmup_days"] == pytest.approx(30.0)
    assert captured["max_fill_rows"] == 25
    assert captured["strict_data"] is True
    output = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert output["report_type"] == "strategy_action_diagnostics"
    assert saved["summary"]["stage_assessment"] == "filled"


def test_trend_core_signal_quality_subcommand_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: Any,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeSignalQualityResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 1,
                "report_type": "trend_core_signal_quality",
                "generated_at": "2026-05-31T00:00:00+00:00",
                "summary": {
                    "research_only": True,
                    "runtime_config_changed": False,
                    "start": "2026-05-10T00:00:00+00:00",
                    "end": "2026-05-30T00:00:00+00:00",
                    "pairs": ["BTC/USD"],
                    "timeframes": ["4h"],
                    "forward_horizon_bars": [6],
                    "primary_horizon_bars": 6,
                    "fee_bps": 25.0,
                    "round_trip_fee_hurdle_pct": 0.5,
                    "fresh_bars_only": True,
                    "strict_data": True,
                    "warmup_days": 30.0,
                    "total_signals": 40,
                    "status": "edge_not_proven",
                    "status_note": "test",
                    "promotion_ready": False,
                    "gate_reasons": ["test reason"],
                },
                "overall": {
                    "6": {
                        "sample_count": 40,
                        "mean_return_pct": 0.1,
                        "median_return_pct": 0.1,
                        "hit_rate": 0.5,
                    }
                },
                "by_timeframe": [],
                "by_pair": [],
                "by_trend_strength_quartile": [],
                "by_confidence_quartile": [],
                "strongest_vs_weakest": {
                    "strongest_minus_weakest_mean_return_pct": -0.1
                },
                "signals_sample": [],
                "preflight": None,
            }

    def _fake_run_trend_core_signal_quality(config: Any, **kwargs: Any) -> Any:
        captured["config"] = config
        captured.update(kwargs)
        return _FakeSignalQualityResult()

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())
    monkeypatch.setattr(
        cli,
        "run_trend_core_signal_quality",
        _fake_run_trend_core_signal_quality,
    )

    report_path = tmp_path / "signal-quality.json"
    exit_code = cli.main(
        [
            "trend-core-signal-quality",
            "--start",
            "2026-05-10T00:00:00Z",
            "--end",
            "2026-05-30T00:00:00Z",
            "--pair",
            "BTC/USD",
            "--timeframe",
            "4h",
            "--forward-horizon-bars",
            "6",
            "--warmup-days",
            "30",
            "--fresh-bars-only",
            "--max-signal-rows",
            "25",
            "--save-report",
            str(report_path),
            "--strict-data",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["pairs"] == ["BTC/USD"]
    assert captured["timeframes"] == ["4h"]
    assert captured["forward_horizon_bars"] == [6]
    assert captured["fresh_bars_only"] is True
    assert captured["strict_data"] is True
    assert captured["warmup_days"] == pytest.approx(30.0)
    assert captured["max_signal_rows"] == 25
    output = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert output["report_type"] == "trend_core_signal_quality"
    assert saved["summary"]["status"] == "edge_not_proven"


def test_market_regime_research_subcommand_returns_nonzero_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    def _raise(*_: Any, **__: Any) -> None:
        raise ValueError("missing: BTC/USD@4h")

    monkeypatch.setattr(cli, "_load_backtest_config", lambda args: object())
    monkeypatch.setattr(cli, "run_market_regime_research", _raise)

    exit_code = cli.main(
        [
            "market-regime-research",
            "--start",
            "2026-05-01T00:00:00Z",
            "--end",
            "2026-05-02T00:00:00Z",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Market regime research failed" in output
    assert "missing: BTC/USD@4h" in output


def test_ml_walk_forward_subcommand_writes_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)

    class _FakeWalkForwardResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 8,
                "generated_at": start.isoformat(),
                "summary": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "strategy_id": "ai_regression",
                    "strategy_type": "machine_learning_regression",
                    "timeframe": "1h",
                    "train_bars": 12,
                    "test_bars": 6,
                    "evaluation_mode": "rolling_window_isolated",
                    "edge_scoring_mode": "intent_hurdle_aligned",
                    "model_state_reused_across_folds": False,
                    "model_semantics": {
                        "model_family": "regression",
                        "strategy_type": "machine_learning_regression",
                        "training_target": "signed_return_delta",
                        "prediction_target": "signed_return_delta",
                        "prediction_targets": ["signed_return_delta"],
                        "feature_schema": "ohlc_v5",
                        "feature_profile": "all",
                        "feature_schemas": ["ohlc_v5"],
                        "feature_profiles": ["all"],
                    },
                    "cost_semantics": {
                        "fee_bps": 25.0,
                        "slippage_bps": 50.0,
                        "round_trip_cost_bps": 150.0,
                        "round_trip_cost_pct": 0.015,
                        "label_cost_multipliers": [],
                        "edge_cost_multipliers": [1.0],
                        "evaluation_hurdle_source": "effective_min_edge_pct",
                        "evaluation_hurdle_sources": {"effective_min_edge_pct": 3},
                        "evaluation_hurdle_pct": 0.015,
                        "evaluation_hurdle_pct_quantiles": {"count": 3},
                    },
                    "fold_count": 1,
                    "pairs": ["BTC/USD"],
                    "fee_bps": 25.0,
                    "slippage_bps": 50.0,
                    "round_trip_cost_bps": 150.0,
                    "coverage_status": "ready",
                    "warnings": [],
                    "metrics": {
                        "prediction_count": 3,
                        "positive_edge_prediction_count": 1,
                        "no_positive_edge_prediction_count": 2,
                        "directional_prediction_count": 3,
                        "directional_accuracy": 2 / 3,
                        "edge_prediction_accuracy": 1 / 3,
                        "fee_adjusted_hit_rate": 1 / 3,
                        "precision_long": 0.5,
                    },
                    "confidence_buckets": [],
                    "regression_calibration": {
                        "prediction_count": 3,
                        "threshold_sweeps": [],
                        "predicted_delta_deciles": [],
                        "monotonicity": {"available": False},
                    },
                    "baselines": {
                        "cash": {
                            "fold_count": 1,
                            "avg_return_pct": 0.0,
                            "positive_folds": 0,
                            "avg_max_drawdown_pct": 0.0,
                            "warnings": [],
                        },
                        "buy_hold_by_pair": {},
                        "buy_hold_equal_weight": {
                            "fold_count": 1,
                            "avg_return_pct": 0.1,
                            "positive_folds": 1,
                            "avg_max_drawdown_pct": 0.2,
                            "warnings": [],
                        },
                        "warnings": [],
                    },
                    "diagnostic_warnings": [],
                    "promotion_tier": "blocked",
                    "promotion_tiers": {
                        "research_promising": {
                            "tier": "research_promising",
                            "clears": False,
                            "reasons": ["Directional accuracy is below 52%."],
                        },
                        "risk_overlay_candidate": {
                            "tier": "risk_overlay_candidate",
                            "clears": False,
                            "reasons": [
                                "Earlier tier research promising did not clear."
                            ],
                        },
                        "self_standing": {
                            "tier": "self_standing",
                            "clears": False,
                            "reasons": [
                                "Earlier tier research promising did not clear."
                            ],
                        },
                    },
                    "promotable": False,
                    "promotable_reasons": ["Directional accuracy is below 52%."],
                    "folds": [],
                },
                "provenance": {"generated_by": "krakked ml-walk-forward"},
            }

    def _fake_run_ml_walk_forward(*args: Any, **kwargs: Any) -> _FakeWalkForwardResult:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeWalkForwardResult()

    monkeypatch.setattr(
        cli,
        "_load_backtest_config",
        lambda args: load_config(
            config_path=Path("config_examples/config.yaml"), env="paper"
        ),
    )
    monkeypatch.setattr(cli, "run_ml_walk_forward", _fake_run_ml_walk_forward)

    report_path = tmp_path / "ml-report.json"
    exit_code = cli.main(
        [
            "ml-walk-forward",
            "--start",
            "2026-04-01T00:00:00Z",
            "--end",
            "2026-04-02T00:00:00Z",
            "--strategy",
            "ai_regression",
            "--timeframe",
            "1h",
            "--train-bars",
            "12",
            "--test-bars",
            "6",
            "--slippage-bps",
            "20",
            "--feature-profile",
            "drop_weakest",
            "--save-report",
            str(report_path),
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["kwargs"]["strategy_id"] == "ai_regression"
    assert captured["kwargs"]["timeframe"] == "1h"
    assert captured["kwargs"]["train_bars"] == 12
    assert captured["kwargs"]["test_bars"] == 6
    assert captured["kwargs"]["slippage_bps"] == pytest.approx(20.0)
    assert (
        captured["args"][0]
        .strategies.configs["ai_regression"]
        .params["feature_profile"]
        == "drop_weakest"
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["metrics"]["prediction_count"] == 3
    assert payload["summary"]["config_path"] is None


def _seed_ml_prune_artifacts(db_path: Path) -> None:
    store = SQLitePortfolioStore(str(db_path))
    try:
        now = datetime(2026, 4, 1, tzinfo=UTC)
        for model_key in (
            "global|1h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1",
            "global|1h|features_ohlc_v4|pa_reg_eps0p001_scalerstdv1",
        ):
            store.record_ml_example(
                strategy_id="ai_regression",
                model_key=model_key,
                created_at=now,
                source_mode="paper",
                label_type="regression",
                features=[1.0, 2.0, 3.0],
                label=0.01,
            )
        store.save_ml_model(
            strategy_id="ai_regression",
            model_key="global|1h|features_ohlc_v4|pa_reg_eps0p001_scalerstdv1",
            label_type="regression",
            framework="dummy",
            model=SimpleNamespace(value=1),
        )
        store.save_ml_model_checkpoint(
            strategy_id="ai_regression",
            model_key="global|1h|features_ohlc_v4|pa_reg_eps0p001_scalerstdv1",
            checkpoint_kind="training",
            label_type="regression",
            framework="dummy",
            model=SimpleNamespace(value=2),
        )
    finally:
        store.close()


def _ml_artifact_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            "current_examples": conn.execute(
                """
                SELECT COUNT(*)
                FROM ml_training_examples
                WHERE strategy_id = 'ai_regression'
                    AND model_key = 'global|1h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1'
                """
            ).fetchone()[0],
            "stale_examples": conn.execute(
                """
                SELECT COUNT(*)
                FROM ml_training_examples
                WHERE strategy_id = 'ai_regression'
                    AND model_key = 'global|1h|features_ohlc_v4|pa_reg_eps0p001_scalerstdv1'
                """
            ).fetchone()[0],
            "stale_models": conn.execute(
                """
                SELECT COUNT(*)
                FROM ml_models
                WHERE strategy_id = 'ai_regression'
                    AND model_key = 'global|1h|features_ohlc_v4|pa_reg_eps0p001_scalerstdv1'
                """
            ).fetchone()[0],
            "stale_checkpoints": conn.execute(
                """
                SELECT COUNT(*)
                FROM ml_model_checkpoints
                WHERE strategy_id = 'ai_regression'
                    AND model_key = 'global|1h|features_ohlc_v4|pa_reg_eps0p001_scalerstdv1'
                """
            ).fetchone()[0],
        }


def test_ml_prune_stale_dry_run_reports_without_deleting(
    tmp_path: Path, capsys: Any
) -> None:
    db_path = tmp_path / "ml-prune.db"
    _seed_ml_prune_artifacts(db_path)

    exit_code = cli.main(
        [
            "ml-prune-stale",
            "--config",
            "config_examples/config.yaml",
            "--db-path",
            str(db_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "ML stale artifact prune dry run." in output
    assert "global|1h|features_ohlc_v4|pa_reg_eps0p001_scalerstdv1" in output
    assert "feature_schema_mismatch" in output
    assert _ml_artifact_counts(db_path) == {
        "current_examples": 1,
        "stale_examples": 1,
        "stale_models": 1,
        "stale_checkpoints": 1,
    }


def test_ml_prune_stale_retains_plural_timeframe_artifacts() -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    strat_cfg = config.strategies.configs["ai_regression"]
    params = dict(strat_cfg.params or {})
    params.pop("timeframe", None)
    params["timeframes"] = ["1h", "4h"]
    strat_cfg.params = params

    groups = [
        MLArtifactGroup(
            strategy_id="ai_regression",
            model_key="global|4h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1",
            example_count=1,
            live_model_count=0,
            checkpoint_count=0,
        ),
        MLArtifactGroup(
            strategy_id="ai_regression",
            model_key="global|1d|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1",
            example_count=1,
            live_model_count=0,
            checkpoint_count=0,
        ),
    ]

    candidates = find_stale_ml_artifact_groups(config, groups)

    assert [candidate.group.model_key for candidate in candidates] == [
        "global|1d|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1"
    ]
    assert candidates[0].stale_reason == "timeframe_mismatch"


def test_ml_prune_stale_uses_regression_backend_suffix() -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    strat_cfg = config.strategies.configs["ai_regression"]
    params = dict(strat_cfg.params or {})
    params["model_backend"] = "sgd_huber"
    strat_cfg.params = params

    groups = [
        MLArtifactGroup(
            strategy_id="ai_regression",
            model_key="global|1h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1",
            example_count=1,
            live_model_count=0,
            checkpoint_count=0,
        ),
        MLArtifactGroup(
            strategy_id="ai_regression",
            model_key=(
                "global|1h|features_ohlc_v5|"
                "sgd_huber_alpha0p0001_eta0p001_eps0p001_scalerstdv1"
            ),
            example_count=1,
            live_model_count=0,
            checkpoint_count=0,
        ),
    ]

    candidates = find_stale_ml_artifact_groups(config, groups)

    assert [candidate.group.model_key for candidate in candidates] == [
        "global|1h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1"
    ]
    assert candidates[0].stale_reason == "model_config_mismatch"


def test_ml_prune_stale_uses_feature_profile_suffix() -> None:
    config = load_config(config_path=Path("config_examples/config.yaml"), env="paper")
    strat_cfg = config.strategies.configs["ai_regression"]
    params = dict(strat_cfg.params or {})
    params["feature_profile"] = "drop_weakest"
    strat_cfg.params = params

    groups = [
        MLArtifactGroup(
            strategy_id="ai_regression",
            model_key="global|1h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1",
            example_count=1,
            live_model_count=0,
            checkpoint_count=0,
        ),
        MLArtifactGroup(
            strategy_id="ai_regression",
            model_key=(
                "global|1h|features_ohlc_v5_profile_drop_weakest|"
                "pa_reg_eps0p001_scalerstdv1"
            ),
            example_count=1,
            live_model_count=0,
            checkpoint_count=0,
        ),
    ]

    candidates = find_stale_ml_artifact_groups(config, groups)

    assert [candidate.group.model_key for candidate in candidates] == [
        "global|1h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1"
    ]
    assert candidates[0].stale_reason == "feature_schema_mismatch"


def test_ml_prune_stale_apply_deletes_stale_artifacts_only(
    tmp_path: Path, capsys: Any
) -> None:
    db_path = tmp_path / "ml-prune.db"
    _seed_ml_prune_artifacts(db_path)

    exit_code = cli.main(
        [
            "ml-prune-stale",
            "--config",
            "config_examples/config.yaml",
            "--db-path",
            str(db_path),
            "--apply",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Deleted stale ML artifact groups:" in output
    assert _ml_artifact_counts(db_path) == {
        "current_examples": 1,
        "stale_examples": 0,
        "stale_models": 0,
        "stale_checkpoints": 0,
    }


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

    def _fake_build_backtest_preflight(
        *args: Any, **kwargs: Any
    ) -> BacktestPreflightResult:  # noqa: ARG001
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


def _write_ml_compare_report(
    path: Path,
    *,
    version: int = 6,
    precision_long: float = 0.5,
    p95_lift: float = 1.0,
    positive_edge_count: int = 5,
    feature_schema: str = "ohlc_v3",
    backend: str = "pa",
    warning: str | None = None,
) -> None:
    generated_at = datetime(2026, 5, 24, tzinfo=UTC).isoformat()
    path.write_text(
        json.dumps(
            {
                "report_version": version,
                "generated_at": generated_at,
                "summary": {
                    "start": generated_at,
                    "end": generated_at,
                    "strategy_id": "ai_regression",
                    "timeframe": "4h",
                    "train_bars": 180,
                    "test_bars": 42,
                    "evaluation_mode": "rolling_window_isolated",
                    "edge_scoring_mode": "intent_hurdle_aligned",
                    "model_state_reused_across_folds": False,
                    "fold_count": 1,
                    "pairs": ["BTC/USD", "ETH/USD"],
                    "fee_bps": 10.0,
                    "slippage_bps": 20.0,
                    "round_trip_cost_bps": 60.0,
                    "coverage_status": "ready",
                    "warnings": [],
                    "metrics": {
                        "prediction_count": 20,
                        "positive_edge_prediction_count": positive_edge_count,
                        "edge_prediction_accuracy": 0.6,
                        "directional_accuracy": 0.55,
                        "precision_long": precision_long,
                    },
                    "confidence_buckets": [],
                    "regression_calibration": {
                        "prediction_count": 20,
                        "threshold_sweeps": [
                            {
                                "name": "evaluation_hurdle",
                                "realized_hit_rate": 0.2,
                                "avg_realized_return_selected": 0.001,
                            },
                            {
                                "name": "predicted_delta_p75",
                                "lift_over_base_rate": 1.1,
                                "avg_realized_return_selected": 0.002,
                            },
                            {
                                "name": "predicted_delta_p90",
                                "lift_over_base_rate": 1.2,
                                "avg_realized_return_selected": 0.003,
                            },
                            {
                                "name": "predicted_delta_p95",
                                "lift_over_base_rate": p95_lift,
                                "avg_realized_return_selected": 0.004,
                            },
                        ],
                        "predicted_delta_deciles": [],
                        "monotonicity": {"upper_half_improves": True},
                    },
                    "diagnostic_warnings": [warning] if warning else [],
                    "promotable": False,
                    "promotable_reasons": [],
                    "folds": [
                        {
                            "diagnostics": {
                                "models": [
                                    {
                                        "model_key": (
                                            "global|4h|features_"
                                            f"{feature_schema}|pa_reg"
                                        ),
                                        "feature_schema_version": feature_schema,
                                        "model_backend": backend,
                                        "framework": "sklearn_dummy",
                                    }
                                ],
                                "features": {"schema_version": feature_schema},
                            },
                            "regression_calibration": {
                                "threshold_sweeps": [],
                                "monotonicity": {"available": False},
                            },
                        }
                    ],
                },
                "provenance": {"generated_by": "krakked ml-walk-forward"},
            }
        ),
        encoding="utf-8",
    )


def test_ml_report_compare_accepts_v5_v6_and_markdown(
    tmp_path: Path, capsys: Any
) -> None:
    low = tmp_path / "low.json"
    high = tmp_path / "high.json"
    _write_ml_compare_report(low, version=5, precision_long=0.1)
    _write_ml_compare_report(high, version=6, precision_long=0.9)

    exit_code = cli.main(
        [
            "ml-report-compare",
            str(low),
            str(high),
            "--sort",
            "precision-long",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    rows = [
        line
        for line in captured.out.splitlines()
        if line.startswith("| ") and not line.startswith("| ---")
    ]
    assert "high" in rows[1]
    assert "low" in rows[2]
    assert "ohlc_v3" in captured.out
    assert captured.err == ""


def test_ml_report_compare_supports_glob_tsv_and_output(
    tmp_path: Path, capsys: Any
) -> None:
    _write_ml_compare_report(tmp_path / "one.json")
    _write_ml_compare_report(tmp_path / "two.json")
    output_path = tmp_path / "comparison.tsv"

    exit_code = cli.main(
        [
            "ml-report-compare",
            "--glob",
            str(tmp_path / "*.json"),
            "--format",
            "tsv",
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "written" in captured.out
    output = output_path.read_text(encoding="utf-8")
    assert output.startswith("name\tver\ttf")
    assert "one" in output
    assert "two" in output


@pytest.mark.parametrize(
    ("sort_by", "expected_first"),
    [
        ("name", "alpha"),
        ("precision-long", "zulu"),
        ("p95-lift", "middle"),
        ("positive-calls", "zulu"),
    ],
)
def test_ml_report_compare_sorts_json_output(
    tmp_path: Path,
    capsys: Any,
    sort_by: str,
    expected_first: str,
) -> None:
    _write_ml_compare_report(
        tmp_path / "alpha.json",
        precision_long=0.1,
        p95_lift=1.0,
        positive_edge_count=1,
    )
    _write_ml_compare_report(
        tmp_path / "middle.json",
        precision_long=0.2,
        p95_lift=2.0,
        positive_edge_count=2,
    )
    _write_ml_compare_report(
        tmp_path / "zulu.json",
        precision_long=0.9,
        p95_lift=0.5,
        positive_edge_count=9,
    )

    exit_code = cli.main(
        [
            "ml-report-compare",
            "--glob",
            str(tmp_path / "*.json"),
            "--format",
            "json",
            "--sort",
            sort_by,
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reports"][0]["name"] == expected_first


def test_ml_report_compare_skips_invalid_json_with_warning(
    tmp_path: Path, capsys: Any
) -> None:
    _write_ml_compare_report(tmp_path / "valid.json", warning="no positive calls")
    (tmp_path / "invalid.json").write_text("{not-json", encoding="utf-8")

    exit_code = cli.main(
        [
            "ml-report-compare",
            "--glob",
            str(tmp_path / "*.json"),
            "--format",
            "markdown",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "valid" in captured.out
    assert "no positive calls" in captured.out
    assert "Warning: Skipping non-JSON report" in captured.err


def test_ml_walk_forward_summary_prints_next_tier_blockers_for_risk_overlay(
    capsys: Any,
) -> None:
    # Construct a synthetic payload pinned to risk_overlay_candidate so the
    # CLI print path exercises the "Next tier blockers" labeling that the
    # operational-tier fix introduced. We do not rely on
    # _assess_promotability here because forcing risk_overlay via real
    # predictions is brittle; this test is specifically about the renderer.
    payload = {
        "summary": {
            "strategy_id": "ai_regression",
            "timeframe": "4h",
            "evaluation_mode": "rolling_window_isolated",
            "edge_scoring_mode": "intent_hurdle_aligned",
            "model_state_reused_across_folds": False,
            "fold_count": 1,
            "train_bars": 180,
            "test_bars": 42,
            "pairs": ["BTC/USD"],
            "round_trip_cost_bps": 60.0,
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-24T00:00:00+00:00",
            "metrics": {
                "prediction_count": 168,
                "directional_accuracy": 0.55,
                "edge_prediction_accuracy": 0.62,
                "precision_long": 0.30,
            },
            "promotion_tier": "risk_overlay_candidate",
            "promotion_tiers": {
                "research_promising": {
                    "tier": "research_promising",
                    "clears": True,
                    "reasons": [
                        "Walk-forward metrics clear the research promising"
                        " thresholds."
                    ],
                },
                "risk_overlay_candidate": {
                    "tier": "risk_overlay_candidate",
                    "clears": True,
                    "reasons": [
                        "Walk-forward metrics clear the risk overlay"
                        " candidate thresholds."
                    ],
                },
                "self_standing": {
                    "tier": "self_standing",
                    "clears": False,
                    "reasons": [
                        "Long precision is below 50% after estimated costs.",
                        "Per-fold strict checks failed: fold 2: non-monotonic",
                    ],
                },
            },
            "promotable": True,
            "promotable_reasons": [
                "Walk-forward metrics clear the risk overlay candidate" " thresholds."
            ],
        }
    }

    cli._print_ml_walk_forward_summary(payload, report_path=None)

    captured = capsys.readouterr().out
    assert "Promotion tier: risk_overlay_candidate (operational)" in captured
    # The pass message under the current tier — no failure bullets here.
    assert "clear the risk overlay candidate thresholds" in captured
    # The next-tier blockers must be labeled, not dumped as plain bullets.
    assert "Next tier blockers (self_standing):" in captured
    assert "Long precision is below 50%" in captured
    assert "Per-fold strict checks failed" in captured


def test_ml_walk_forward_summary_skips_next_tier_blockers_for_self_standing(
    capsys: Any,
) -> None:
    payload = {
        "summary": {
            "strategy_id": "ai_regression",
            "timeframe": "4h",
            "evaluation_mode": "rolling_window_isolated",
            "edge_scoring_mode": "intent_hurdle_aligned",
            "model_state_reused_across_folds": False,
            "fold_count": 1,
            "train_bars": 180,
            "test_bars": 42,
            "pairs": ["BTC/USD"],
            "round_trip_cost_bps": 60.0,
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-24T00:00:00+00:00",
            "metrics": {
                "prediction_count": 168,
                "directional_accuracy": 0.55,
                "edge_prediction_accuracy": 0.62,
                "precision_long": 0.60,
            },
            "promotion_tier": "self_standing",
            "promotion_tiers": {
                "research_promising": {
                    "tier": "research_promising",
                    "clears": True,
                    "reasons": ["clear"],
                },
                "risk_overlay_candidate": {
                    "tier": "risk_overlay_candidate",
                    "clears": True,
                    "reasons": ["clear"],
                },
                "self_standing": {
                    "tier": "self_standing",
                    "clears": True,
                    "reasons": ["clear"],
                },
            },
            "promotable": True,
            "promotable_reasons": [
                "Walk-forward metrics clear the self standing thresholds."
            ],
        }
    }

    cli._print_ml_walk_forward_summary(payload, report_path=None)

    captured = capsys.readouterr().out
    assert "Promotion tier: self_standing (operational)" in captured
    # There is no tier above self_standing, so no "Next tier blockers" header.
    assert "Next tier blockers" not in captured


def test_ml_report_compare_preserves_insufficient_data_monotonicity(
    tmp_path: Path, capsys: Any
) -> None:
    # A v7 report whose monotonicity is the new sentinel string must surface
    # that string in the compare table, not a blank cell.
    insufficient = tmp_path / "insufficient.json"
    payload = {
        "report_version": 7,
        "generated_at": datetime(2026, 5, 24, tzinfo=UTC).isoformat(),
        "summary": {
            "start": datetime(2026, 5, 24, tzinfo=UTC).isoformat(),
            "end": datetime(2026, 5, 24, tzinfo=UTC).isoformat(),
            "strategy_id": "ai_regression",
            "timeframe": "4h",
            "train_bars": 12,
            "test_bars": 6,
            "evaluation_mode": "rolling_window_isolated",
            "edge_scoring_mode": "intent_hurdle_aligned",
            "model_state_reused_across_folds": False,
            "fold_count": 1,
            "pairs": ["BTC/USD"],
            "fee_bps": 10.0,
            "slippage_bps": 20.0,
            "round_trip_cost_bps": 60.0,
            "coverage_status": "ready",
            "warnings": [],
            "metrics": {"prediction_count": 6, "positive_edge_prediction_count": 2},
            "confidence_buckets": [],
            "regression_calibration": {
                "threshold_sweeps": [],
                "monotonicity": {"upper_half_improves": "insufficient_data"},
            },
            "diagnostic_warnings": [],
            "promotion_tier": "blocked",
            "promotion_tiers": {
                "research_promising": {
                    "tier": "research_promising",
                    "clears": False,
                    "reasons": ["Fewer than 20 scored out-of-sample predictions."],
                }
            },
            "promotable": False,
            "promotable_reasons": ["Fewer than 20 scored out-of-sample predictions."],
            "folds": [
                {
                    "diagnostics": {"models": [], "features": {}},
                    "regression_calibration": {
                        "threshold_sweeps": [],
                        "monotonicity": {"upper_half_improves": "insufficient_data"},
                    },
                }
            ],
        },
        "provenance": {"generated_by": "krakked ml-walk-forward"},
    }
    insufficient.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = cli.main(
        ["ml-report-compare", str(insufficient), "--format", "markdown"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "insufficient_data" in captured.out


def _write_ml_ablation_report(
    path: Path,
    *,
    version: int = 6,
    feature_schema: str = "ohlc_v5",
    backend: str = "pa",
) -> None:
    generated_at = datetime(2026, 5, 24, tzinfo=UTC).isoformat()
    feature_rows = [
        ("keep_top", [0.6, 0.55], [0.9, 0.8], [0.5, 0.4]),
        ("risky_keep", [0.2, 0.22], [0.4, 0.42], [0.3, 0.35]),
        ("review_flip", [0.12, 0.11], [0.2, 0.18], [0.2, -0.2]),
        ("drop_low", [0.01, 0.01], [0.02, 0.02], [0.01, 0.01]),
    ]

    def _fold(index: int) -> dict[str, Any]:
        contributions = [
            {
                "feature": name,
                "coefficient": coefficients[index - 1],
                "avg_abs_row_contribution": avg_values[index - 1],
                "p95_abs_row_contribution": p95_values[index - 1],
            }
            for name, avg_values, p95_values, coefficients in feature_rows
        ]
        return {
            "fold_index": index,
            "diagnostics": {
                "models": [
                    {
                        "model_key": f"global|4h|features_{feature_schema}|pa_reg",
                        "feature_schema_version": feature_schema,
                        "model_backend": backend,
                        "framework": "sklearn_dummy",
                    }
                ],
                "features": {
                    "schema_version": feature_schema,
                    "health_warnings": [
                        "Scaled feature risky_keep has tail values above 3.0."
                    ],
                    "clipping": {
                        "features": {
                            "risky_keep": {"clipped_rate": 0.06 if index == 2 else 0.01}
                        }
                    },
                    "linear_contributions": contributions,
                },
            },
            "regression_calibration": {
                "threshold_sweeps": [],
                "monotonicity": {"available": False},
            },
        }

    path.write_text(
        json.dumps(
            {
                "report_version": version,
                "generated_at": generated_at,
                "summary": {
                    "start": generated_at,
                    "end": generated_at,
                    "strategy_id": "ai_regression",
                    "timeframe": "4h",
                    "folds": [_fold(1), _fold(2)],
                },
                "provenance": {"generated_by": "krakked ml-walk-forward"},
            }
        ),
        encoding="utf-8",
    )


def test_ml_feature_ablation_summary_outputs_markdown_candidates(
    tmp_path: Path, capsys: Any
) -> None:
    report_path = tmp_path / "ablation.json"
    _write_ml_ablation_report(report_path)

    exit_code = cli.main(["ml-feature-ablation-summary", str(report_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "| report | tf | features | backend | feature |" in captured.out
    assert "drop_low" in captured.out
    assert "drop_candidate" in captured.out
    assert "risky_keep" in captured.out
    assert "keep_but_health_risk" in captured.out
    assert captured.err == ""


def test_ml_feature_ablation_summary_json_contains_metrics_and_recommendations(
    tmp_path: Path, capsys: Any
) -> None:
    report_path = tmp_path / "ablation.json"
    _write_ml_ablation_report(report_path)

    exit_code = cli.main(
        [
            "ml-feature-ablation-summary",
            str(report_path),
            "--format",
            "json",
            "--sort",
            "name",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    rows = {row["feature"]: row for row in payload["features"]}
    assert rows["keep_top"]["recommendation"] == "keep_candidate"
    assert rows["risky_keep"]["recommendation"] == "keep_but_health_risk"
    assert rows["risky_keep"]["health_warning_count"] == 2
    assert rows["risky_keep"]["max_clipped_rate"] == pytest.approx(0.06)
    assert rows["risky_keep"]["clipped_rate_gate_failed"] is True
    assert rows["review_flip"]["recommendation"] == "review_candidate"
    assert rows["review_flip"]["sign_stable"] is False
    assert rows["review_flip"]["coefficient_positive_count"] == 1
    assert rows["review_flip"]["coefficient_negative_count"] == 1
    assert rows["drop_low"]["recommendation"] == "drop_candidate"
    assert rows["drop_low"]["contribution_share"] < 0.05


def test_ml_feature_ablation_summary_supports_glob_tsv_and_output(
    tmp_path: Path, capsys: Any
) -> None:
    _write_ml_ablation_report(tmp_path / "one.json")
    _write_ml_ablation_report(tmp_path / "two.json")
    output_path = tmp_path / "ablation.tsv"

    exit_code = cli.main(
        [
            "ml-feature-ablation-summary",
            "--glob",
            str(tmp_path / "*.json"),
            "--format",
            "tsv",
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "written" in captured.out
    output = output_path.read_text(encoding="utf-8")
    assert output.startswith("report\ttf\tfeatures")
    assert "drop_low" in output
    assert output.count("drop_candidate") == 2


@pytest.mark.parametrize(
    ("sort_by", "expected_first"),
    [
        ("drop-score", "drop_low"),
        ("contribution", "keep_top"),
        ("rank", "keep_top"),
        ("health", "risky_keep"),
        ("name", "drop_low"),
    ],
)
def test_ml_feature_ablation_summary_sort_modes(
    tmp_path: Path,
    capsys: Any,
    sort_by: str,
    expected_first: str,
) -> None:
    report_path = tmp_path / "ablation.json"
    _write_ml_ablation_report(report_path)

    exit_code = cli.main(
        [
            "ml-feature-ablation-summary",
            str(report_path),
            "--format",
            "json",
            "--sort",
            sort_by,
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["features"][0]["feature"] == expected_first


def test_ml_feature_ablation_summary_skips_invalid_and_no_contribution_reports(
    tmp_path: Path, capsys: Any
) -> None:
    valid = tmp_path / "valid.json"
    no_contrib = tmp_path / "no-contrib.json"
    invalid = tmp_path / "invalid.json"
    _write_ml_ablation_report(valid)
    _write_ml_compare_report(no_contrib)
    invalid.write_text("{not-json", encoding="utf-8")

    exit_code = cli.main(
        [
            "ml-feature-ablation-summary",
            "--glob",
            str(tmp_path / "*.json"),
            "--format",
            "markdown",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "valid" in captured.out
    assert "drop_low" in captured.out
    assert "Warning: Skipping non-JSON report" in captured.err
    assert "Warning: Skipping ML report without feature contributions" in captured.err


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
                    "clamped_actions": 0,
                    "execution_errors": 0,
                    "replay_inputs": {},
                    "per_strategy": {"majors_mean_rev": {"realized_pnl_usd": 25.0}},
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
                    "clamped_actions": 2,
                    "execution_errors": 0,
                    "replay_inputs": {},
                    "per_strategy": {"majors_mean_rev": {"realized_pnl_usd": 55.0}},
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
    assert "Clamped actions: 0.00 -> 2.00 (+2.00)" in output
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
