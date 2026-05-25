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
    assert (
        "Replay trust: Limited signal: some strategy actions were blocked by guardrails."
        in output
    )
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


def test_ml_walk_forward_subcommand_writes_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 4, 2, tzinfo=UTC)

    class _FakeWalkForwardResult:
        def to_report_dict(self) -> dict[str, Any]:
            return {
                "report_version": 7,
                "generated_at": start.isoformat(),
                "summary": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "strategy_id": "ai_regression",
                    "timeframe": "1h",
                    "train_bars": 12,
                    "test_bars": 6,
                    "evaluation_mode": "rolling_window_isolated",
                    "edge_scoring_mode": "intent_hurdle_aligned",
                    "model_state_reused_across_folds": False,
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
        captured["args"][0].strategies.configs["ai_regression"].params[
            "feature_profile"
        ]
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
                            "risky_keep": {
                                "clipped_rate": 0.06 if index == 2 else 0.01
                            }
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
